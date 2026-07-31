"""Retrieval diagnostics for the admin Wiki/knowledge console.

This service is intentionally read-only: it reuses the existing RAG and Wiki
retrieval paths, then returns enough trace data for an administrator to
understand routing, filtering, rerank, and final-context selection.
"""
from __future__ import annotations

from typing import Any, Optional

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.models.common.context import UserContext
from app.models.common.execution import DocumentChunk
from app.models.knowledge.graph import File
from app.models.wiki.wiki_link import WikiLink
from app.models.wiki.wiki_page import WikiPage
from app.models.wiki.wiki_page_source import WikiPageSource
from app.services.doc_scope import normalize_doc_scope
from app.skills.doc_skill import get_doc_skill
from app.supervisor.nodes.knowledge_router import _classify_by_rules
from sqlalchemy import func, or_, select


def _preview(text: str, max_chars: int = 220) -> str:
    cleaned = " ".join(str(text or "").split())
    return cleaned[:max_chars]


def _diagnostic_content(text: str, max_chars: int = 4000) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 20].rstrip() + "\n...[truncated]"


def _wiki_query_terms(query: str) -> list[str]:
    cleaned = " ".join(str(query or "").split())
    terms = [cleaned]
    for noise in ("介绍一下", "介绍下", "介绍", "什么是", "是什么", "如何理解", "讲讲", "说明"):
        if noise in cleaned:
            terms.append(cleaned.replace(noise, "").strip())
    terms.extend([token.strip() for token in cleaned.replace("，", " ").replace(",", " ").split()])
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        result.append(term[:80])
    return result[:6]


def _primary_page(chunk: DocumentChunk) -> Optional[int]:
    if isinstance(chunk.page_number, int) and chunk.page_number > 0:
        return chunk.page_number
    raw = (chunk.metadata or {}).get("page_numbers")
    values = raw if isinstance(raw, list) else [raw]
    for value in values:
        for token in str(value or "").replace(",", " ").split():
            if token.isdigit() and int(token) > 0:
                return int(token)
    return None


def _hit_from_chunk(chunk: DocumentChunk, *, source: str, kept: bool, reason: str = "") -> dict[str, Any]:
    meta = chunk.metadata or {}
    title = meta.get("title") or meta.get("header_path") or chunk.header_path
    file_name = meta.get("source_file") or chunk.source_file
    return {
        "source": source,
        "chunk_id": chunk.chunk_id,
        "title": str(title) if title else None,
        "file_id": str(meta.get("file_id") or "") or None,
        "file_name": str(file_name) if file_name else None,
        "page_number": _primary_page(chunk),
        "score": float(chunk.score or 0.0),
        "rerank_score": (
            float(chunk.rerank_score) if chunk.rerank_score is not None else None
        ),
        "status": str(meta.get("status") or "") or None,
        "domain": str(meta.get("domain") or "") or None,
        "kept": kept,
        "reason": reason or None,
        "preview": _preview(chunk.content),
        "content": _diagnostic_content(chunk.content),
    }


def _hit_from_wiki_page(
    page: WikiPage,
    *,
    source: str,
    kept: bool,
    reason: str,
    source_count: int = 0,
    conflict_count: int = 0,
) -> dict[str, Any]:
    body = page.markdown_body or page.summary or ""
    return {
        "source": source,
        "chunk_id": f"wiki_page:{page.id}",
        "title": page.title,
        "file_id": None,
        "file_name": None,
        "page_number": None,
        "score": 1.0,
        "rerank_score": None,
        "status": page.status,
        "domain": page.domain,
        "kept": kept,
        "reason": reason,
        "preview": _preview(body),
        "content": _diagnostic_content(body),
        "meta": {
            "slug": page.slug,
            "source_count": source_count,
            "conflict_count": conflict_count,
        },
    }


async def _find_unpublished_wiki_candidates(
    *,
    workspace_id: str,
    query: str,
    domains: list[str],
    wiki_slugs: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    q = query.strip()
    if not workspace_id or not q:
        return []
    terms = _wiki_query_terms(q)
    if not terms:
        return []
    like_filters = []
    for term in terms:
        like = f"%{term}%"
        like_filters.append(WikiPage.title.like(like))
        like_filters.append(WikiPage.slug.like(like))
        like_filters.append(WikiPage.summary.like(like))
        like_filters.append(WikiPage.markdown_body.like(like))
    async with get_async_db_manager().session_scope() as session:
        stmt = (
            select(WikiPage)
            .where(
                WikiPage.workspace_id == workspace_id,
                WikiPage.status.in_(["candidate", "draft"]),
                or_(*like_filters),
            )
            .order_by(WikiPage.updated_at.desc())
            .limit(max(1, min(limit, 10)))
        )
        if domains:
            stmt = stmt.where(WikiPage.domain.in_(domains))
        if wiki_slugs:
            stmt = stmt.where(WikiPage.slug.in_(wiki_slugs))
        pages = (await session.execute(stmt)).scalars().all()
        hits: list[dict[str, Any]] = []
        for page in pages:
            source_count = (
                await session.execute(
                    select(func.count(WikiPageSource.id)).where(
                        WikiPageSource.workspace_id == workspace_id,
                        WikiPageSource.page_id == page.id,
                    )
                )
            ).scalar_one()
            conflict_count = (
                await session.execute(
                    select(func.count(WikiLink.id)).where(
                        WikiLink.workspace_id == workspace_id,
                        WikiLink.status == "active",
                        WikiLink.link_type == "contradicts",
                        or_(
                            WikiLink.source_page_id == page.id,
                            WikiLink.target_page_id == page.id,
                        ),
                    )
                )
            ).scalar_one()
            expired_source_count = (
                await session.execute(
                    select(func.count(WikiPageSource.id))
                    .join(File, File.id == WikiPageSource.file_id)
                    .where(
                        WikiPageSource.workspace_id == workspace_id,
                        WikiPageSource.page_id == page.id,
                        File.is_deleted.is_(False),
                        File.effective_until.is_not(None),
                        File.effective_until < func.now(),
                    )
                )
            ).scalar_one()
            hits.append(
                {
                    **_hit_from_wiki_page(
                    page,
                    source="wiki_candidate",
                    kept=False,
                    reason="candidate_exists_but_unpublished",
                    source_count=int(source_count or 0),
                    conflict_count=int(conflict_count or 0),
                    ),
                    "meta": {
                        "slug": page.slug,
                        "source_count": int(source_count or 0),
                        "conflict_count": int(conflict_count or 0),
                        "expired_source_count": int(expired_source_count or 0),
                    },
                }
            )
        return hits


def _infer_path(query: str, doc_scope: dict[str, Any] | None) -> tuple[str, str, str]:
    if doc_scope and (doc_scope.get("domains") or doc_scope.get("wiki_slugs")):
        return "wiki", "scope", "explicit_wiki_scope"

    settings = get_settings()
    if not settings.wiki.first_enabled:
        return "rag", "disabled", "route_disabled"
    rule_path, rule_reason = _classify_by_rules(
        query,
        rag_keywords=settings.wiki.router_keywords_rag_list,
        wiki_keywords=settings.wiki.router_keywords_wiki_list,
    )
    if rule_path:
        return rule_path, "rule", rule_reason
    return "rag", "fallback", "fallback_to_rag_evidence"


class RetrievalDiagnosticsService:
    async def diagnose(
        self,
        *,
        query: str,
        user_context: UserContext,
        knowledge_path: Optional[str] = None,
        doc_scope: Optional[dict[str, Any]] = None,
        top_k: int = 8,
        deep_search: bool = False,
    ) -> dict[str, Any]:
        settings = get_settings()
        skill = get_doc_skill()
        normalized_scope = normalize_doc_scope(doc_scope, strict=False, drop_empty=True)
        inferred_path, inferred_source, inferred_reason = _infer_path(query, normalized_scope)
        path, source, reason = inferred_path, inferred_source, inferred_reason
        if knowledge_path in {"rag", "wiki", "both"}:
            path = str(knowledge_path)
            source = "request"
            reason = "explicit_request_path"

        file_ids = list((normalized_scope or {}).get("file_ids") or [])
        domains = list((normalized_scope or {}).get("domains") or [])
        wiki_slugs = list((normalized_scope or {}).get("wiki_slugs") or [])
        visibilities = list((normalized_scope or {}).get("visibilities") or [])
        dept_ids = list((normalized_scope or {}).get("dept_ids") or [])
        business_domain = (normalized_scope or {}).get("business_domain")
        document_type = (normalized_scope or {}).get("document_type")
        confidentiality_level = (normalized_scope or {}).get("confidentiality_level")

        issues: list[str] = []
        hits: list[dict[str, Any]] = []
        final_context: list[dict[str, Any]] = []
        counts = {
            "rag_candidates": 0,
            "rag_reranked": 0,
            "rag_filtered_low_score": 0,
            "wiki_hits": 0,
            "wiki_candidate_hits": 0,
            "final_context": 0,
            "permission_filtered": 0,
            "metadata_filtered": 0,
        }

        if knowledge_path in {"rag", "wiki", "both"} and knowledge_path != inferred_path:
            issues.append("route_mismatch")
        if inferred_source == "disabled":
            issues.append("route_disabled")

        if path in {"wiki", "both"}:
            wiki_chunks = await skill.wiki_navigate(
                query=query,
                user_context=user_context,
                max_pages=max(1, min(top_k, int(settings.wiki.index_load_top_k or top_k))),
                domains=domains,
                wiki_slugs=wiki_slugs,
                file_ids=file_ids or None,
                visibilities=visibilities or None,
                dept_ids=dept_ids or None,
                business_domain=business_domain,
                document_type=document_type,
                confidentiality_level=confidentiality_level,
            )
            counts["wiki_hits"] = len(wiki_chunks)
            if not wiki_chunks:
                issues.append("wiki_missing")
                candidate_hits = await _find_unpublished_wiki_candidates(
                    workspace_id=user_context.workspace_id,
                    query=query,
                    domains=domains,
                    wiki_slugs=wiki_slugs,
                    limit=top_k,
                )
                counts["wiki_candidate_hits"] = len(candidate_hits)
                if candidate_hits:
                    issues.append("candidate_exists_but_unpublished")
                    if any((hit.get("meta") or {}).get("conflict_count") for hit in candidate_hits):
                        issues.append("wiki_conflict")
                    if any((hit.get("meta") or {}).get("expired_source_count") for hit in candidate_hits):
                        issues.append("source_expired")
                    hits.extend(candidate_hits)
            for chunk in wiki_chunks:
                hit = _hit_from_chunk(chunk, source="wiki", kept=True, reason="trusted_wiki_context")
                hits.append(hit)
                final_context.append(hit)
                if not (chunk.metadata or {}).get("source_files"):
                    issues.append("answer_generation_risk")
                if (chunk.metadata or {}).get("conflict_count"):
                    issues.append("wiki_conflict")
                if (chunk.metadata or {}).get("source_expired"):
                    issues.append("source_expired")

        if path in {"rag", "both"}:
            queries = await skill.query_rewriter.rewrite(query, original_query=query, num_variants=3)
            mode = "enhanced" if deep_search else "fast"
            from app.core.rag.retriever_factory import get_retriever

            retriever = skill._retriever or get_retriever(mode, skill.client)
            candidates = await retriever.search(
                queries=queries,
                user_context=user_context,
                top_n=int(settings.rag.merged_top_n),
                include_images=False,
                file_ids=file_ids or None,
                visibilities=visibilities or None,
                dept_ids=dept_ids or None,
            )
            counts["rag_candidates"] = len(candidates)
            if not candidates:
                issues.append("rag_no_candidates")
            reranked = candidates[:top_k] if deep_search else await skill.reranker.rerank(query, candidates, top_k)
            counts["rag_reranked"] = len(reranked)
            threshold = 0.0 if deep_search else float(settings.rag.rerank_score_threshold)
            kept_ids: set[str] = set()
            for chunk in reranked:
                score = float(chunk.rerank_score or 0.0)
                kept = deep_search or score >= threshold
                if kept:
                    kept_ids.add(chunk.chunk_id)
                else:
                    counts["rag_filtered_low_score"] += 1
                hits.append(
                    _hit_from_chunk(
                        chunk,
                        source="rag",
                        kept=kept,
                        reason="kept_for_context" if kept else "filtered_by_rerank_threshold",
                    )
                )
            for chunk in reranked:
                if chunk.chunk_id in kept_ids:
                    final_context.append(_hit_from_chunk(chunk, source="rag", kept=True, reason="final_context"))
            if candidates and reranked and counts["rag_filtered_low_score"] >= len(reranked):
                issues.append("rerank_dropped")

        counts["final_context"] = len(final_context)
        if not final_context:
            issues.append("no_final_context")
            if normalized_scope:
                issues.append("metadata_scope_mismatch")
                counts["metadata_filtered"] = 1
            if user_context.data_scope and int(user_context.data_scope or 0) != 1:
                issues.append("permission_filtered")
                counts["permission_filtered"] = 1

        # Preserve order while de-duplicating issue codes.
        issues = list(dict.fromkeys(issues))

        return {
            "query": query,
            "knowledge_path": path,
            "knowledge_path_source": source,
            "knowledge_path_reason": reason,
            "permissions": {
                "workspace_id": user_context.workspace_id,
                "dept_id": user_context.dept_id,
                "data_scope": user_context.data_scope,
                "permission_filter_applied": True,
                "permission_model": "workspace + visibility + owner + department data_scope",
                "permission_filter_summary": "Diagnostics are executed with the same workspace/user scope as retrieval; filtered content is counted only as a filter event, not exposed.",
                "doc_scope_applied": bool(normalized_scope),
                "file_ids_limited": len(file_ids),
                "visibilities_limited": len(visibilities),
                "dept_ids_limited": len(dept_ids),
                "wiki_domains_limited": len(domains),
                "wiki_slugs_limited": len(wiki_slugs),
            },
            "metadata_filter_summary": {
                "doc_scope_applied": bool(normalized_scope),
                "file_ids_limited": len(file_ids),
                "visibilities_limited": len(visibilities),
                "dept_ids_limited": len(dept_ids),
                "wiki_domains_limited": len(domains),
                "wiki_slugs_limited": len(wiki_slugs),
                "scope": (normalized_scope or {}).get("scope"),
                "department_id": (normalized_scope or {}).get("department_id"),
                "business_domain": (normalized_scope or {}).get("business_domain"),
                "document_type": (normalized_scope or {}).get("document_type"),
                "confidentiality_level": (normalized_scope or {}).get("confidentiality_level"),
                "metadata_scope_mismatch": "metadata_scope_mismatch" in issues,
                "scope_keys": sorted(list((normalized_scope or {}).keys())),
            },
            "route_trace": {
                "inferred_path": inferred_path,
                "inferred_source": inferred_source,
                "inferred_reason": inferred_reason,
                "requested_path": knowledge_path,
                "final_path": path,
                "route_mismatch": "route_mismatch" in issues,
            },
            "counts": counts,
            "hits": hits,
            "final_context": final_context,
            "issues": issues,
        }


_singleton: Optional[RetrievalDiagnosticsService] = None


def get_retrieval_diagnostics_service() -> RetrievalDiagnosticsService:
    global _singleton
    if _singleton is None:
        _singleton = RetrievalDiagnosticsService()
    return _singleton
