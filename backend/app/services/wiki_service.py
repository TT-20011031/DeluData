"""WikiService - 编排层。

职责：
- 加载工作区现有 wiki 视图（喂给 Compiler）
- 把 Compiler 的 CompileOutcome 在事务里落库
  · 写 / 更新 wiki_pages（带乐观锁）
  · 写 wiki_revisions
  · 写 wiki_page_sources
  · 调用 WikiLinker 重置 outgoing_links
  · 写 contradicts 链接
- 提供给 API 与 CLI 的高层 helper：compile_workspace / compile_files / lint_workspace
- CRUD：列表 / 详情 / 手动编辑

设计原则：
- 单一事务：每个公开方法对应一个事务边界
- 不直接调 LLM；LLM 的调用交给 WikiCompiler / WikiLinter
- 所有写入显式带 workspace_id；读取依赖 TenantMixin（外层已 set_current_workspace）
"""
from __future__ import annotations

import difflib
import json
import logging
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.database import get_async_db_manager
from app.core.db.tenant_mixin import (
    bypass_tenant_filter,
    get_current_workspace,
    set_current_workspace,
)
from app.core.wiki.compiler import (
    CompiledPage,
    CompileOutcome,
    WikiCompiler,
    get_wiki_compiler,
)
from app.core.wiki.chunk_loader import get_wiki_chunk_loader
from app.core.wiki.linker import WikiLinker
from app.core.wiki.linter import LintReport, WikiLinter, get_wiki_linter
from app.core.wiki.slug import normalize_slug
from app.models.knowledge.graph import File, Folder
from app.models.wiki.wiki_compile_task import WikiCompileTask
from app.models.wiki.wiki_link import WikiLink
from app.models.wiki.wiki_page import WikiPage
from app.models.wiki.wiki_page_source import WikiPageSource
from app.models.wiki.wiki_route_metric import WikiRouteMetric
from app.models.wiki.wiki_revision import WikiRevision

logger = logging.getLogger("wiki.service")

TRUSTED_WIKI_PAGE_STATUSES = {"published", "verified"}
HIGH_VALUE_SOURCE_HINTS = (
    "policy",
    "制度",
    "合同",
    "contract",
    "产品",
    "product",
    "项目",
    "project",
    "手册",
    "manual",
    "规范",
    "spec",
)


def _merge_aliases(*groups: Optional[list[str]]) -> Optional[list[str]]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for item in group or []:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                merged.append(text)
    return merged or None


def _target_status_for_compile(triggered_by: str) -> str:
    """Return the maturity status for compiler output.

    Compiler output is model-proposed knowledge, so it starts as a candidate.
    Manual governance can promote pages to published or verified later.
    """
    return "candidate"


def _clamp_int(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def _governance_trigger_type(last_compiled_by: Optional[str]) -> str:
    raw = str(last_compiled_by or "").lower()
    if "wiki_gap" in raw:
        return "wiki_gap"
    if "reflection" in raw:
        return "reflection"
    if "manual" in raw or raw.startswith("user:"):
        return "manual"
    if "doc_upload" in raw or "ingest" in raw:
        return "ingest"
    if raw.startswith("worker:"):
        return raw.split(":", 1)[1] or "worker"
    return raw or "unknown"


def _priority_from_score(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _merge_source_rows(source_rows: list[Any]) -> list[dict[str, Any]]:
    """Merge page sources by display file name and prefer live files.

    Re-uploading a deleted document can leave historical WikiPageSource rows
    pointing to the old file id. For governance and display, the current file
    with the same name is the useful source; the deleted one should not create
    duplicate出处 or keep the page blocked forever.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for row in source_rows:
        src = row[0]
        name = row[1]
        file_type = row[2] if len(row) > 2 else None
        status = row[3] if len(row) > 3 else None
        is_deleted = bool(row[4]) if len(row) > 4 else False
        visibility = row[5] if len(row) > 5 else None
        dept_id = row[6] if len(row) > 6 else None
        owner_id = row[7] if len(row) > 7 else None
        document_type = row[8] if len(row) > 8 else None
        business_domain = row[9] if len(row) > 9 else None
        confidentiality_level = row[10] if len(row) > 10 else None
        effective_from = row[11] if len(row) > 11 else None
        effective_until = row[12] if len(row) > 12 else None
        external_ref = row[13] if len(row) > 13 else None
        display_name = str(name or src.file_id or "")
        key = display_name.lower()
        live = bool(name) and not is_deleted and status != "deleted"
        current = grouped.get(key)
        if current is None:
            grouped[key] = {
                "src": src,
                "file_name": name,
                "file_type": file_type,
                "status": status,
                "is_deleted": is_deleted or not bool(name),
                "visibility": visibility,
                "dept_id": dept_id,
                "owner_id": owner_id,
                "document_type": document_type,
                "business_domain": business_domain,
                "confidentiality_level": confidentiality_level,
                "effective_from": effective_from,
                "effective_until": effective_until,
                "external_ref": external_ref,
                "live": live,
                "chunk_ids": list(src.chunk_ids or []),
                "excerpt": src.excerpt,
            }
            continue
        current_live = bool(current.get("live"))
        if live and not current_live:
            keep_chunks = list({*current.get("chunk_ids", []), *(src.chunk_ids or [])})
            grouped[key] = {
                "src": src,
                "file_name": name,
                "file_type": file_type,
                "status": status,
                "is_deleted": False,
                "visibility": visibility,
                "dept_id": dept_id,
                "owner_id": owner_id,
                "document_type": document_type,
                "business_domain": business_domain,
                "confidentiality_level": confidentiality_level,
                "effective_from": effective_from,
                "effective_until": effective_until,
                "external_ref": external_ref,
                "live": True,
                "chunk_ids": keep_chunks,
                "excerpt": src.excerpt or current.get("excerpt"),
            }
        else:
            current["chunk_ids"] = list({*current.get("chunk_ids", []), *(src.chunk_ids or [])})
            current["is_deleted"] = bool(current.get("is_deleted")) and not live
            current["live"] = current_live or live
    return list(grouped.values())


def _source_select_columns() -> tuple[Any, ...]:
    return (
        WikiPageSource,
        File.name,
        File.file_type,
        File.status,
        File.is_deleted,
        File.visibility,
        File.dept_id,
        File.owner_id,
        File.document_type,
        File.business_domain,
        File.confidentiality_level,
        File.effective_from,
        File.effective_until,
        File.external_ref,
    )


def _compact_counts(values: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value if value not in (None, "") else "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _source_scope_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    live_items = [item for item in items if item.get("live")]
    effective = live_items or items
    dept_ids = sorted(
        {
            int(item["dept_id"])
            for item in effective
            if item.get("dept_id") is not None
        }
    )
    now = datetime.utcnow()
    expired_count = 0
    for item in effective:
        effective_until = item.get("effective_until")
        if effective_until and effective_until < now:
            expired_count += 1
    return {
        "source_count": len(effective),
        "department_ids": dept_ids,
        "is_cross_department": len(dept_ids) > 1,
        "is_unclassified": len(effective) == 0 or any(
            item.get("dept_id") is None for item in effective
        ),
        "visibility_counts": _compact_counts([item.get("visibility") for item in effective]),
        "document_type_counts": _compact_counts([item.get("document_type") for item in effective]),
        "business_domain_counts": _compact_counts([item.get("business_domain") for item in effective]),
        "confidentiality_counts": _compact_counts(
            [item.get("confidentiality_level") for item in effective]
        ),
        "expired_source_count": expired_count,
        "has_expired_source": expired_count > 0,
    }


def _chunk_preview(text: str, max_chars: int = 180) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _build_wiki_graph_payload(
    *,
    pages: list[Any],
    links: list[Any],
    source_scope_by_page: dict[str, dict[str, Any]],
    source_items_by_page: Optional[dict[str, list[dict[str, Any]]]] = None,
) -> dict[str, Any]:
    trusted_pages = [
        page
        for page in pages
        if str(getattr(page, "status", "")) in TRUSTED_WIKI_PAGE_STATUSES
    ]
    page_ids = {str(page.id) for page in trusted_pages}

    degree: Counter[str] = Counter()
    edges: list[dict[str, Any]] = []
    for link in links:
        if str(getattr(link, "status", "")) != "active":
            continue
        source_id = str(link.source_page_id)
        target_id = str(link.target_page_id)
        if source_id not in page_ids or target_id not in page_ids:
            continue
        degree[source_id] += 1
        degree[target_id] += 1
        evidence_chunk_ids = list(getattr(link, "evidence_chunk_ids", None) or [])
        edges.append(
            {
                "id": str(link.id),
                "source": source_id,
                "target": target_id,
                "link_type": str(getattr(link, "link_type", None) or "mentions"),
                "status": str(getattr(link, "status", None) or "active"),
                "confidence": float(getattr(link, "confidence", None) or 0.0),
                "note": getattr(link, "note", None),
                "evidence_count": len(evidence_chunk_ids),
            }
        )

    source_items_by_page = source_items_by_page or {}
    file_nodes: dict[str, dict[str, Any]] = {}
    source_edge_count = 0
    for page in trusted_pages:
        page_id = str(page.id)
        for item in source_items_by_page.get(page_id, []):
            if item.get("is_deleted") or not item.get("live"):
                continue
            src = item.get("src")
            file_id = str(getattr(src, "file_id", "") or "")
            if not file_id:
                continue
            node_id = f"file:{file_id}"
            file_name = item.get("file_name") or file_id
            file_node = file_nodes.get(node_id)
            if file_node is None:
                file_node = {
                    "id": node_id,
                    "node_type": "file",
                    "slug": file_id,
                    "title": file_name,
                    "summary": item.get("excerpt"),
                    "domain": item.get("business_domain") or "source",
                    "status": item.get("status") or "indexed",
                    "degree": 0,
                    "source_scope": None,
                    "updated_at": None,
                    "file_id": file_id,
                    "file_type": item.get("file_type"),
                    "file_name": file_name,
                    "source_count": 0,
                }
                file_nodes[node_id] = file_node
            file_node["source_count"] = int(file_node.get("source_count") or 0) + 1
            degree[node_id] += 1
            degree[page_id] += 1
            source_edge_count += 1
            edges.append(
                {
                    "id": f"source:{file_id}:{page_id}",
                    "source": node_id,
                    "target": page_id,
                    "link_type": "source",
                    "status": "active",
                    "confidence": float(item.get("relevance") or 1.0),
                    "note": None,
                    "evidence_count": len(item.get("chunk_ids") or []),
                }
            )

    nodes: list[dict[str, Any]] = []
    for page in trusted_pages:
        page_id = str(page.id)
        updated_at = getattr(page, "updated_at", None)
        nodes.append(
            {
                "id": page_id,
                "node_type": "entity",
                "slug": page.slug,
                "title": page.title,
                "summary": getattr(page, "summary", None),
                "domain": getattr(page, "domain", None) or "general",
                "status": page.status,
                "degree": int(degree.get(page_id, 0)),
                "source_scope": source_scope_by_page.get(page_id),
                "updated_at": updated_at.isoformat() if updated_at else None,
                "file_id": None,
                "file_type": None,
                "file_name": None,
                "source_count": int((source_scope_by_page.get(page_id) or {}).get("source_count") or 0),
            }
        )
    for file_node in file_nodes.values():
        file_node["degree"] = int(degree.get(str(file_node["id"]), 0))
        nodes.append(file_node)

    domain_counts = Counter(str(getattr(page, "domain", None) or "general") for page in trusted_pages)
    link_type_counts = Counter(edge["link_type"] for edge in edges)
    isolated_count = sum(1 for node in nodes if int(node["degree"]) == 0)
    wiki_link_count = len(edges) - source_edge_count

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "page_count": len(trusted_pages),
            "link_count": wiki_link_count,
            "isolated_count": isolated_count,
            "domain_counts": dict(domain_counts),
            "link_type_counts": dict(link_type_counts),
            "entity_count": len(trusted_pages),
            "file_count": len(file_nodes),
            "wiki_link_count": wiki_link_count,
            "source_edge_count": source_edge_count,
            "total_edge_count": len(edges),
        },
    }


def _wiki_source_exists_filter(
    *,
    workspace_id: str,
    scope: Optional[str] = None,
    department_id: Optional[int] = None,
    user_department_id: Optional[int] = None,
    user_id: Optional[str] = None,
    user_role: str = "user",
    business_domain: Optional[str] = None,
    document_type: Optional[str] = None,
    confidentiality_level: Optional[str] = None,
):
    source_stmt = (
        select(WikiPageSource.id)
        .join(File, File.id == WikiPageSource.file_id)
        .where(
            WikiPageSource.workspace_id == workspace_id,
            WikiPageSource.page_id == WikiPage.id,
            File.workspace_id == workspace_id,
            File.is_deleted.is_(False),
        )
    )
    if user_role != "knowledge_manager":
        source_stmt = source_stmt.where(
            or_(
                File.visibility == "public",
                and_(File.visibility == "dept", File.dept_id == user_department_id),
                and_(File.visibility == "private", File.owner_id == user_id),
            )
        )
    if business_domain:
        source_stmt = source_stmt.where(File.business_domain == business_domain)
    if document_type:
        source_stmt = source_stmt.where(File.document_type == document_type)
    if confidentiality_level:
        source_stmt = source_stmt.where(File.confidentiality_level == confidentiality_level)
    if scope == "public":
        source_stmt = source_stmt.where(File.visibility == "public")
    elif scope == "my_department":
        source_stmt = source_stmt.where(File.dept_id == user_department_id)
    elif scope == "department":
        source_stmt = source_stmt.where(File.dept_id == department_id)
    elif scope == "unclassified":
        return or_(~exists(source_stmt), exists(source_stmt.where(File.dept_id.is_(None))))
    elif scope == "cross_department":
        grouped = (
            select(WikiPageSource.page_id)
            .join(File, File.id == WikiPageSource.file_id)
            .where(
                WikiPageSource.workspace_id == workspace_id,
                WikiPageSource.page_id == WikiPage.id,
                File.workspace_id == workspace_id,
                File.is_deleted.is_(False),
                File.dept_id.is_not(None),
            )
            .group_by(WikiPageSource.page_id)
            .having(func.count(func.distinct(File.dept_id)) > 1)
        )
        return exists(grouped)
    return exists(source_stmt)


# =============================================================================
# 内部 helper
# =============================================================================


def _serialize_existing_pages(pages: list[WikiPage]) -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "slug": p.slug,
            "title": p.title,
            "aliases": list(p.aliases or []),
            "summary": p.summary or "",
            "markdown_body": p.markdown_body or "",
            "domain": p.domain,
            "version": int(p.version or 1),
            "status": p.status,
        }
        for p in pages
    ]


# 链接 link_type 优先级：当同一对 (source, target) 出现多种 link_type 时，
# 仅保留最强语义的一条，避免反向链接列表中"提及"与"相关"重复出现。
# 数字越大优先级越高。
_LINK_TYPE_PRIORITY: dict[str, int] = {
    "supersedes": 4,
    "contradicts": 3,
    "related": 2,
    "mentions": 1,
}


def _dedupe_links_by_peer(
    rows: list[tuple[Any, str, str]],
    *,
    direction: str,
) -> list[dict[str, Any]]:
    """对 outgoing/incoming 链接按对端 slug 去重，保留 link_type 优先级最高的一条。

    Args:
        rows: SQLAlchemy 返回的 (WikiLink, peer_slug, peer_title) 元组列表
        direction: 'outgoing' → 输出键名 target_slug/target_title；
                   'incoming' → 输出键名 source_slug/source_title
    """
    best: dict[str, dict[str, Any]] = {}
    for link, peer_slug, peer_title in rows:
        if not peer_slug:
            continue
        priority = _LINK_TYPE_PRIORITY.get(str(link.link_type), 0)
        cur = best.get(peer_slug)
        if cur is None or priority > cur["_priority"]:
            payload = {
                "id": link.id,
                "link_type": link.link_type,
                "status": link.status,
                "note": link.note,
                "_priority": priority,
            }
            if direction == "outgoing":
                payload["target_slug"] = peer_slug
                payload["target_title"] = peer_title
            else:
                payload["source_slug"] = peer_slug
                payload["source_title"] = peer_title
            best[peer_slug] = payload
    # 去掉内部使用的 _priority 字段
    return [{k: v for k, v in item.items() if k != "_priority"} for item in best.values()]


def _diff(old: str, new: str) -> str:
    if old == new:
        return ""
    return "\n".join(
        difflib.unified_diff(
            (old or "").splitlines(),
            (new or "").splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )


# =============================================================================
# WikiService
# =============================================================================


class WikiService:
    """高层服务：编排 Compiler / Linker / Linter 与数据库。"""

    def __init__(
        self,
        compiler: Optional[WikiCompiler] = None,
        linter: Optional[WikiLinter] = None,
    ) -> None:
        self._compiler = compiler
        self._linter = linter

    @property
    def compiler(self) -> WikiCompiler:
        if self._compiler is None:
            self._compiler = get_wiki_compiler()
        return self._compiler

    @property
    def linter(self) -> WikiLinter:
        if self._linter is None:
            self._linter = get_wiki_linter()
        return self._linter

    # ------------------------------------------------------------------
    # 公开 API：编译
    # ------------------------------------------------------------------

    async def compile_files(
        self,
        *,
        workspace_id: str,
        file_ids: list[str],
        triggered_by: str = "manual",
        user_id: Optional[str] = None,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        """编译指定文件并落库。

        progress_cb: 可选 (stage, done, total) 回调；
                     stage='extract' / 'compile'，用于把进度上报给任务队列等。
        cancel_check: 可选；返回 True 表示用户已取消。透传给 compiler；
                      已成功的页仍正常落库（"软取消"承诺）。返回 summary 含 cancelled 字段。
        """
        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        db_manager = get_async_db_manager()
        try:
            async with db_manager.session_scope() as session:
                file_payloads = await self._fetch_file_payloads(
                    session, workspace_id=workspace_id, file_ids=file_ids
                )
                if not file_payloads:
                    return {
                        "success": False,
                        "error": "未找到任何匹配的文件（可能已被删除或不属于该工作区）",
                    }

                existing_pages = await self._fetch_existing_pages(
                    session, workspace_id=workspace_id
                )
                outcome = await self.compiler.compile_for_files(
                    workspace_id=workspace_id,
                    file_payloads=file_payloads,
                    existing_pages=_serialize_existing_pages(existing_pages),
                    progress_cb=progress_cb,
                    cancel_check=cancel_check,
                )
                applied = await self._apply_outcome(
                    session,
                    workspace_id=workspace_id,
                    outcome=outcome,
                    triggered_by=triggered_by,
                )
                summary = {
                    "success": True,
                    "trigger": triggered_by,
                    "user_id": user_id,
                    "files": len(file_payloads),
                    "candidates": outcome.total_candidates,
                    "applied": applied,
                    "skipped": outcome.skipped_candidates,
                    "errors": outcome.error_messages,
                    "elapsed_seconds": round(outcome.elapsed_seconds, 3),
                    # 软取消标志：worker 据此决定调 mark_cancelled 还是 complete_task
                    "cancelled": bool(outcome.cancelled),
                }
                logger.info("[WikiService] compile_files done: %s", summary)
                return summary
        finally:
            set_current_workspace(previous_ws or "")

    async def compile_workspace(
        self,
        *,
        workspace_id: str,
        triggered_by: str = "manual",
        user_id: Optional[str] = None,
        file_limit: int = 200,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        """对整个工作区已索引的文件执行一次全量编译（用于 M1 验收）。

        progress_cb / cancel_check: 透传给底层 compile_files。
        """
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            with bypass_tenant_filter():
                stmt = (
                    select(File.id)
                    .where(
                        File.workspace_id == workspace_id,
                        File.is_deleted.is_(False),
                        File.status == "indexed",
                    )
                    .order_by(File.created_at.desc())
                    .limit(file_limit)
                )
                file_ids = [row[0] for row in (await session.execute(stmt)).all()]

        if not file_ids:
            return {"success": False, "error": "工作区内没有可编译的已索引文件"}
        return await self.compile_files(
            workspace_id=workspace_id,
            file_ids=file_ids,
            triggered_by=triggered_by,
            user_id=user_id,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
        )

    # ------------------------------------------------------------------
    # 公开 API：Lint
    # ------------------------------------------------------------------

    async def lint_workspace(
        self,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        db_manager = get_async_db_manager()
        try:
            async with db_manager.session_scope() as session:
                report = await self.linter.run(
                    session=session, workspace_id=workspace_id
                )
                return report.to_dict()
        finally:
            set_current_workspace(previous_ws or "")

    # ------------------------------------------------------------------
    # 公开 API：CRUD
    # ------------------------------------------------------------------

    async def list_pages(
        self,
        *,
        workspace_id: str,
        user_id: Optional[str] = None,
        user_role: str = "user",
        user_department_id: Optional[int] = None,
        domain: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
        scope: Optional[str] = None,
        department_id: Optional[int] = None,
        business_domain: Optional[str] = None,
        document_type: Optional[str] = None,
        confidentiality_level: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        db_manager = get_async_db_manager()
        try:
            async with db_manager.session_scope() as session:
                stmt = select(WikiPage).where(WikiPage.workspace_id == workspace_id)
                if domain:
                    stmt = stmt.where(WikiPage.domain == domain)
                if status:
                    statuses = [s.strip() for s in str(status).split(",") if s.strip()]
                    if statuses:
                        stmt = stmt.where(WikiPage.status.in_(statuses))
                else:
                    # 实体 Wiki 只展示正式知识；候选页仅在候选治理台可见。
                    stmt = stmt.where(WikiPage.status.in_(list(TRUSTED_WIKI_PAGE_STATUSES)))
                if keyword:
                    kw = f"%{keyword.strip()}%"
                    stmt = stmt.where(
                        or_(
                            WikiPage.title.like(kw),
                            WikiPage.slug.like(kw),
                            WikiPage.summary.like(kw),
                        )
                    )
                scope_filter = _wiki_source_exists_filter(
                    workspace_id=workspace_id,
                    scope=scope,
                    department_id=department_id,
                    user_department_id=user_department_id,
                    user_id=user_id,
                    user_role=user_role,
                    business_domain=business_domain,
                    document_type=document_type,
                    confidentiality_level=confidentiality_level,
                )
                if (scope and scope != "all") or user_role != "knowledge_manager" or any(
                    [business_domain, document_type, confidentiality_level]
                ):
                    stmt = stmt.where(scope_filter)

                count_stmt = select(func.count()).select_from(stmt.subquery())
                total = (await session.execute(count_stmt)).scalar_one()

                stmt = (
                    stmt.order_by(WikiPage.updated_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
                pages = (await session.execute(stmt)).scalars().all()
                items: list[dict[str, Any]] = []
                for p in pages:
                    governance = None
                    if str(p.status) in {"candidate", "draft"}:
                        governance = await self._build_governance_snapshot(
                            session, workspace_id=workspace_id, page=p
                        )
                    source_scope = await self._build_page_source_scope(
                        session, workspace_id=workspace_id, page_id=p.id
                    )
                    items.append(
                        {
                            "id": p.id,
                            "slug": p.slug,
                            "title": p.title,
                            "summary": p.summary,
                            "domain": p.domain,
                            "status": p.status,
                            "version": p.version,
                            "char_count": p.char_count,
                            "last_compiled_at": (
                                p.last_compiled_at.isoformat()
                                if p.last_compiled_at
                                else None
                            ),
                            "updated_at": p.updated_at.isoformat()
                            if p.updated_at
                            else None,
                            "governance": governance,
                            "source_scope": source_scope,
                        }
                    )
                return {
                    "total": int(total or 0),
                    "items": items,
                }
        finally:
            set_current_workspace(previous_ws or "")

    async def get_graph(
        self,
        *,
        workspace_id: str,
        user_id: Optional[str] = None,
        user_role: str = "user",
        user_department_id: Optional[int] = None,
        domain: Optional[str] = None,
        scope: Optional[str] = None,
        department_id: Optional[int] = None,
        business_domain: Optional[str] = None,
        document_type: Optional[str] = None,
        confidentiality_level: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return the trusted Wiki entity graph visible to the current user."""
        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        db_manager = get_async_db_manager()
        try:
            async with db_manager.session_scope() as session:
                page_stmt = select(WikiPage).where(
                    WikiPage.workspace_id == workspace_id,
                    WikiPage.status.in_(list(TRUSTED_WIKI_PAGE_STATUSES)),
                )
                if domain:
                    page_stmt = page_stmt.where(WikiPage.domain == domain)

                scope_filter = _wiki_source_exists_filter(
                    workspace_id=workspace_id,
                    scope=scope,
                    department_id=department_id,
                    user_department_id=user_department_id,
                    user_id=user_id,
                    user_role=user_role,
                    business_domain=business_domain,
                    document_type=document_type,
                    confidentiality_level=confidentiality_level,
                )
                if (scope and scope != "all") or user_role != "knowledge_manager" or any(
                    [business_domain, document_type, confidentiality_level]
                ):
                    page_stmt = page_stmt.where(scope_filter)

                pages = (
                    await session.execute(page_stmt.order_by(WikiPage.updated_at.desc()))
                ).scalars().all()
                page_ids = {str(p.id) for p in pages}

                links = (
                    await session.execute(
                        select(WikiLink).where(
                            WikiLink.workspace_id == workspace_id,
                            WikiLink.status == "active",
                            WikiLink.source_page_id.in_(page_ids),
                            WikiLink.target_page_id.in_(page_ids),
                        )
                    )
                ).scalars().all()

                source_scope_by_page: dict[str, dict[str, Any]] = {}
                for page in pages:
                    source_scope_by_page[str(page.id)] = await self._build_page_source_scope(
                        session, workspace_id=workspace_id, page_id=page.id
                    )
                source_items_by_page: dict[str, list[dict[str, Any]]] = {}
                if page_ids:
                    source_stmt = (
                        select(*_source_select_columns())
                        .select_from(WikiPageSource)
                        .join(File, File.id == WikiPageSource.file_id)
                        .where(
                            WikiPageSource.workspace_id == workspace_id,
                            WikiPageSource.page_id.in_(page_ids),
                            File.workspace_id == workspace_id,
                            File.is_deleted.is_(False),
                        )
                    )
                    if user_role != "knowledge_manager":
                        source_stmt = source_stmt.where(
                            or_(
                                File.visibility == "public",
                                and_(File.visibility == "dept", File.dept_id == user_department_id),
                                and_(File.visibility == "private", File.owner_id == user_id),
                            )
                        )
                    if business_domain:
                        source_stmt = source_stmt.where(File.business_domain == business_domain)
                    if document_type:
                        source_stmt = source_stmt.where(File.document_type == document_type)
                    if confidentiality_level:
                        source_stmt = source_stmt.where(File.confidentiality_level == confidentiality_level)
                    if scope == "public":
                        source_stmt = source_stmt.where(File.visibility == "public")
                    elif scope == "my_department":
                        source_stmt = source_stmt.where(File.dept_id == user_department_id)
                    elif scope == "department":
                        effective_dept = department_id or user_department_id
                        if effective_dept is not None:
                            source_stmt = source_stmt.where(File.dept_id == effective_dept)
                    elif scope == "private":
                        source_stmt = source_stmt.where(File.visibility == "private", File.owner_id == user_id)

                    source_rows = (await session.execute(source_stmt)).all()
                    rows_by_page: dict[str, list[Any]] = {}
                    for row in source_rows:
                        page_id = str(row[0].page_id)
                        rows_by_page.setdefault(page_id, []).append(row)
                    for page_id, rows in rows_by_page.items():
                        source_items_by_page[page_id] = [
                            item
                            for item in _merge_source_rows(rows)
                            if item.get("live") and not item.get("is_deleted")
                        ]

                return _build_wiki_graph_payload(
                    pages=pages,
                    links=links,
                    source_scope_by_page=source_scope_by_page,
                    source_items_by_page=source_items_by_page,
                )
        finally:
            set_current_workspace(previous_ws or "")

    async def get_quota_status(
        self,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        """[M4.2] 工作区 Wiki 配额使用情况。

        Returns:
            {
              "workspace_id": "...",
              "max_pages_per_workspace": 500,
              "max_links_per_page": 50,
              "pages": {"current": 123, "limit": 500, "usage_pct": 0.246},
              "links": {"max_per_page": 50, "max_outgoing_observed": 38},
              "near_limit": false,
            }
        """
        from app.config import get_settings as _get_settings
        wiki_cfg = _get_settings().wiki
        max_pages = max(1, int(wiki_cfg.max_pages_per_workspace or 500))
        max_links = max(1, int(wiki_cfg.max_links_per_page or 50))

        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        db_manager = get_async_db_manager()
        try:
            async with db_manager.session_scope() as session:
                # 当前页数（archived 不算）
                current_pages = (
                    await session.execute(
                        select(func.count(WikiPage.id)).where(
                            WikiPage.workspace_id == workspace_id,
                            WikiPage.status != "archived",
                        )
                    )
                ).scalar_one() or 0

                # 单页最大出链数（子查询：先 count 再 max）
                link_count_subq = (
                    select(func.count(WikiLink.id).label("link_count"))
                    .where(WikiLink.workspace_id == workspace_id)
                    .group_by(WikiLink.source_page_id)
                    .subquery()
                )
                max_outgoing = (
                    await session.execute(
                        select(func.max(link_count_subq.c.link_count))
                    )
                ).scalar() or 0

                pages_pct = float(current_pages) / float(max_pages) if max_pages > 0 else 0.0
                near_limit = (
                    pages_pct >= 0.9
                    or int(max_outgoing or 0) >= int(max_links * 0.9)
                )
                return {
                    "workspace_id": workspace_id,
                    "max_pages_per_workspace": max_pages,
                    "max_links_per_page": max_links,
                    "pages": {
                        "current": int(current_pages),
                        "limit": max_pages,
                        "usage_pct": round(pages_pct, 4),
                    },
                    "links": {
                        "max_per_page": max_links,
                        "max_outgoing_observed": int(max_outgoing or 0),
                    },
                    "near_limit": bool(near_limit),
                }
        finally:
            set_current_workspace(previous_ws or "")

    async def list_domains(
        self,
        *,
        workspace_id: str,
        sample_per_domain: int = 5,
    ) -> list[dict[str, Any]]:
        """[M3.5] 按 domain 聚合 published wiki 实体页，供 KnowledgeScopePicker 一级 Tab。

        Returns:
            [{"domain": "policy", "page_count": 12, "sample_titles": ["A", "B", ...]}, ...]
        """
        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        db_manager = get_async_db_manager()
        try:
            async with db_manager.session_scope() as session:
                count_stmt = (
                    select(WikiPage.domain, func.count(WikiPage.id).label("c"))
                    .where(
                        WikiPage.workspace_id == workspace_id,
                        WikiPage.status.in_(list(TRUSTED_WIKI_PAGE_STATUSES)),
                    )
                    .group_by(WikiPage.domain)
                    .order_by(func.count(WikiPage.id).desc())
                )
                rows = (await session.execute(count_stmt)).all()
                buckets: list[dict[str, Any]] = []
                for domain_name, page_cnt in rows:
                    sample_stmt = (
                        select(WikiPage.title)
                        .where(
                            WikiPage.workspace_id == workspace_id,
                            WikiPage.domain == domain_name,
                            WikiPage.status.in_(list(TRUSTED_WIKI_PAGE_STATUSES)),
                        )
                        .order_by(WikiPage.updated_at.desc())
                        .limit(max(0, int(sample_per_domain or 0)))
                    )
                    sample_titles = [
                        t for (t,) in (await session.execute(sample_stmt)).all() if t
                    ]
                    buckets.append({
                        "domain": str(domain_name or "general"),
                        "page_count": int(page_cnt or 0),
                        "sample_titles": sample_titles,
                    })
                return buckets
        finally:
            set_current_workspace(previous_ws or "")

    async def get_page(
        self,
        *,
        workspace_id: str,
        slug: str,
    ) -> Optional[dict[str, Any]]:
        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        db_manager = get_async_db_manager()
        try:
            async with db_manager.session_scope() as session:
                page = await self._get_page_by_slug(
                    session, workspace_id=workspace_id, slug=slug
                )
                if page is None:
                    return None

                # 出链 / 入链：与 list_pages 口径一致，隐藏指向 archived 的链接
                # （否则会出现「右侧链接列表里有，但左侧实体 Wiki 里看不到」的错觉）
                outgoing = (
                    await session.execute(
                        select(WikiLink, WikiPage.slug, WikiPage.title)
                        .join(WikiPage, WikiPage.id == WikiLink.target_page_id)
                        .where(
                            WikiLink.workspace_id == workspace_id,
                            WikiLink.source_page_id == page.id,
                            WikiPage.status != "archived",
                        )
                    )
                ).all()

                incoming = (
                    await session.execute(
                        select(WikiLink, WikiPage.slug, WikiPage.title)
                        .join(WikiPage, WikiPage.id == WikiLink.source_page_id)
                        .where(
                            WikiLink.workspace_id == workspace_id,
                            WikiLink.target_page_id == page.id,
                            WikiPage.status != "archived",
                        )
                    )
                ).all()

                # 出处
                sources = (
                    await session.execute(
                        select(
                            *_source_select_columns(),
                        )
                        .outerjoin(File, File.id == WikiPageSource.file_id)
                        .where(
                            WikiPageSource.workspace_id == workspace_id,
                            WikiPageSource.page_id == page.id,
                        )
                    )
                ).all()

                # 修订
                merged_sources = _merge_source_rows(sources)
                source_chunk_ids = [
                    str(chunk_id)
                    for item in merged_sources
                    for chunk_id in (item.get("chunk_ids") or [])
                    if str(chunk_id).strip()
                ]
                loaded_chunks = await get_wiki_chunk_loader().load_chunks_by_ids(
                    workspace_id=workspace_id,
                    chunk_ids=source_chunk_ids,
                )
                chunk_map = {chunk.chunk_id: chunk for chunk in loaded_chunks}

                revisions = (
                    await session.execute(
                        select(WikiRevision)
                        .where(
                            WikiRevision.workspace_id == workspace_id,
                            WikiRevision.page_id == page.id,
                        )
                        .order_by(WikiRevision.version.desc())
                        .limit(20)
                    )
                ).scalars().all()

                governance = await self._build_governance_snapshot(
                    session, workspace_id=workspace_id, page=page
                )

                return {
                    "id": page.id,
                    "slug": page.slug,
                    "title": page.title,
                    "aliases": list(page.aliases or []),
                    "domain": page.domain,
                    "status": page.status,
                    "summary": page.summary,
                    "markdown_body": page.markdown_body,
                    "version": page.version,
                    "char_count": page.char_count,
                    "token_count": page.token_count,
                    "last_compiled_at": (
                        page.last_compiled_at.isoformat()
                        if page.last_compiled_at
                        else None
                    ),
                    "last_compiled_by": page.last_compiled_by,
                    "compile_meta": page.compile_meta,
                    # 同一对端 (slug) 多 link_type 去重，仅保留语义最强的一条，
                    # 避免反向链接列表中"提及"与"相关"重复展示。
                    "outgoing_links": _dedupe_links_by_peer(outgoing, direction="outgoing"),
                    "incoming_links": _dedupe_links_by_peer(incoming, direction="incoming"),
                    "sources": [
                        {
                            "id": src.id,
                            "file_id": src.file_id,
                            "file_name": item.get("file_name"),
                            "chunk_ids": list(item.get("chunk_ids") or []),
                            "evidence_chunks": [
                                {
                                    "chunk_id": str(chunk_id),
                                    "file_id": src.file_id,
                                    "file_name": item.get("file_name"),
                                    "page_number": (
                                        chunk_map[str(chunk_id)].page_numbers[0]
                                        if str(chunk_id) in chunk_map
                                        and chunk_map[str(chunk_id)].page_numbers
                                        else None
                                    ),
                                    "preview": (
                                        _chunk_preview(chunk_map[str(chunk_id)].content)
                                        if str(chunk_id) in chunk_map
                                        else ""
                                    ),
                                }
                                for chunk_id in list(item.get("chunk_ids") or [])
                            ],
                            "excerpt": item.get("excerpt"),
                            "visibility": item.get("visibility"),
                            "department_id": item.get("dept_id"),
                            "owner_id": item.get("owner_id"),
                            "document_type": item.get("document_type"),
                            "business_domain": item.get("business_domain"),
                            "confidentiality_level": item.get("confidentiality_level"),
                            "effective_from": (
                                item.get("effective_from").isoformat()
                                if item.get("effective_from")
                                else None
                            ),
                            "effective_until": (
                                item.get("effective_until").isoformat()
                                if item.get("effective_until")
                                else None
                            ),
                            "external_ref": item.get("external_ref"),
                        }
                        for item in merged_sources
                        for src in [item["src"]]
                    ],
                    "source_scope": _source_scope_summary(merged_sources),
                    "revisions": [
                        {
                            "version": r.version,
                            "committed_by": r.committed_by,
                            "commit_message": r.commit_message,
                            "committed_at": r.committed_at.isoformat()
                            if r.committed_at
                            else None,
                        }
                        for r in revisions
                    ],
                    "governance": governance,
                }
        finally:
            set_current_workspace(previous_ws or "")

    async def update_page(
        self,
        *,
        workspace_id: str,
        slug: str,
        markdown_body: Optional[str] = None,
        summary: Optional[str] = None,
        title: Optional[str] = None,
        domain: Optional[str] = None,
        status: Optional[str] = None,
        committed_by: str = "user",
        commit_message: Optional[str] = None,
    ) -> dict[str, Any]:
        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        db_manager = get_async_db_manager()
        try:
            async with db_manager.session_scope() as session:
                page = await self._get_page_by_slug(
                    session, workspace_id=workspace_id, slug=slug
                )
                if page is None:
                    return {"success": False, "error": "page_not_found"}

                old_markdown = page.markdown_body or ""
                old_summary = page.summary or ""
                changed = False
                if markdown_body is not None and markdown_body != old_markdown:
                    page.markdown_body = markdown_body
                    page.char_count = len(markdown_body)
                    changed = True
                if summary is not None and summary != old_summary:
                    page.summary = summary
                    changed = True
                if title is not None and title != page.title:
                    page.title = title
                    changed = True
                if domain is not None and domain != page.domain:
                    page.domain = domain
                    changed = True
                if status is not None and status != page.status:
                    if status in TRUSTED_WIKI_PAGE_STATUSES:
                        source_rows = (
                            await session.execute(
                                select(
                                    *_source_select_columns(),
                                )
                                .select_from(WikiPageSource)
                                .outerjoin(File, File.id == WikiPageSource.file_id)
                                .where(
                                    WikiPageSource.workspace_id == workspace_id,
                                    WikiPageSource.page_id == page.id,
                                )
                            )
                        ).all()
                        merged_source_rows = _merge_source_rows(source_rows)
                        live_source_rows = [
                            item for item in merged_source_rows if item.get("live")
                        ]
                        effective_source_rows = live_source_rows or merged_source_rows
                        if len(effective_source_rows) <= 0:
                            return {
                                "success": False,
                                "error": "wiki_page_requires_source_before_publish",
                            }
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
                        if int(conflict_count or 0) > 0:
                            return {
                                "success": False,
                                "error": "wiki_page_has_active_conflicts",
                            }
                        if any(item.get("is_deleted") for item in effective_source_rows):
                            return {
                                "success": False,
                                "error": "wiki_page_has_invalid_sources",
                            }
                        if any(
                            item.get("effective_until")
                            and item.get("effective_until") < datetime.utcnow()
                            for item in effective_source_rows
                        ):
                            return {
                                "success": False,
                                "error": "wiki_page_has_expired_sources",
                            }
                    page.status = status
                    changed = True

                if not changed:
                    return {"success": True, "changed": False}

                page.version += 1
                session.add(
                    WikiRevision(
                        id=str(uuid.uuid4()),
                        workspace_id=workspace_id,
                        page_id=page.id,
                        version=page.version,
                        committed_by=committed_by,
                        commit_message=commit_message,
                        snapshot_markdown=page.markdown_body or "",
                        snapshot_summary=page.summary,
                        diff_text=_diff(old_markdown, page.markdown_body or ""),
                    )
                )

                # 用户编辑后：重建出链（因为 markdown 可能新增 [[xx]]）
                if markdown_body is not None:
                    inline = WikiLinker.parse_inline_links(page.markdown_body or "")
                    declarations = WikiLinker.merge_link_declarations(
                        outgoing_links=[],
                        inline_slugs=inline,
                    )
                    await WikiLinker.replace_outgoing_links(
                        session=session,
                        workspace_id=workspace_id,
                        source_page_id=page.id,
                        link_declarations=declarations,
                    )

                return {"success": True, "changed": True, "version": page.version}
        finally:
            set_current_workspace(previous_ws or "")

    async def list_candidate_governance(
        self,
        *,
        workspace_id: str,
        user_id: Optional[str] = None,
        user_role: str = "user",
        user_department_id: Optional[int] = None,
        keyword: Optional[str] = None,
        statuses: Optional[list[str]] = None,
        scope: Optional[str] = None,
        department_id: Optional[int] = None,
        business_domain: Optional[str] = None,
        document_type: Optional[str] = None,
        confidentiality_level: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return candidate/draft pages ordered by governance priority.

        Scores are derived from existing evidence and telemetry so this v1 does
        not require a schema migration.
        """
        allowed_statuses = statuses or ["candidate", "draft"]
        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        try:
            async with get_async_db_manager().session_scope() as session:
                stmt = select(WikiPage).where(
                    WikiPage.workspace_id == workspace_id,
                    WikiPage.status.in_(allowed_statuses),
                )
                if keyword:
                    kw = f"%{keyword.strip()}%"
                    stmt = stmt.where(
                        or_(
                            WikiPage.title.like(kw),
                            WikiPage.slug.like(kw),
                            WikiPage.summary.like(kw),
                        )
                    )
                scope_filter = _wiki_source_exists_filter(
                    workspace_id=workspace_id,
                    scope=scope,
                    department_id=department_id,
                    user_department_id=user_department_id,
                    user_id=user_id,
                    user_role=user_role,
                    business_domain=business_domain,
                    document_type=document_type,
                    confidentiality_level=confidentiality_level,
                )
                if (scope and scope != "all") or user_role != "knowledge_manager" or any(
                    [business_domain, document_type, confidentiality_level]
                ):
                    stmt = stmt.where(scope_filter)
                total = (
                    await session.execute(select(func.count()).select_from(stmt.subquery()))
                ).scalar_one()
                pages = (
                    await session.execute(
                        stmt.order_by(WikiPage.updated_at.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                ).scalars().all()

                items: list[dict[str, Any]] = []
                for page in pages:
                    governance = await self._build_governance_snapshot(
                        session, workspace_id=workspace_id, page=page
                    )
                    source_scope = await self._build_page_source_scope(
                        session, workspace_id=workspace_id, page_id=page.id
                    )
                    items.append(
                        {
                            "id": page.id,
                            "slug": page.slug,
                            "title": page.title,
                            "summary": page.summary,
                            "domain": page.domain,
                            "status": page.status,
                            "version": page.version,
                            "char_count": page.char_count,
                            "last_compiled_at": (
                                page.last_compiled_at.isoformat()
                                if page.last_compiled_at
                                else None
                            ),
                            "updated_at": page.updated_at.isoformat()
                            if page.updated_at
                            else None,
                            "governance": governance,
                            "source_scope": source_scope,
                        }
                    )
                items.sort(
                    key=lambda item: (
                        int(item["governance"].get("score", 0)),
                        item.get("updated_at") or "",
                    ),
                    reverse=True,
                )
                return {"total": int(total or 0), "items": items}
        finally:
            set_current_workspace(previous_ws or "")

    async def apply_governance_action(
        self,
        *,
        workspace_id: str,
        slug: str,
        action: str,
        target_slug: Optional[str] = None,
        note: Optional[str] = None,
        committed_by: str = "user",
        commit_message: Optional[str] = None,
    ) -> dict[str, Any]:
        status_by_action = {
            "publish": "published",
            "verify": "verified",
            "draft": "draft",
            "archive": "archived",
            "deprecate": "deprecated",
        }
        if action in status_by_action:
            return await self.update_page(
                workspace_id=workspace_id,
                slug=slug,
                status=status_by_action[action],
                committed_by=committed_by,
                commit_message=commit_message or f"governance:{action}",
            )

        if action not in {"boost", "unboost", "mark_duplicate", "mark_conflict", "merge"}:
            return {"success": False, "error": "unsupported_governance_action"}
        if action in {"mark_duplicate", "merge"} and not target_slug:
            return {"success": False, "error": "target_slug_required"}

        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        try:
            async with get_async_db_manager().session_scope() as session:
                page = await self._get_page_by_slug(
                    session, workspace_id=workspace_id, slug=slug
                )
                if page is None:
                    return {"success": False, "error": "page_not_found"}

                meta = dict(page.compile_meta or {})
                governance = dict(meta.get("governance") or {})
                if action == "boost":
                    governance["manual_boost"] = True
                elif action == "unboost":
                    governance["manual_boost"] = False
                elif action == "mark_duplicate":
                    governance["duplicate_of"] = target_slug
                    governance["duplicate_note"] = note or ""
                elif action == "mark_conflict":
                    governance["marked_conflict"] = True
                    governance["conflict_note"] = note or ""
                elif action == "merge":
                    governance["merged_into"] = target_slug
                    governance["merge_note"] = note or ""
                    page.status = "archived"

                meta["governance"] = governance
                page.compile_meta = meta
                page.version = int(page.version or 1) + 1
                session.add(
                    WikiRevision(
                        id=str(uuid.uuid4()),
                        workspace_id=workspace_id,
                        page_id=page.id,
                        version=page.version,
                        committed_by=committed_by,
                        commit_message=commit_message or f"governance:{action}",
                        snapshot_markdown=page.markdown_body or "",
                        snapshot_summary=page.summary,
                        diff_text="",
                        extra_meta={"governance_action": action, "target_slug": target_slug, "note": note},
                    )
                )
                return {"success": True, "changed": True, "version": page.version}
        finally:
            set_current_workspace(previous_ws or "")

    async def purge_orphans_for_deleted_files(
        self,
        *,
        workspace_id: str,
        triggered_by: str = "user",
    ) -> dict[str, Any]:
        """[治理] 软删「原文件均已被删除」的孤儿实体页（status='archived'）。

        判定准则（保守策略，避免误删手工建页）：
        - page 至少绑定一条 wiki_page_sources（即由编译产生）
        - 所有绑定的 source file 都满足 (不存在 OR is_deleted=True)
        - 当前 status 不是 'archived'

        动作：把符合条件的 page 改为 archived，并清空该 page 的 wiki_revisions。
        链接 / 出处不动；历史按源文件删除治理要求清空。
        """
        from app.models.knowledge.graph import File as FileModel

        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        purged: list[dict[str, Any]] = []
        try:
            async with get_async_db_manager().session_scope() as session:
                # 1. 候选 page：有 source 且不存在任何 alive source
                any_source = (
                    select(WikiPageSource.id)
                    .where(
                        WikiPageSource.page_id == WikiPage.id,
                        WikiPageSource.workspace_id == workspace_id,
                    )
                )
                alive_source = (
                    select(WikiPageSource.id)
                    .join(FileModel, FileModel.id == WikiPageSource.file_id)
                    .where(
                        WikiPageSource.page_id == WikiPage.id,
                        WikiPageSource.workspace_id == workspace_id,
                        FileModel.is_deleted == False,  # noqa: E712
                    )
                )
                stmt = (
                    select(WikiPage)
                    .where(
                        WikiPage.workspace_id == workspace_id,
                        WikiPage.status != "archived",
                        exists(any_source),
                        ~exists(alive_source),
                    )
                )
                pages: list[WikiPage] = list(
                    (await session.execute(stmt)).scalars().all()
                )

                for page in pages:
                    # 2. 收集该页所有源文件（区分已删 / 丢失），生成审计原因
                    src_stmt = (
                        select(
                            WikiPageSource.file_id,
                            FileModel.name,
                            FileModel.is_deleted,
                        )
                        .select_from(WikiPageSource)
                        .outerjoin(FileModel, FileModel.id == WikiPageSource.file_id)
                        .where(
                            WikiPageSource.page_id == page.id,
                            WikiPageSource.workspace_id == workspace_id,
                        )
                    )
                    src_rows = (await session.execute(src_stmt)).all()

                    deleted_names: list[str] = []
                    missing_count = 0
                    for row in src_rows:
                        file_id, name, is_deleted = row
                        if name is None:
                            missing_count += 1
                        elif is_deleted:
                            deleted_names.append(str(name))
                    parts: list[str] = []
                    if deleted_names:
                        sample = "/".join(deleted_names[:3])
                        if len(deleted_names) > 3:
                            sample += f" 等 {len(deleted_names)} 份"
                        parts.append(f"已删源文件: {sample}")
                    if missing_count:
                        parts.append(f"丢失 {missing_count} 份")
                    reason = "; ".join(parts) or "所有源文件不可达"

                    previous_status = page.status
                    page.status = "archived"
                    page.version = int(page.version or 1) + 1

                    await session.execute(
                        delete(WikiRevision).where(
                            WikiRevision.workspace_id == workspace_id,
                            WikiRevision.page_id == page.id,
                        )
                    )

                    purged.append(
                        {
                            "id": page.id,
                            "slug": page.slug,
                            "title": page.title,
                            "previous_status": previous_status,
                            "reason": reason,
                        }
                    )

                logger.info(
                    "wiki purge_orphans done: workspace=%s scanned=%d purged=%d",
                    workspace_id,
                    len(pages),
                    len(purged),
                )
                return {
                    "workspace_id": workspace_id,
                    "scanned_count": len(pages),
                    "purged_count": len(purged),
                    "purged": purged,
                }
        finally:
            set_current_workspace(previous_ws or "")

    async def batch_archive_pages(
        self,
        *,
        workspace_id: str,
        slugs: Optional[list[str]] = None,
        domain: Optional[str] = None,
        triggered_by: str = "user",
    ) -> dict[str, Any]:
        """[治理] 批量归档实体页（status='archived'）。

        支持两种模式：
        - slugs: 按 slug 列表精确归档
        - domain: 归档整个域下所有非 archived 页面（仅在 slugs 为空时生效）

        动作：把符合条件的 page 改为 archived，并写一条 wiki_revisions 便于追溯。
        """
        from app.models.knowledge.graph import File as FileModel

        if not workspace_id:
            return {"success": False, "error": "workspace_id 不能为空"}

        slugs_clean: list[str] = []
        domain_clean: Optional[str] = None

        if slugs:
            slugs_clean = [s.strip().lower() for s in slugs if isinstance(s, str) and s.strip()]
            if not slugs_clean:
                return {"success": False, "error": "slugs 为空或无效"}
        elif domain:
            domain_clean = str(domain).strip().lower() or None
            if not domain_clean:
                return {"success": False, "error": "domain 为空或无效"}
        else:
            return {"success": False, "error": "必须提供 slugs 或 domain 其中之一"}

        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        archived: list[dict[str, Any]] = []
        skipped: list[str] = []
        try:
            async with get_async_db_manager().session_scope() as session:
                stmt = select(WikiPage).where(
                    WikiPage.workspace_id == workspace_id,
                    WikiPage.status != "archived",
                )
                if slugs_clean:
                    stmt = stmt.where(WikiPage.slug.in_(slugs_clean))
                elif domain_clean:
                    stmt = stmt.where(WikiPage.domain == domain_clean)

                pages: list[WikiPage] = list(
                    (await session.execute(stmt)).scalars().all()
                )

                found_slugs = {p.slug for p in pages}
                if slugs_clean:
                    skipped = [s for s in slugs_clean if s not in found_slugs]

                for page in pages:
                    previous_status = page.status
                    page.status = "archived"
                    page.version = int(page.version or 1) + 1

                    session.add(
                        WikiRevision(
                            id=str(uuid.uuid4()),
                            workspace_id=workspace_id,
                            page_id=page.id,
                            version=page.version,
                            committed_by=triggered_by,
                            commit_message="batch_archive",
                            snapshot_markdown=page.markdown_body or "",
                            snapshot_summary=page.summary,
                            diff_text="",
                        )
                    )

                    archived.append(
                        {
                            "id": page.id,
                            "slug": page.slug,
                            "title": page.title,
                            "previous_status": previous_status,
                            "reason": "batch_archive",
                        }
                    )

                logger.info(
                    "wiki batch_archive done: workspace=%s requested=%d archived=%d skipped=%d",
                    workspace_id,
                    len(slugs_clean) if slugs_clean else len(pages),
                    len(archived),
                    len(skipped),
                )
                return {
                    "workspace_id": workspace_id,
                    "requested_count": len(slugs_clean) if slugs_clean else len(pages),
                    "archived_count": len(archived),
                    "skipped_count": len(skipped),
                    "archived": archived,
                    "skipped_slugs": skipped,
                }
        finally:
            set_current_workspace(previous_ws or "")

    # ------------------------------------------------------------------
    # 内部：编译结果落库
    # ------------------------------------------------------------------

    async def _apply_outcome(
        self,
        session: AsyncSession,
        *,
        workspace_id: str,
        outcome: CompileOutcome,
        triggered_by: str,
    ) -> dict[str, Any]:
        created = 0
        updated = 0
        conflicted_links = 0
        link_total: dict[str, int] = {
            "created": 0,
            "deleted": 0,
            "broken": 0,
            "reverse_added": 0,
        }
        page_results: list[dict[str, Any]] = []

        # [M4.2] 配额运行时校验 ------------------------------------------------
        from app.config import get_settings as _get_settings
        wiki_cfg = _get_settings().wiki
        max_pages = max(1, int(wiki_cfg.max_pages_per_workspace or 500))
        max_links = max(1, int(wiki_cfg.max_links_per_page or 50))

        # 当前 published / draft 页数（archived 不算）
        current_pages_count = (
            await session.execute(
                select(func.count(WikiPage.id)).where(
                    WikiPage.workspace_id == workspace_id,
                    WikiPage.status != "archived",
                )
            )
        ).scalar_one() or 0
        current_pages_count = int(current_pages_count)

        # 一次性查所有候选 slug 在工作区是否已存在（用于判断 create vs update）
        candidate_slugs = list({c.slug for c in outcome.pages if c.slug})
        existing_slug_set: set[str] = set()
        if candidate_slugs:
            rows = (
                await session.execute(
                    select(WikiPage.slug).where(
                        WikiPage.workspace_id == workspace_id,
                        WikiPage.slug.in_(candidate_slugs),
                    )
                )
            ).all()
            existing_slug_set = {row[0] for row in rows if row[0]}

        pages_remaining = max(0, max_pages - current_pages_count)
        quota_breach = {
            "pages_dropped": 0,
            "links_dropped": 0,
            "max_pages_per_workspace": max_pages,
            "max_links_per_page": max_links,
            "current_pages_before": current_pages_count,
        }
        # ---------------------------------------------------------------------

        for compiled in outcome.pages:
            # 是否会触发 create？compiled.existing_page_id 为空且 slug 不在 workspace
            will_create = (
                not compiled.existing_page_id
                and compiled.slug not in existing_slug_set
            )

            # [M4.2] 配额命中：触发 create 但配额已满 → 截断并记录
            if will_create and pages_remaining <= 0:
                quota_breach["pages_dropped"] += 1
                outcome.error_messages.append(
                    f"slug={compiled.slug} 已超工作区上限（max_pages_per_workspace={max_pages}），跳过创建"
                )
                logger.warning(
                    "[WikiService][Quota] workspace=%s slug=%s 触达 max_pages 上限被跳过",
                    workspace_id, compiled.slug,
                )
                continue

            # [M4.2] 单页出链截断
            if compiled.outgoing_links and len(compiled.outgoing_links) > max_links:
                truncated = len(compiled.outgoing_links) - max_links
                quota_breach["links_dropped"] += truncated
                logger.warning(
                    "[WikiService][Quota] workspace=%s slug=%s outgoing_links %d -> %d (truncated %d)",
                    workspace_id, compiled.slug,
                    len(compiled.outgoing_links), max_links, truncated,
                )
                compiled.outgoing_links = compiled.outgoing_links[:max_links]

            try:
                result = await self._apply_single_page(
                    session=session,
                    workspace_id=workspace_id,
                    compiled=compiled,
                    triggered_by=triggered_by,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[WikiService] apply_single_page failed slug=%s", compiled.slug
                )
                outcome.error_messages.append(
                    f"slug={compiled.slug} 落库失败: {exc}"
                )
                continue

            if result["operation"] == "create":
                created += 1
                pages_remaining = max(0, pages_remaining - 1)
                # 同步 set，避免同批两条 candidate 都触发 create 时双计数
                existing_slug_set.add(compiled.slug)
            elif result["operation"] == "update":
                updated += 1
            for k, v in (result.get("link_stats") or {}).items():
                link_total[k] = link_total.get(k, 0) + int(v)
            conflicted_links += int(result.get("conflicts_added") or 0)
            page_results.append(result)

        return {
            "created": created,
            "updated": updated,
            "links": link_total,
            "conflicts_added": conflicted_links,
            "pages": page_results,
            # [M4.2] 配额观测，未触发时 pages_dropped/links_dropped 都为 0
            "quota_breach": quota_breach,
        }

    async def _apply_single_page(
        self,
        session: AsyncSession,
        *,
        workspace_id: str,
        compiled: CompiledPage,
        triggered_by: str,
    ) -> dict[str, Any]:
        existing: Optional[WikiPage] = None
        if compiled.existing_page_id:
            existing = (
                await session.execute(
                    select(WikiPage).where(
                        WikiPage.workspace_id == workspace_id,
                        WikiPage.id == compiled.existing_page_id,
                    )
                )
            ).scalar_one_or_none()
        if existing is None:
            # fallback：按 slug 再查一次
            existing = await self._get_page_by_slug(
                session, workspace_id=workspace_id, slug=compiled.slug
            )

        target_status = _target_status_for_compile(triggered_by)
        if (
            target_status == "candidate"
            and existing is not None
            and str(existing.status) in TRUSTED_WIKI_PAGE_STATUSES
        ):
            logger.info(
                "[WikiService] skip candidate compile for trusted wiki page: workspace=%s slug=%s status=%s",
                workspace_id,
                compiled.slug,
                existing.status,
            )
            return {
                "operation": "skipped_trusted_existing",
                "page_id": existing.id,
                "slug": existing.slug,
                "status": existing.status,
                "link_stats": {
                    "created": 0,
                    "deleted": 0,
                    "broken": 0,
                    "reverse_added": 0,
                },
                "conflicts_added": 0,
                "reason": "candidate_compile_does_not_overwrite_trusted_page",
            }

        operation = "create"
        old_markdown = ""
        old_summary = ""

        if existing is None:
            page = WikiPage(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                slug=compiled.slug,
                title=compiled.title,
                aliases=_merge_aliases(compiled.aliases),
                domain=compiled.domain,
                summary=compiled.summary or None,
                markdown_body=compiled.markdown_body,
                status=target_status,
                version=1,
                char_count=len(compiled.markdown_body or ""),
                last_compiled_at=datetime.utcnow(),
                last_compiled_by=triggered_by,
                compile_meta=compiled.compile_meta or None,
            )
            session.add(page)
            await session.flush()
        else:
            operation = "update"
            old_markdown = existing.markdown_body or ""
            old_summary = existing.summary or ""
            existing.title = compiled.title
            existing.aliases = _merge_aliases(existing.aliases, compiled.aliases)
            existing.summary = compiled.summary or existing.summary
            existing.markdown_body = compiled.markdown_body
            existing.domain = compiled.domain or existing.domain
            if str(existing.status) not in TRUSTED_WIKI_PAGE_STATUSES:
                existing.status = target_status
            existing.version = int(existing.version or 1) + 1
            existing.char_count = len(compiled.markdown_body or "")
            existing.last_compiled_at = datetime.utcnow()
            existing.last_compiled_by = triggered_by
            existing.compile_meta = compiled.compile_meta or existing.compile_meta
            page = existing

        # 写修订
        extra_meta = None
        cm = compiled.compile_meta or {}
        if cm:
            extra_meta = {
                "model": cm.get("model"),
                "prompt_tokens": cm.get("prompt_tokens"),
                "completion_tokens": cm.get("completion_tokens"),
                "total_tokens": cm.get("total_tokens"),
                "evidence_chunks": cm.get("evidence_chunks"),
                "truncated": cm.get("truncated", False),
                "candidate_merge_count": cm.get("candidate_merge_count", 1),
                "elapsed_seconds": cm.get("elapsed_seconds"),
            }
        session.add(
            WikiRevision(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                page_id=page.id,
                version=page.version,
                committed_by=triggered_by,
                commit_message=compiled.ops_log or "auto-compile",
                snapshot_markdown=compiled.markdown_body,
                snapshot_summary=compiled.summary,
                diff_text=_diff(old_markdown, compiled.markdown_body),
                extra_meta=extra_meta,
            )
        )

        # 写出处（新增 / upsert）—— 多源支持：
        # 优先用 compiled.sources_by_file（每个 file_id 写一条），
        # 兼容旧路径：没有 sources_by_file 时退回到 source_file_id 单源写入。
        sources_to_persist: dict[str, list[str]] = {}
        if compiled.sources_by_file:
            for fid, chunk_ids in compiled.sources_by_file.items():
                if not fid:
                    continue
                clean_chunks = [
                    str(cid) for cid in (chunk_ids or []) if str(cid).strip()
                ]
                if clean_chunks:
                    sources_to_persist[str(fid)] = clean_chunks
        if not sources_to_persist and compiled.source_file_id and compiled.evidence_chunk_ids:
            sources_to_persist[str(compiled.source_file_id)] = list(
                compiled.evidence_chunk_ids
            )

        for file_id, chunk_ids in sources_to_persist.items():
            existing_src = (
                await session.execute(
                    select(WikiPageSource).where(
                        WikiPageSource.workspace_id == workspace_id,
                        WikiPageSource.page_id == page.id,
                        WikiPageSource.file_id == file_id,
                    )
                )
            ).scalar_one_or_none()
            if existing_src is None:
                session.add(
                    WikiPageSource(
                        id=str(uuid.uuid4()),
                        workspace_id=workspace_id,
                        page_id=page.id,
                        file_id=file_id,
                        chunk_ids=chunk_ids,
                    )
                )
            else:
                merged_ids = list({*(existing_src.chunk_ids or []), *chunk_ids})
                existing_src.chunk_ids = merged_ids

        # 重置出链
        inline_slugs = WikiLinker.parse_inline_links(compiled.markdown_body)
        link_declarations = WikiLinker.merge_link_declarations(
            outgoing_links=compiled.outgoing_links,
            inline_slugs=inline_slugs,
        )
        link_stats = await WikiLinker.replace_outgoing_links(
            session=session,
            workspace_id=workspace_id,
            source_page_id=page.id,
            link_declarations=link_declarations,
            evidence_chunk_ids=compiled.evidence_chunk_ids,
        )

        # 写冲突
        conflicts_added = 0
        for c in compiled.contradicts:
            link_id = await WikiLinker.write_contradicts(
                session=session,
                workspace_id=workspace_id,
                source_page_id=page.id,
                target_slug=c.get("with_existing") or compiled.slug,
                new_claim=c.get("new_claim", ""),
                existing_claim=c.get("with_existing", ""),
                evidence_chunk_ids=c.get("evidence_chunk_ids") or [],
            )
            if link_id is not None:
                conflicts_added += 1

        return {
            "operation": operation,
            "page_id": page.id,
            "slug": page.slug,
            "status": page.status,
            "version": page.version,
            "link_stats": link_stats,
            "conflicts_added": conflicts_added,
            "compile_meta": compiled.compile_meta or {},
        }

    # ------------------------------------------------------------------
    # 内部：读取
    # ------------------------------------------------------------------

    async def _build_page_source_scope(
        self,
        session: AsyncSession,
        *,
        workspace_id: str,
        page_id: str,
    ) -> dict[str, Any]:
        rows = (
            await session.execute(
                select(*_source_select_columns())
                .select_from(WikiPageSource)
                .outerjoin(File, File.id == WikiPageSource.file_id)
                .where(
                    WikiPageSource.workspace_id == workspace_id,
                    WikiPageSource.page_id == page_id,
                )
            )
        ).all()
        return _source_scope_summary(_merge_source_rows(rows))

    async def _build_governance_snapshot(
        self,
        session: AsyncSession,
        *,
        workspace_id: str,
        page: WikiPage,
    ) -> dict[str, Any]:
        source_rows = (
            await session.execute(
                select(
                    *_source_select_columns(),
                )
                .select_from(WikiPageSource)
                .outerjoin(File, File.id == WikiPageSource.file_id)
                .where(
                    WikiPageSource.workspace_id == workspace_id,
                    WikiPageSource.page_id == page.id,
                )
            )
        ).all()

        merged_source_rows = _merge_source_rows(source_rows)
        live_source_rows = [item for item in merged_source_rows if item.get("live")]
        effective_source_rows = live_source_rows or merged_source_rows

        source_count = len(effective_source_rows)
        chunk_count = 0
        deleted_sources = 0
        high_value_sources = 0
        restricted_sources = 0
        expired_sources = 0
        visibility_counts: dict[str, int] = {}
        source_file_types: dict[str, int] = {}
        business_domain_counts: dict[str, int] = {}
        document_type_counts: dict[str, int] = {}
        confidentiality_counts: dict[str, int] = {}
        for item in effective_source_rows:
            chunk_count += len(item.get("chunk_ids") or [])
            name = item.get("file_name")
            file_type = item.get("file_type")
            status = item.get("status")
            is_deleted = bool(item.get("is_deleted"))
            visibility = item.get("visibility")
            effective_until = item.get("effective_until")
            if is_deleted or status == "deleted" or name is None:
                deleted_sources += 1
            if effective_until and effective_until < datetime.utcnow():
                expired_sources += 1
            vis = str(visibility or "unknown")
            visibility_counts[vis] = visibility_counts.get(vis, 0) + 1
            if vis != "public":
                restricted_sources += 1
            ft = str(file_type or "unknown").lower()
            source_file_types[ft] = source_file_types.get(ft, 0) + 1
            business_domain = str(item.get("business_domain") or "unknown")
            business_domain_counts[business_domain] = business_domain_counts.get(business_domain, 0) + 1
            document_type = str(item.get("document_type") or "unknown")
            document_type_counts[document_type] = document_type_counts.get(document_type, 0) + 1
            confidentiality = str(item.get("confidentiality_level") or "unknown")
            confidentiality_counts[confidentiality] = confidentiality_counts.get(confidentiality, 0) + 1
            haystack = f"{name or ''} {file_type or ''}".lower()
            if any(hint.lower() in haystack for hint in HIGH_VALUE_SOURCE_HINTS):
                high_value_sources += 1

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

        duplicate_count = (
            await session.execute(
                select(func.count(WikiPage.id)).where(
                    WikiPage.workspace_id == workspace_id,
                    WikiPage.id != page.id,
                    WikiPage.status != "archived",
                    or_(
                        func.lower(WikiPage.title) == str(page.title or "").lower(),
                        func.lower(WikiPage.slug) == str(page.slug or "").lower(),
                    ),
                )
            )
        ).scalar_one()

        since = datetime.utcnow() - timedelta(days=30)
        query_token = (page.title or page.slug or "")[:48]
        query_count = 0
        gap_count = 0
        if query_token:
            like_token = f"%{query_token}%"
            query_count = (
                await session.execute(
                    select(func.count(WikiRouteMetric.id)).where(
                        WikiRouteMetric.workspace_id == workspace_id,
                        WikiRouteMetric.created_at >= since,
                        WikiRouteMetric.user_query.like(like_token),
                    )
                )
            ).scalar_one()
            gap_count = (
                await session.execute(
                    select(func.count(WikiRouteMetric.id)).where(
                        WikiRouteMetric.workspace_id == workspace_id,
                        WikiRouteMetric.created_at >= since,
                        WikiRouteMetric.has_wiki_gap.is_(True),
                        WikiRouteMetric.user_query.like(like_token),
                    )
                )
            ).scalar_one()

        compile_meta = dict(page.compile_meta or {})
        governance_meta = dict(compile_meta.get("governance") or {})
        merge_count = int(compile_meta.get("candidate_merge_count") or 1)
        manual_boost = bool(governance_meta.get("manual_boost"))
        marked_conflict = bool(governance_meta.get("marked_conflict"))
        duplicate_of = governance_meta.get("duplicate_of")
        merged_into = governance_meta.get("merged_into")

        score = 20
        score += min(int(query_count or 0) * 6, 24)
        score += min(int(gap_count or 0) * 10, 30)
        score += min(max(source_count - 1, 0) * 8, 24)
        score += min(max(merge_count - 1, 0) * 8, 16)
        score += min(high_value_sources * 8, 16)
        if manual_boost:
            score += 15
        if source_count <= 0:
            score -= 25
        if source_count == 1:
            score -= 6
        score -= int(conflict_count or 0) * 30
        score -= deleted_sources * 15
        score -= expired_sources * 15
        score -= int(duplicate_count or 0) * 10
        if marked_conflict:
            score -= 20
        if duplicate_of or merged_into:
            score -= 20
        if compile_meta.get("truncated"):
            score -= 8
        final_score = _clamp_int(score)

        risks: list[str] = []
        if source_count <= 0:
            risks.append("source_missing")
        elif source_count == 1:
            risks.append("low_source_count")
        if int(conflict_count or 0) > 0 or marked_conflict:
            risks.append("active_conflict")
        if int(duplicate_count or 0) > 0 or duplicate_of:
            risks.append("duplicate_entity")
        if deleted_sources > 0:
            risks.append("source_deleted")
        if expired_sources > 0:
            risks.append("source_expired")
        if compile_meta.get("truncated"):
            risks.append("content_truncated")
        if restricted_sources > 0:
            risks.append("restricted_source_scope")
        if merged_into:
            risks.append("merged_candidate")

        can_publish = (
            source_count > 0
            and int(conflict_count or 0) == 0
            and deleted_sources == 0
            and expired_sources == 0
            and not marked_conflict
            and not duplicate_of
            and not merged_into
        )
        publish_blockers: list[str] = []
        if source_count <= 0:
            publish_blockers.append("source_missing")
        if int(conflict_count or 0) > 0 or marked_conflict:
            publish_blockers.append("active_conflict")
        if deleted_sources > 0:
            publish_blockers.append("source_deleted")
        if expired_sources > 0:
            publish_blockers.append("source_expired")
        if duplicate_of:
            publish_blockers.append("duplicate_entity")
        if merged_into:
            publish_blockers.append("merged_candidate")

        return {
            "score": final_score,
            "priority": _priority_from_score(final_score),
            "signals": {
                "query_frequency_30d": int(query_count or 0),
                "wiki_gap_30d": int(gap_count or 0),
                "source_count": source_count,
                "chunk_count": chunk_count,
                "cross_document": source_count > 1,
                "candidate_merge_count": merge_count,
                "high_value_source_count": high_value_sources,
                "trigger_type": _governance_trigger_type(page.last_compiled_by),
                "manual_boost": manual_boost,
            },
            "risks": risks,
            "can_publish": can_publish,
            "publish_blockers": publish_blockers,
            "source_security": {
                "visibility_counts": visibility_counts,
                "restricted_source_count": restricted_sources,
                "file_types": source_file_types,
                "business_domain_counts": business_domain_counts,
                "document_type_counts": document_type_counts,
                "confidentiality_counts": confidentiality_counts,
                "expired_source_count": expired_sources,
            },
            "metadata_scope": _source_scope_summary(effective_source_rows),
            "manual_flags": governance_meta,
        }

    async def _fetch_existing_pages(
        self,
        session: AsyncSession,
        *,
        workspace_id: str,
        limit: int = 1000,
    ) -> list[WikiPage]:
        stmt = (
            select(WikiPage)
            .where(WikiPage.workspace_id == workspace_id)
            .order_by(WikiPage.updated_at.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())

    async def _fetch_file_payloads(
        self,
        session: AsyncSession,
        *,
        workspace_id: str,
        file_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not file_ids:
            return []
        with bypass_tenant_filter():
            stmt = (
                select(File, Folder.name)
                .outerjoin(Folder, Folder.id == File.folder_id)
                .where(
                    File.workspace_id == workspace_id,
                    File.id.in_(file_ids),
                    File.is_deleted.is_(False),
                )
            )
            rows = (await session.execute(stmt)).all()
        payloads: list[dict[str, Any]] = []
        for row in rows:
            file_obj: File = row[0]
            folder_name = row[1]
            payloads.append(
                {
                    "file_id": file_obj.id,
                    "name": file_obj.name,
                    "folder_name": folder_name or "",
                    "domain_hint": "",
                    "file_type": file_obj.file_type,
                }
            )
        return payloads

    @staticmethod
    async def _get_page_by_slug(
        session: AsyncSession,
        *,
        workspace_id: str,
        slug: str,
    ) -> Optional[WikiPage]:
        slug_norm = normalize_slug(slug)
        if not slug_norm:
            return None
        return (
            await session.execute(
                select(WikiPage).where(
                    WikiPage.workspace_id == workspace_id,
                    WikiPage.slug == slug_norm,
                )
            )
        ).scalar_one_or_none()


_wiki_service: Optional[WikiService] = None


def get_wiki_service() -> WikiService:
    global _wiki_service
    if _wiki_service is None:
        _wiki_service = WikiService()
    return _wiki_service
