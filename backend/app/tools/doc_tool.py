"""
文档检索工具 - 将 DocSkill 封装为 LangChain StructuredTool

当前策略：
1. 单次检索（hybrid + rerank）
2. 过滤导航块与低分切片
3. 以高置信截断和固定上限收口最终文本证据
4. 独立选择 PDF 图片证据，并按固定顺序裁剪图片页
5. 仅在 0 证据时执行一次修复检索
"""
import asyncio
import logging
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from langchain_core.messages import BaseMessage
from langchain_core.tools import StructuredTool
from pydantic import Field

from app.config import get_settings
from app.core.storage.service import get_storage_service
from app.core.utils.storage_path import resolve_storage_path
from app.tools.doc_support import selection as doc_selection
from app.tools.doc_support.selection import (
    chunk_primary_page_number as _chunk_primary_page_number,
    parse_page_numbers as _parse_page_numbers,
)
from app.tools.base import BaseToolInput, WorkerResult

logger = logging.getLogger(__name__)


def _build_doc_anchor_hint(content: str, max_chars: int = 40) -> Optional[str]:
    """
    为 Word 引用构建文本锚点，供前端精确滚动定位（页码失配时兜底）。
    """
    if not content:
        return None

    cleaned = re.sub(r"\[\[(?:IMG|IMAGE):[^\]]+\]\]", " ", str(content))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    # 优先选择前几句里更“信息密度高”的句子，降低锚点过于泛化导致的误定位
    sentences = [s.strip() for s in re.split(r"[。！？.!?；;\n]+", cleaned) if s.strip()]
    candidates = [s for s in sentences[:3] if len(s) >= 8]
    if candidates:
        anchor = max(candidates, key=len)
    else:
        anchor = sentences[0] if sentences else cleaned

    anchor = anchor[:max_chars].strip()
    if len(anchor) < 6:
        return None
    return anchor


def _build_citation_file_slot(
    file_id: str,
    file_type: str,
    page_number: Optional[int],
    anchor_hint: Optional[str] = None,
) -> str:
    """
    构建 citation 的 file_id 槽位（兼容式 V2）：
    - 旧版: file_id
    - 新版: file_id?page=12
    """
    clean_file_id = str(file_id or "").strip().split("?", 1)[0]
    if not clean_file_id:
        return ""

    settings = get_settings()
    if not bool(getattr(settings.rag, "citation_enable_page_link", True)):
        return clean_file_id

    normalized_type = str(file_type or "").lower().strip()
    if normalized_type not in {"pdf", "docx", "doc"}:
        return clean_file_id

    page_param_key = str(getattr(settings.rag, "citation_page_param_key", "page") or "page").strip() or "page"
    try:
        default_page = max(1, int(getattr(settings.rag, "citation_default_page", 1) or 1))
    except (TypeError, ValueError):
        default_page = 1
    has_explicit_page = isinstance(page_number, int) and page_number > 0
    safe_anchor = str(anchor_hint or "").strip()

    # DOC/DOCX：优先锚点定位（避免后端页码与前端渲染页不一致导致误跳页）
    if normalized_type in {"docx", "doc"}:
        if safe_anchor:
            return f"{clean_file_id}?{urlencode({'anchor': safe_anchor})}"
        if not has_explicit_page:
            return clean_file_id

        # 仅在没有锚点时，才使用页码作为兜底
        final_page = page_number if has_explicit_page else default_page
        return f"{clean_file_id}?{urlencode({page_param_key: final_page})}"

    final_page = page_number if has_explicit_page else default_page
    params: Dict[str, Any] = {page_param_key: final_page}
    return f"{clean_file_id}?{urlencode(params)}"


def _build_single_repair_query(query: str, original_query: Optional[str]) -> str:
    base = (query or "").strip()
    origin = (original_query or "").strip()
    merged = f"{origin} {base}".strip() if origin and origin not in base else base

    # 轻量同义词扩展（非 LLM，来自配置）
    settings = get_settings()
    synonym_pairs = settings.rag.repair_synonym_pairs or {}
    extra_terms = []
    for k, v in synonym_pairs.items():
        if k in merged and v not in merged:
            extra_terms.append(v)

    for term in re.findall(r"[A-Za-z][A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+", merged):
        spaced = re.sub(r"[-_]+", " ", term).strip()
        compact = re.sub(r"[-_]+", "", term).strip()
        if spaced and spaced not in merged:
            extra_terms.append(spaced)
        if compact and compact not in merged:
            extra_terms.append(compact)

    tokens = [t for t in re.split(r"[\s,，。；;]+", merged) if t]
    dedup_tokens = list(dict.fromkeys(tokens + extra_terms))
    return " ".join(dedup_tokens)


def _relaxed_effective_chunks(chunks: list, *, max_chunks: int = 1) -> list:
    candidates = []
    for chunk in chunks or []:
        metadata = getattr(chunk, "metadata", None) or {}
        chunk_type = str(metadata.get("type") or "").lower()
        if chunk_type in {"pageindex_navigation", "navigation"}:
            continue
        if not str(getattr(chunk, "content", "") or "").strip():
            continue
        candidates.append(chunk)
    return sorted(candidates, key=doc_selection.chunk_score, reverse=True)[:max_chunks]


def _chunk_source_type(chunk) -> str:
    metadata = getattr(chunk, "metadata", None) or {}
    return "wiki" if metadata.get("kind") == "wiki_page" else "rag"


def _chunk_preview(content: str, max_chars: int = 220) -> str:
    cleaned = re.sub(r"\[\[(?:IMG|IMAGE|CITATION):[^\]]+\]\]", " ", str(content or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _wiki_source_file_count(chunk) -> int:
    metadata = getattr(chunk, "metadata", None) or {}
    source_files = metadata.get("source_files") or []
    return len(source_files) if isinstance(source_files, list) else 0


def _build_context_record(chunk, *, file_info_map: dict[str, dict[str, Any]], used: bool, reason: str = "") -> dict[str, Any]:
    metadata = getattr(chunk, "metadata", None) or {}
    source_type = _chunk_source_type(chunk)
    raw_file_id = str(metadata.get("file_id") or "").strip()
    file_info = file_info_map.get(raw_file_id) if raw_file_id else None
    file_name = None
    if file_info:
        base_name = str(file_info.get("name") or "").strip()
        file_type = str(file_info.get("type") or "").strip()
        file_name = f"{base_name}.{file_type}" if file_type and base_name and not base_name.lower().endswith(f".{file_type.lower()}") else base_name
    title = str(metadata.get("title") or getattr(chunk, "header_path", None) or getattr(chunk, "source_file", "") or "").strip()
    return {
        "source": source_type,
        "chunk_id": str(getattr(chunk, "chunk_id", "") or metadata.get("chunk_id") or ""),
        "title": title or None,
        "slug": str(metadata.get("slug") or "").strip() or None,
        "file_id": raw_file_id or None,
        "file_name": file_name or getattr(chunk, "source_file", None),
        "page_number": _chunk_primary_page_number(chunk),
        "score": float(getattr(chunk, "score", 0.0) or 0.0),
        "rerank_score": getattr(chunk, "rerank_score", None),
        "used": bool(used),
        "reason": reason,
        "has_source_files": _wiki_source_file_count(chunk) > 0 if source_type == "wiki" else None,
        "preview": _chunk_preview(getattr(chunk, "content", "")),
    }


def _ensure_both_context(selected_chunks: list, effective_chunks: list, max_chunks: int) -> list:
    """Ensure Both path keeps at least one Wiki and one RAG chunk when both exist."""
    if not selected_chunks:
        return selected_chunks
    selected_ids = {str(getattr(c, "chunk_id", "") or "") for c in selected_chunks}
    has_wiki = any(_chunk_source_type(c) == "wiki" for c in selected_chunks)
    has_rag = any(_chunk_source_type(c) == "rag" for c in selected_chunks)
    available_wiki = [c for c in effective_chunks if _chunk_source_type(c) == "wiki"]
    available_rag = [c for c in effective_chunks if _chunk_source_type(c) == "rag"]
    additions = []
    if available_wiki and not has_wiki:
        additions.append(available_wiki[0])
    if available_rag and not has_rag:
        additions.append(available_rag[0])
    if not additions:
        return selected_chunks
    limit = max(2, max_chunks)
    merged = list(selected_chunks)
    for chunk in additions:
        cid = str(getattr(chunk, "chunk_id", "") or "")
        if cid and cid in selected_ids:
            continue
        if len(merged) >= limit:
            merged.pop()
        merged.append(chunk)
        if cid:
            selected_ids.add(cid)
    return merged[:limit]


async def _expand_folder_file_ids(
    session,
    folder_ids: List[str],
    include_subfolders: bool,
    user_context,
    visibilities: Optional[List[str]] = None,
    dept_ids: Optional[List[str]] = None,
    workspace_id: Optional[str] = None,
) -> List[str]:
    """Expand folder ids to file ids (optional recursive)."""
    if not folder_ids:
        return []

    from app.services.filesystem_service import FilesystemService

    filesystem = FilesystemService(session, workspace_id=workspace_id)
    return await filesystem.expand_folder_file_ids(
        folder_ids=folder_ids,
        include_subfolders=include_subfolders,
        user_context=user_context,
        visibilities=visibilities or None,
        dept_ids=dept_ids or None,
    )


class DocToolInput(BaseToolInput):
    """文档工具输入参数"""

    original_query: Optional[str] = Field(default=None, description="用户原始问题")
    deep_search: Optional[bool] = Field(default=None, description="是否启用深度检索（None 时从 user_context 回退读取）")
    doc_scope: Optional[Dict[str, Any]] = Field(
        default=None,
        description="文档检索范围: file_ids/folder_ids/include_subfolders/top_k/include_images",
    )
    # [M3.3] KnowledgeRouter 注入的检索路径：rag(默认) | wiki | both
    knowledge_path: Optional[str] = Field(
        default=None,
        description="知识检索路径：rag(切片检索) | wiki(实体页装载) | both(并行合并，Wiki 优先)",
    )


async def run_doc_task(
    query: str,
    user_context: Dict[str, Any],
    original_query: Optional[str] = None,
    deep_search: Optional[bool] = None,
    doc_scope: Optional[Dict[str, Any]] = None,
    session_id: str = "",
    parent_step_id: str = "",
    messages: Optional[List[BaseMessage]] = None,
    round_index: int = 0,
    knowledge_path: Optional[str] = None,
    **kwargs,
) -> WorkerResult:
    import asyncio

    from sqlalchemy import select

    from app.core.db.database import get_async_db_manager
    from app.models.common.context import UserContext
    from app.models.knowledge.graph import File
    from app.skills.doc_skill import DocSkill

    started = time.perf_counter()
    settings = get_settings()

    # [M3.3] 解析检索路径（默认 rag，开关关闭时与 M2 行为字节级一致）
    norm_path = (knowledge_path or "rag").strip().lower()
    if norm_path not in ("rag", "wiki", "both"):
        norm_path = "rag"

    logger.info(
        "doc_tool: 执行检索, round=%s, deep_search=%s, knowledge_path=%s, query='%s...', original='%s...'",
        round_index,
        deep_search,
        norm_path,
        query[:50],
        original_query[:50] if original_query else "N/A",
    )
    safe_messages = messages or []

    search_query = query

    raw_user_context = user_context if isinstance(user_context, dict) else {}
    ctx = UserContext(**user_context) if isinstance(user_context, dict) else user_context
    raw_mcp_top_k = raw_user_context.get("mcp_ask_evidence_top_k")
    mcp_ask_evidence_top_k: Optional[int] = None
    if raw_mcp_top_k is not None:
        try:
            parsed_mcp_top_k = int(raw_mcp_top_k)
        except (TypeError, ValueError):
            parsed_mcp_top_k = 20
        mcp_ask_evidence_top_k = (
            20 if parsed_mcp_top_k < 1 else min(parsed_mcp_top_k, 20)
        )

    raw_mcp_token_budget = raw_user_context.get("mcp_ask_evidence_token_budget")
    mcp_ask_evidence_token_budget: Optional[int] = None
    if raw_mcp_token_budget is not None:
        try:
            parsed_mcp_token_budget = int(raw_mcp_token_budget)
        except (TypeError, ValueError):
            parsed_mcp_token_budget = 9000
        mcp_ask_evidence_token_budget = min(
            max(parsed_mcp_token_budget, 2000),
            16000,
        )

    raw_required_file_ids = raw_user_context.get("mcp_ask_required_file_ids") or []
    if isinstance(raw_required_file_ids, str):
        raw_required_file_ids = [raw_required_file_ids]
    if not isinstance(raw_required_file_ids, (list, tuple, set)):
        raw_required_file_ids = []
    required_file_id_cap = mcp_ask_evidence_top_k or 20
    normalized_required_file_ids = list(
        dict.fromkeys(
            str(file_id).strip()
            for file_id in raw_required_file_ids
            if str(file_id).strip()
        )
    )[:required_file_id_cap]
    mcp_ask_required_file_ids = set(normalized_required_file_ids)
    mcp_ask_preserve_related_candidates = bool(
        raw_user_context.get("mcp_ask_preserve_related_candidates")
        and mcp_ask_required_file_ids
    )

    scope = doc_scope or kwargs.get("doc_scope") or {}
    file_ids = scope.get("file_ids") or []
    folder_ids = scope.get("folder_ids") or []
    include_subfolders = bool(scope.get("include_subfolders", False))
    top_k = scope.get("top_k", 5)
    include_images = scope.get("include_images", True)
    visibilities = scope.get("visibilities") or []
    dept_ids = scope.get("dept_ids") or []
    if not dept_ids and scope.get("department_id") is not None:
        dept_ids = [scope.get("department_id")]
    business_domain = scope.get("business_domain")
    document_type = scope.get("document_type")
    confidentiality_level = scope.get("confidentiality_level")
    scope_deep_search = scope.get("deep_search")
    # [M3.5] Wiki 范围（domain / slug）；非数组值兜底为空列表
    scope_domains_raw = scope.get("domains") or []
    scope_slugs_raw = scope.get("wiki_slugs") or []
    scope_domains = [d for d in (scope_domains_raw if isinstance(scope_domains_raw, list) else []) if isinstance(d, str) and d.strip()]
    scope_wiki_slugs = [s for s in (scope_slugs_raw if isinstance(scope_slugs_raw, list) else []) if isinstance(s, str) and s.strip()]

    if isinstance(file_ids, str):
        file_ids = [file_ids]
    if isinstance(folder_ids, str):
        folder_ids = [folder_ids]

    selected_file_ids = list(file_ids)
    if folder_ids:
        async with get_async_db_manager().session_scope() as session:
            expanded_ids = await _expand_folder_file_ids(
                session,
                folder_ids,
                include_subfolders,
                ctx,
                visibilities=visibilities or None,
                dept_ids=dept_ids or None,
                workspace_id=ctx.workspace_id,
            )
            if expanded_ids:
                selected_file_ids.extend(expanded_ids)

    selected_file_ids = list({fid for fid in selected_file_ids if fid})
    if not selected_file_ids:
        selected_file_ids = None

    try:
        top_k = int(top_k)
    except Exception:
        top_k = 5
    if top_k <= 0:
        top_k = 5
    if include_images is None:
        include_images = True
    include_images = bool(include_images)
    if deep_search is None:
        deep_search = scope_deep_search
    if deep_search is None:
        deep_search = raw_user_context.get("deep_search")
    deep_search = bool(deep_search)

    fetch_top_k = (
        mcp_ask_evidence_top_k
        if mcp_ask_evidence_top_k is not None
        else max(top_k, settings.rag.rerank_top_k)
    )
    score_threshold = settings.rag.rerank_score_threshold
    token_budget = (
        mcp_ask_evidence_token_budget
        if mcp_ask_evidence_token_budget is not None
        else settings.supervisor.rag_token_budget_per_request
    )

    skill = DocSkill()

    async def query_once(
        q: str,
        *,
        scoped_file_ids: Optional[List[str]],
        emit_progress: bool,
        query_top_k: int,
    ):
        return await skill.query_knowledge_base(
            query=q,
            original_query=original_query,
            user_context=ctx,
            top_k=query_top_k,
            session_id=(session_id if emit_progress else ""),
            parent_step_id=(parent_step_id if emit_progress else ""),
            messages=safe_messages,
            include_images=include_images,
            file_ids=scoped_file_ids,
            visibilities=visibilities or None,
            dept_ids=dept_ids or None,
            deep_search=deep_search,
            preserve_related_candidates=mcp_ask_preserve_related_candidates,
        )

    async def retrieve_once(q: str):
        normal_task = query_once(
            q,
            scoped_file_ids=selected_file_ids,
            emit_progress=True,
            query_top_k=fetch_top_k,
        )
        if not mcp_ask_preserve_related_candidates:
            return await normal_task

        required_file_id_list = sorted(mcp_ask_required_file_ids)
        query_results = await asyncio.gather(
            normal_task,
            query_once(
                q,
                scoped_file_ids=required_file_id_list,
                emit_progress=False,
                query_top_k=min(
                    fetch_top_k,
                    max(3, len(required_file_id_list) * 3),
                ),
            ),
            return_exceptions=True,
        )
        merged_chunks = []
        seen_chunk_ids: set[str] = set()
        for result in query_results:
            if isinstance(result, Exception):
                logger.warning(
                    "doc_tool: MCP 指定文件补检失败，保留其他检索结果: %s",
                    result,
                )
                continue
            for chunk in result:
                metadata = getattr(chunk, "metadata", None) or {}
                chunk_id = str(
                    getattr(chunk, "chunk_id", "")
                    or metadata.get("chunk_id")
                    or id(chunk)
                )
                if chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(chunk_id)
                merged_chunks.append(chunk)
        found_required_file_ids = {
            str((getattr(chunk, "metadata", None) or {}).get("file_id") or "")
            for chunk in merged_chunks
        }
        missing_required_file_ids = [
            file_id
            for file_id in required_file_id_list
            if file_id not in found_required_file_ids
        ]
        if missing_required_file_ids:
            fallback_chunks = await skill.load_file_chunks_by_ids(
                file_ids=missing_required_file_ids,
                user_context=ctx,
                query=q,
                max_chunks_per_file=3,
                include_images=include_images,
                visibilities=visibilities or None,
                dept_ids=dept_ids or None,
            )
            for chunk in fallback_chunks:
                metadata = getattr(chunk, "metadata", None) or {}
                chunk_id = str(
                    getattr(chunk, "chunk_id", "")
                    or metadata.get("chunk_id")
                    or id(chunk)
                )
                if chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(chunk_id)
                merged_chunks.append(chunk)
        return merged_chunks

    async def navigate_wiki_once():
        # [M3.3] Wiki 路径：直接调 wiki_navigate，输出与 query_knowledge_base 同形（List[DocumentChunk]）
        # [M3.5] 透传 doc_scope.domains / wiki_slugs 收窄装载范围
        return await skill.wiki_navigate(
            query=query,
            user_context=ctx,
            session_id=session_id,
            parent_step_id=parent_step_id,
            domains=scope_domains or None,
            wiki_slugs=scope_wiki_slugs or None,
            file_ids=selected_file_ids,
            visibilities=visibilities or None,
            dept_ids=dept_ids or None,
            business_domain=business_domain or None,
            document_type=document_type or None,
            confidentiality_level=confidentiality_level or None,
        )

    def _merge_wiki_first(wiki_chunks: list, rag_chunks: list) -> list:
        """[M3.3] both 路径合并策略：Wiki 在前，RAG 后置；按 chunk_id 去重。"""
        seen: set[str] = set()
        merged: list = []
        for c in (wiki_chunks or []):
            cid = str(getattr(c, "chunk_id", "") or "")
            if cid and cid in seen:
                continue
            if cid:
                seen.add(cid)
            merged.append(c)
        for c in (rag_chunks or []):
            cid = str(getattr(c, "chunk_id", "") or "")
            if cid and cid in seen:
                continue
            if cid:
                seen.add(cid)
            merged.append(c)
        return merged

    # ============ 首次检索：按 knowledge_path 分支 ============
    wiki_chunks_count = 0
    route_issues: list[str] = []
    wiki_fallback_to_rag = False
    try:
        if norm_path == "wiki":
            wiki_chunks = await navigate_wiki_once()
            wiki_chunks_count = len(wiki_chunks or [])
            if wiki_chunks:
                chunks = wiki_chunks
            else:
                route_issues.append("wiki_missing")
                wiki_fallback_to_rag = True
                chunks = await retrieve_once(search_query)
        elif norm_path == "both":
            wiki_chunks, rag_chunks = await asyncio.gather(
                navigate_wiki_once(),
                retrieve_once(search_query),
                return_exceptions=False,
            )
            wiki_chunks_count = len(wiki_chunks or [])
            if wiki_chunks_count == 0:
                route_issues.append("wiki_missing")
            chunks = _merge_wiki_first(wiki_chunks, rag_chunks)
        else:  # rag (默认)
            chunks = await retrieve_once(search_query)
    except Exception as e:
        logger.error("doc_tool: 首次检索失败 (path=%s): %s", norm_path, e)
        return WorkerResult(
            output=f"知识库检索出错: {str(e)}",
            quality_signal={
                "verdict": "fail",
                "reason_code": "rag_retrieval_error",
                "confidence": 0.9,
                "retryable": True,
            },
            meta={
                "worker_round": round_index,
                "token_used": 0,
                "chunks_used": 0,
                "stop_reason": "rag_retrieval_error",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "knowledge_path": norm_path,
                "wiki_chunks_count": wiki_chunks_count,
                "wiki_chunks_used": 0,
                "rag_chunks_used": 0,
                "wiki_fallback_to_rag": wiki_fallback_to_rag,
                "issues": ["rag_retrieval_error", *route_issues],
                "final_context": [],
                "unused_context": [],
                "answer_context_source": "empty",
                "wiki_gap_signal": None,
            },
        )

    required_ids_for_filter = (
        mcp_ask_required_file_ids
        if mcp_ask_preserve_related_candidates
        else None
    )
    effective_chunks, _, low_score_filtered = doc_selection.filter_effective_chunks(
        chunks,
        score_threshold,
        required_file_ids=required_ids_for_filter,
    )
    used_single_repair = False
    used_relaxed_fallback = False

    if (
        not effective_chunks
        and settings.supervisor.rag_single_repair_enabled
    ):
        repair_query = _build_single_repair_query(search_query, original_query)
        if repair_query and repair_query != search_query:
            used_single_repair = True
            if norm_path in ("wiki", "both") and wiki_chunks_count == 0:
                route_issues.append("wiki_to_rag_repair")
            logger.info("doc_tool: 触发单次修复检索 query='%s...'", repair_query[:80])
            repaired_chunks = await retrieve_once(repair_query)
            chunks = repaired_chunks
            effective_chunks, _, low_score_filtered = doc_selection.filter_effective_chunks(
                chunks,
                score_threshold,
                required_file_ids=required_ids_for_filter,
            )

    if not effective_chunks and chunks and low_score_filtered > 0:
        relaxed_chunks = _relaxed_effective_chunks(chunks)
        if relaxed_chunks:
            used_relaxed_fallback = True
            route_issues.append("rag_relaxed_fallback")
            effective_chunks = relaxed_chunks
            logger.info(
                "doc_tool: 启用低分兜底证据 chunk_id=%s score=%.4f",
                getattr(relaxed_chunks[0], "chunk_id", ""),
                doc_selection.chunk_score(relaxed_chunks[0]),
            )

    if not effective_chunks:
        return WorkerResult(
            output="在知识库中未找到相关信息。",
            quality_signal={
                "verdict": "partial",
                "reason_code": "rag_empty",
                "confidence": 0.95,
                "retryable": False,
            },
            meta={
                "worker_round": round_index,
                "token_used": 0,
                "chunks_used": 0,
                "stop_reason": "rag_empty",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "knowledge_path": norm_path,
                "wiki_chunks_count": wiki_chunks_count,
                "wiki_chunks_used": 0,
                "rag_chunks_used": 0,
                "wiki_fallback_to_rag": wiki_fallback_to_rag,
                "issues": list(dict.fromkeys([*route_issues, "no_final_context"])),
                "final_context": [],
                "unused_context": [],
                "answer_context_source": "empty",
                "wiki_gap_signal": None,
            },
        )

    # ============ [M3.4] wiki_gap 检测：知识路径需要 Wiki 但本次没装载到 ============
    # 触发条件：路由判定走 wiki/both，但 navigator 0 命中（INDEX 缺该实体）；
    # 同时 RAG 路径仍有命中文件（说明文档库里有原文，可让 compile_worker 编译为实体页）。
    wiki_gap_signal: Optional[Dict[str, Any]] = None
    if norm_path in ("wiki", "both") and wiki_chunks_count == 0:
        gap_file_ids: List[str] = sorted({
            (c.metadata or {}).get("file_id", "")
            for c in effective_chunks
            if (c.metadata or {}).get("file_id")
        })
        gap_file_ids = [f for f in gap_file_ids if f][:10]  # 单次最多入队 10 个文件
        if gap_file_ids and ctx.workspace_id:
            try:
                from app.services.wiki_compile_queue_service import get_wiki_compile_queue
                queue = get_wiki_compile_queue()
                task = await queue.enqueue_task(
                    workspace_id=ctx.workspace_id,
                    trigger_type="wiki_gap",
                    user_id=str(getattr(ctx, "user_id", "") or "") or None,
                    payload={
                        "file_ids": gap_file_ids,
                        "source_query": query[:200],
                        "knowledge_path": norm_path,
                    },
                )
                wiki_gap_signal = {
                    "task_id": task.id,
                    "trigger_type": "wiki_gap",
                    "file_ids": gap_file_ids,
                    "knowledge_path": norm_path,
                    "query": query[:200],
                }
                logger.info(
                    "[doc_tool] wiki_gap 入队成功 task_id=%s files=%d path=%s",
                    task.id, len(gap_file_ids), norm_path,
                )
            except Exception as enq_err:  # noqa: BLE001
                logger.warning("[doc_tool] wiki_gap 入队失败（软降级）: %s", enq_err)

    text_max_chunks = (
        mcp_ask_evidence_top_k
        if mcp_ask_evidence_top_k is not None
        else max(1, int(getattr(settings.rag, "synth_text_evidence_cap", 5)))
    )
    text_relative_margin = max(0.0, float(getattr(settings.rag, "synth_text_relative_margin", 0.08)))
    page_window = max(0, int(settings.rag.mm_page_window))
    max_images = max(0, int(getattr(settings.rag, "mm_evidence_max_images", 5)))
    pdf_relative_margin = max(0.0, float(getattr(settings.rag, "mm_pdf_relative_margin", 0.06)))
    secondary_best_delta = max(0.0, float(getattr(settings.rag, "mm_secondary_pdf_best_score_delta", 0.05)))
    secondary_support_ratio = max(0.0, float(getattr(settings.rag, "mm_secondary_pdf_support_ratio", 0.75)))
    max_candidate_files = max(1, int(getattr(settings.rag, "mm_max_candidate_files", 2)))
    primary_anchor_pages = max(1, int(getattr(settings.rag, "mm_primary_anchor_pages", 2)))
    secondary_anchor_pages = max(1, int(getattr(settings.rag, "mm_secondary_anchor_pages", 1)))

    selected_chunks, used_tokens, trimmed = doc_selection.select_chunks_for_synthesizer(
        chunks=effective_chunks,
        token_budget=token_budget,
        score_threshold=score_threshold,
        relative_margin=text_relative_margin,
        max_chunks=text_max_chunks,
        required_file_ids=(
            mcp_ask_required_file_ids
            if mcp_ask_preserve_related_candidates
            else None
        ),
        required_max_chunks=3,
        required_token_budget_ratio=0.5,
    )
    if norm_path == "both" and not mcp_ask_preserve_related_candidates:
        selected_chunks = _ensure_both_context(selected_chunks, effective_chunks, text_max_chunks)

    file_ids = list(
        {
            (c.metadata or {}).get("file_id", "")
            for c in effective_chunks
            if (c.metadata or {}).get("file_id")
        }
    )
    file_info_map = {}
    if file_ids:
        async with get_async_db_manager().session_scope() as session:
            stmt = select(
                File.id,
                File.name,
                File.file_type,
                File.storage_path,
                File.workspace_id,
            ).where(File.id.in_(file_ids))
            result = await session.execute(stmt)
            for row in result:
                file_info_map[row.id] = {
                    "name": row.name,
                    "type": row.file_type,
                    "storage_path": row.storage_path,
                    "workspace_id": row.workspace_id,
                }

    selected_chunk_ids = {
        str(getattr(c, "chunk_id", "") or (getattr(c, "metadata", {}) or {}).get("chunk_id") or "")
        for c in selected_chunks
    }
    final_context = [
        _build_context_record(c, file_info_map=file_info_map, used=True, reason="final_context")
        for c in selected_chunks
    ]
    required_file_evidence_count = sum(
        1
        for item in final_context
        if str(item.get("file_id") or "") in mcp_ask_required_file_ids
    )
    if mcp_ask_required_file_ids and required_file_evidence_count == 0:
        route_issues.append("required_file_evidence_missing")
    unused_context = [
        _build_context_record(c, file_info_map=file_info_map, used=False, reason="not_selected_for_budget")
        for c in effective_chunks
        if str(getattr(c, "chunk_id", "") or (getattr(c, "metadata", {}) or {}).get("chunk_id") or "") not in selected_chunk_ids
    ][:8]
    wiki_chunks_used = sum(1 for item in final_context if item.get("source") == "wiki")
    rag_chunks_used = sum(1 for item in final_context if item.get("source") == "rag")
    if wiki_chunks_used and rag_chunks_used:
        answer_context_source = "both"
    elif wiki_chunks_used:
        answer_context_source = "wiki"
    elif rag_chunks_used:
        answer_context_source = "rag"
    else:
        answer_context_source = "empty"
        route_issues.append("no_final_context")
    if any(item.get("source") == "wiki" and item.get("has_source_files") is False for item in final_context):
        route_issues.append("answer_generation_risk")
    if norm_path in ("wiki", "both") and wiki_chunks_count > 0 and wiki_chunks_used == 0:
        route_issues.append("wiki_hit_but_not_used")
    route_issues = list(dict.fromkeys(route_issues))

    def format_citation(c):
        metadata = c.metadata or {}
        if metadata.get("kind") == "wiki_page":
            slug = str(metadata.get("slug") or "").strip()
            title = str(metadata.get("title") or c.source_file or "Wiki").strip()
            wiki_ref = f"[[{slug}|{title}]]" if slug else title
            source_files = metadata.get("source_files") or []
            if isinstance(source_files, list) and source_files:
                source_hint = "；".join(
                    str(
                        (item.get("file_name") or item.get("name"))
                        if isinstance(item, dict)
                        else item
                    )
                    for item in source_files[:3]
                )
                return f"Wiki 来源: {wiki_ref}\n原文来源: {source_hint}\n{c.content}"
            return f"Wiki 来源: {wiki_ref}\n可信度提示: 该 Wiki 页缺少来源文件，请结合原文证据或说明需要补证。\n{c.content}"

        raw_file_id = metadata.get("file_id", "")
        if raw_file_id and raw_file_id in file_info_map:
            file_info = file_info_map[raw_file_id]
            base_name = file_info["name"]
            file_type = file_info.get("type", "")
            if file_type and not base_name.lower().endswith(f".{file_type.lower()}"):
                display_name = f"{base_name}.{file_type}"
            else:
                display_name = base_name
            primary_page = _chunk_primary_page_number(c)
            anchor_hint = _build_doc_anchor_hint(c.content) if str(file_type).lower() in {"docx", "doc"} else None
            citation_slot = _build_citation_file_slot(
                raw_file_id,
                file_type,
                primary_page,
                anchor_hint=anchor_hint,
            )
            citation_tag = f"[[CITATION:{citation_slot}:{display_name}]]"
            return f"来源: {citation_tag}\n{c.content}"

        source_hint = f"来源: {c.source_file}" if c.source_file else ""
        return f"{source_hint}\n{c.content}" if source_hint else c.content

    wiki_context = "\n\n".join([format_citation(c) for c in selected_chunks if _chunk_source_type(c) == "wiki"])
    rag_context = "\n\n".join([format_citation(c) for c in selected_chunks if _chunk_source_type(c) == "rag"])
    context_sections = []
    if wiki_context:
        context_sections.append(f"## Wiki 证据（解释框架）\n{wiki_context}")
    if rag_context:
        context_sections.append(f"## RAG 原文证据\n{rag_context}")
    context = "\n\n".join(context_sections) or "\n\n".join([format_citation(c) for c in selected_chunks])

    source_files = list(
        {
            c.source_file.split(".")[0][:20]
            for c in selected_chunks[:3]
            if c.source_file
        }
    )
    source_name = "_".join(source_files[:2]) if source_files else "kb"
    source_name = source_name.replace(" ", "_").replace("-", "_")[:30]
    df_key = f"rag_{source_name}_r{round_index}"

    if used_relaxed_fallback:
        reason_code = "rag_relaxed_fallback"
    elif used_single_repair and trimmed:
        reason_code = "rag_repaired_trimmed"
    elif used_single_repair:
        reason_code = "rag_repaired"
    elif trimmed:
        reason_code = "rag_budget_trimmed"
    else:
        reason_code = "rag_ok"

    output_text = f"知识库检索结果（{answer_context_source}）：\n{context}"
    if low_score_filtered > 0:
        output_text += f"\n\n（已过滤 {low_score_filtered} 条低相关内容）"

    text_chunk_ids = []
    for idx, chunk in enumerate(selected_chunks, start=1):
        metadata = chunk.metadata or {}
        chunk_id = str(getattr(chunk, "chunk_id", "") or metadata.get("chunk_id") or f"chunk_{idx}")
        text_chunk_ids.append(chunk_id)

    mm_images: List[Dict[str, Any]] = []
    if include_images and effective_chunks and max_images > 0:
        try:
            from app.core.utils.image_service import get_image_service

            image_service = get_image_service()
            storage_service = get_storage_service()
            page_candidates = doc_selection.select_page_images_for_synthesizer(
                chunks=effective_chunks,
                file_info_map=file_info_map,
                score_threshold=score_threshold,
                pdf_relative_margin=pdf_relative_margin,
                secondary_best_delta=secondary_best_delta,
                secondary_support_ratio=secondary_support_ratio,
                page_window=page_window,
                max_files=max_candidate_files,
                max_images=max_images,
                primary_anchor_pages=primary_anchor_pages,
                secondary_anchor_pages=secondary_anchor_pages,
            )

            candidates_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for candidate in page_candidates:
                candidates_by_file[str(candidate["file_id"])].append(candidate)

            transfer_mode = settings.rag.mm_image_transfer_mode
            for file_id, file_candidates in candidates_by_file.items():
                file_info = file_info_map.get(file_id) or {}
                storage_path = str(file_info.get("storage_path") or "").strip()
                if not storage_path:
                    continue
                workspace_id = str(file_info.get("workspace_id") or ctx.workspace_id or "").strip()
                page_assets: dict[int, dict[str, Any]] = {}
                missing_candidates: list[dict[str, Any]] = []

                for candidate in file_candidates:
                    page_number = int(candidate["page_number"])
                    image_id = f"page_{page_number:04d}"
                    persistent_storage_path = await image_service.ensure_image_storage_path_async(
                        workspace_id=workspace_id,
                        file_id=file_id,
                        image_id=image_id,
                    )
                    if persistent_storage_path:
                        page_assets[page_number] = {
                            "image_id": image_id,
                            "storage_path": persistent_storage_path,
                        }
                        continue
                    missing_candidates.append(candidate)

                if missing_candidates:
                    if not await storage_service.exists(storage_path):
                        logger.warning(
                            "doc_tool: 页图渲染跳过，文件不存在 file_id=%s path=%s",
                            file_id,
                            storage_path,
                        )
                        continue

                    suffix = Path(resolve_storage_path(storage_path)).suffix or ".pdf"
                    async with storage_service.materialize(storage_path, suffix=suffix) as local_pdf_path:
                        for candidate in missing_candidates:
                            page_number = int(candidate["page_number"])
                            image_id = await image_service.render_pdf_page_image_async(
                                pdf_path=local_pdf_path,
                                file_id=file_id,
                                page_number=page_number,
                                dpi=int(settings.rag.page_image_dpi),
                            )
                            if not image_id:
                                continue
                            persistent_storage_path = await image_service.ensure_image_storage_path_async(
                                workspace_id=workspace_id,
                                file_id=file_id,
                                image_id=image_id,
                            )
                            if not persistent_storage_path:
                                continue
                            page_assets[page_number] = {
                                "image_id": image_id,
                                "storage_path": persistent_storage_path,
                            }

                for candidate in file_candidates:
                    page_number = int(candidate["page_number"])
                    asset = page_assets.get(page_number)
                    if not asset:
                        continue

                    image_id = str(asset["image_id"])
                    if transfer_mode == "base64":
                        image_data = image_service.get_image_base64(file_id, image_id)
                        if not image_data:
                            image_data = await image_service.get_image_base64_from_storage_path_async(
                                str(asset["storage_path"]),
                            )
                        if not image_data:
                            continue
                        mm_images.append(
                            {
                                "file_id": file_id,
                                "file_name": candidate.get("file_name"),
                                "page_number": page_number,
                                "image_id": image_id,
                                "kind": candidate.get("kind"),
                                "url": image_data,
                            }
                        )
                    else:
                        image_url = await image_service.get_multimodal_image_url_async(
                            workspace_id=workspace_id,
                            file_id=file_id,
                            image_id=image_id,
                            ttl=int(settings.rag.mm_image_url_ttl_sec),
                        )
                        if not image_url:
                            continue
                        mm_images.append(
                            {
                                "file_id": file_id,
                                "file_name": candidate.get("file_name"),
                                "page_number": page_number,
                                "image_id": image_id,
                                "kind": candidate.get("kind"),
                                "url": image_url,
                            }
                        )
        except Exception as e:
            logger.warning("doc_tool: 构建多模态图片证据失败，降级文本-only: %s", e)

    doc_selection.log_image_alignment(selected_chunks, mm_images, file_info_map)

    mm_evidence = {
        "mode": settings.rag.mm_image_transfer_mode,
        "text_chunk_ids": text_chunk_ids,
        "text_chunk_count": len(selected_chunks),
        "images": mm_images,
        "limits": {
            "text_max": text_max_chunks,
            "text_token_budget": token_budget,
            "text_relative_margin": text_relative_margin,
            "page_window": page_window,
            "image_max": max_images,
            "pdf_relative_margin": pdf_relative_margin,
            "max_candidate_files": max_candidate_files,
        },
    }

    return WorkerResult(
        output=output_text,
        artifacts={df_key: doc_selection.chunks_to_df(selected_chunks)},
        quality_signal={
            "verdict": "pass",
            "reason_code": reason_code,
            "confidence": 0.85,
            "retryable": False,
        },
        meta={
            "worker_round": round_index,
            "token_used": used_tokens,
            "chunks_used": len(selected_chunks),
            "stop_reason": reason_code,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "mm_evidence": mm_evidence,
            # [M3.3] 知识路径埋点（供 Synthesizer 与 wiki_route_metrics 消费）
            "knowledge_path": norm_path,
            "wiki_fallback_to_rag": wiki_fallback_to_rag,
            "issues": route_issues,
            "wiki_chunks_count": wiki_chunks_count,
            "wiki_chunks_used": wiki_chunks_used,
            "rag_chunks_used": rag_chunks_used,
            "final_context": final_context,
            "unused_context": unused_context,
            "answer_context_source": answer_context_source,
            "evidence_token_budget": token_budget,
            "required_file_ids": sorted(mcp_ask_required_file_ids),
            "required_file_evidence_count": required_file_evidence_count,
            "preserve_related_candidates": mcp_ask_preserve_related_candidates,
            # [M3.4] 选中送给 Synthesizer 的 wiki 实体清单（用于 [[slug|title]] 引用提示）
            "wiki_entities": [
                {
                    "slug": str((c.metadata or {}).get("slug") or ""),
                    "title": str((c.metadata or {}).get("title") or ""),
                    "summary": str((c.summary or "")[:120]),
                }
                for c in selected_chunks
                if (c.metadata or {}).get("kind") == "wiki_page"
                and (c.metadata or {}).get("slug")
            ],
            # [M3.4] Wiki 缺失信号（None 表示未触发；非 None 时已入队 wiki_compile_tasks）
            "wiki_gap_signal": wiki_gap_signal,
        },
    )


doc_tool = StructuredTool.from_function(
    func=None,
    coroutine=run_doc_task,
    name="doc_worker",
    description="执行知识库检索任务。在文档库中搜索相关信息，返回检索结果及来源引用。",
    args_schema=DocToolInput,
)
