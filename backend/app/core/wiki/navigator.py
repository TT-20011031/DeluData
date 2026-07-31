"""WikiNavigator - Wiki-First 检索路径的"导航式装载"。

职责（M3.2）：
1. **INDEX 构建**：从 wiki_pages 拉取工作区全部实体页，按 domain 分组，组装精简索引
   （slug + title + summary 截断），用于 LLM 选页
2. **LLM 选页**：把 INDEX 喂给 fast 模型，结构化输出"最多 N 个相关 slug"
3. **整页装载**：拉选中页的完整 Markdown + 一阶 incoming 链接（仅 title+slug，避免膨胀）
4. **输出适配**：包装为 DocumentChunk（与 RAG 路径同形），通过 metadata.kind="wiki_page"
   让下游识别

设计原则：
- 与 WikiService 解耦：直接走 ORM 查询，不复用 service 的事务包装（避免双重 session）
- 多租户安全：所有查询都带 workspace_id，统一通过 set_current_workspace 兜底
- 失败软降级：DB 异常 / 索引为空 / LLM 失败时返回空列表，让上游回退到 RAG
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.core.db.tenant_mixin import (
    get_current_workspace,
    set_current_workspace,
)
from app.core.llm.async_llm import get_async_llm
from app.core.security.data_scope import build_visibility_where_clause
from app.models.common.execution import DocumentChunk
from app.models.knowledge.graph import File
from app.models.wiki.wiki_link import WikiLink
from app.models.wiki.wiki_page import WikiPage
from app.models.wiki.wiki_page_source import WikiPageSource

logger = logging.getLogger(__name__)

TRUSTED_WIKI_PAGE_STATUSES = ("published", "verified")


# ============ LLM 选页结构化 Schema ============

def _build_select_tool_schema(max_pages: int) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "select_wiki_pages",
            "description": (
                "从给定 INDEX 中选出最适合回答用户问题的实体页 slug。"
                f"最多返回 {max_pages} 个。完全不相关时返回空列表。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "selected_slugs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": max_pages,
                        "description": "按相关度从高到低排序的 slug 列表",
                    },
                    "reason": {
                        "type": "string",
                        "description": "选页依据的一句话说明（中文）",
                    },
                },
                "required": ["selected_slugs", "reason"],
                "additionalProperties": False,
            },
        },
    }


_SELECT_SYSTEM_PROMPT = """\
你是 Wiki 知识库的"导航员"。下面是当前工作区的实体页索引（已按 domain 分组）：

----- INDEX BEGIN -----
{index_text}
----- INDEX END -----

请仅从上面的 INDEX 中挑选最多 {max_pages} 个最可能回答用户问题的实体页 slug。
要求：
- 只选 INDEX 中真实存在的 slug，不要生造或拼写错误。
- 优先选择标题/摘要直接相关的页面；不确定时宁缺毋滥。
- 若没有任何相关页，返回空列表。
- reason 字段用一句中文说明你为何挑这几页。
"""


# ============ INDEX 构建（纯函数，可独立单测） ============

def _format_index_text(
    pages: list[dict[str, Any]],
    summary_max_chars: int,
) -> str:
    """把页列表组装为 LLM 可读的精简 INDEX 文本（按 domain 分组）。"""
    if not pages:
        return "(空：当前工作区还没有任何实体页)"

    grouped: dict[str, list[dict[str, Any]]] = {}
    for p in pages:
        domain = (p.get("domain") or "general").strip().lower() or "general"
        grouped.setdefault(domain, []).append(p)

    lines: list[str] = []
    for domain in sorted(grouped.keys()):
        lines.append(f"[{domain.upper()}]")
        for p in grouped[domain]:
            slug = p.get("slug") or ""
            title = (p.get("title") or "").strip()
            summary = (p.get("summary") or "").strip().replace("\n", " ")
            if summary_max_chars > 0 and len(summary) > summary_max_chars:
                summary = summary[: summary_max_chars - 1] + "…"
            if summary:
                lines.append(f"- {slug}: {title} — {summary}")
            else:
                lines.append(f"- {slug}: {title}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ============ Navigator 主体 ============

class WikiNavigator:
    """Wiki-First 路径的"导航 + 装载"主类。"""

    @staticmethod
    def _normalize_source_scope(
        *,
        file_ids: Optional[list[str]] = None,
        visibilities: Optional[list[str]] = None,
        dept_ids: Optional[list[str]] = None,
        business_domain: Optional[str] = None,
        document_type: Optional[str] = None,
        confidentiality_level: Optional[str] = None,
        user_context: Optional[Any] = None,
    ) -> dict[str, Any]:
        def _clean_list(values: Optional[list[str]]) -> list[str]:
            result: list[str] = []
            seen: set[str] = set()
            for value in values or []:
                text = str(value or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                result.append(text)
            return result

        return {
            "file_ids": _clean_list(file_ids),
            "visibilities": _clean_list(visibilities),
            "dept_ids": _clean_list(dept_ids),
            "business_domain": str(business_domain or "").strip(),
            "document_type": str(document_type or "").strip(),
            "confidentiality_level": str(confidentiality_level or "").strip(),
            "user_context": user_context,
        }

    @staticmethod
    def _has_source_scope(source_scope: Optional[dict[str, Any]]) -> bool:
        if not source_scope:
            return False
        user_context = source_scope.get("user_context")
        if user_context is not None and not any(
            code in getattr(user_context, "capabilities", []) for code in ("*", "knowledge:manage")
        ):
            return True
        return any(
            bool(source_scope.get(key))
            for key in (
                "file_ids",
                "visibilities",
                "dept_ids",
                "business_domain",
                "document_type",
                "confidentiality_level",
            )
        )

    @staticmethod
    def _source_scope_conditions(source_scope: dict[str, Any]) -> list[Any]:
        conditions: list[Any] = [File.is_deleted.is_(False)]
        file_ids = source_scope.get("file_ids") or []
        visibilities = source_scope.get("visibilities") or []
        dept_ids = source_scope.get("dept_ids") or []
        user_context = source_scope.get("user_context")
        business_domain = source_scope.get("business_domain")
        document_type = source_scope.get("document_type")
        confidentiality_level = source_scope.get("confidentiality_level")

        if file_ids:
            conditions.append(File.id.in_(file_ids))
        if user_context is not None and not any(
            code in getattr(user_context, "capabilities", []) for code in ("*", "knowledge:manage")
        ):
            permission_clause = build_visibility_where_clause(
                File,
                user_context=user_context,
                visibilities=visibilities or None,
                dept_ids=dept_ids or None,
            )
            if permission_clause is not None:
                conditions.append(permission_clause)
        else:
            if visibilities:
                conditions.append(File.visibility.in_(visibilities))
            if dept_ids:
                numeric_dept_ids = [int(d) for d in dept_ids if str(d).isdigit()]
                if numeric_dept_ids:
                    conditions.append(
                        or_(
                            File.visibility != "dept",
                            File.dept_id.in_(numeric_dept_ids),
                        )
                    )
        if business_domain:
            conditions.append(File.business_domain == business_domain)
        if document_type:
            conditions.append(File.document_type == document_type)
        if confidentiality_level:
            conditions.append(File.confidentiality_level == confidentiality_level)
        return conditions

    async def navigate(
        self,
        *,
        workspace_id: str,
        query: str,
        max_pages: Optional[int] = None,
        per_page_max_chars: Optional[int] = None,
        domains: Optional[list[str]] = None,
        wiki_slugs: Optional[list[str]] = None,
        file_ids: Optional[list[str]] = None,
        visibilities: Optional[list[str]] = None,
        dept_ids: Optional[list[str]] = None,
        business_domain: Optional[str] = None,
        document_type: Optional[str] = None,
        confidentiality_level: Optional[str] = None,
        user_context: Optional[Any] = None,
    ) -> list[DocumentChunk]:
        """
        Wiki 导航主入口。

        Args:
            workspace_id: 租户 / 工作区 ID
            query: 用户原始问题
            max_pages: 最多装载几页（None 时使用 settings.wiki.index_load_top_k）
            per_page_max_chars: 单页 Markdown 截断长度
            domains: [M3.5] 限定装载的实体页所属域；空列表 / None 表示不限制
            wiki_slugs: [M3.5] 用户显式指定的实体页 slug；非空时跳过 LLM 选页直接装载

        Returns:
            DocumentChunk 列表（按相关度排序，已截断）；INDEX 为空 / LLM 失败时返回 []。
        """
        if not workspace_id:
            logger.warning("[WikiNavigator] workspace_id 为空，跳过")
            return []

        settings = get_settings()
        wiki_cfg = settings.wiki
        eff_max_pages = int(max_pages or wiki_cfg.index_load_top_k or 5)
        eff_max_pages = max(1, min(eff_max_pages, 20))
        # 单页正文上限：默认 3000 字（KISS），不超过工作区配置的 max_page_chars
        default_per_page = 3000
        eff_per_page = int(per_page_max_chars or default_per_page)
        eff_per_page = max(500, min(eff_per_page, int(wiki_cfg.max_page_chars or default_per_page)))

        # 范围过滤参数清洗
        domain_filter = [d.strip() for d in (domains or []) if isinstance(d, str) and d.strip()]
        slug_filter = [s.strip().lower() for s in (wiki_slugs or []) if isinstance(s, str) and s.strip()]
        source_scope = self._normalize_source_scope(
            file_ids=file_ids,
            visibilities=visibilities,
            dept_ids=dept_ids,
            business_domain=business_domain,
            document_type=document_type,
            confidentiality_level=confidentiality_level,
            user_context=user_context,
        )

        previous_ws = get_current_workspace()
        set_current_workspace(workspace_id)
        db_manager = get_async_db_manager()
        try:
            async with db_manager.session_scope() as session:
                # [M3.5] 显式 wiki_slugs 路径：跳过 LLM 选页，直接装载（最多 eff_max_pages）
                if slug_filter:
                    direct_slugs = slug_filter[:eff_max_pages]
                    chunks = await self._load_pages_as_chunks(
                        session,
                        workspace_id=workspace_id,
                        slugs=direct_slugs,
                        per_page_max_chars=eff_per_page,
                        source_scope=source_scope,
                    )
                    logger.info(
                        "[WikiNavigator] 显式 slug 装载 %d 页 workspace=%s slugs=%s",
                        len(chunks), workspace_id, direct_slugs,
                    )
                    return chunks

                # 1. 构建 INDEX（可按 domains 收窄）
                index_pages = await self._fetch_index_pages(
                    session, workspace_id, domains=domain_filter, source_scope=source_scope
                )
                if not index_pages:
                    logger.info(
                        "[WikiNavigator] workspace=%s 无可用 wiki_pages（domains=%s），跳过",
                        workspace_id, domain_filter,
                    )
                    return []

                index_text = _format_index_text(
                    index_pages,
                    summary_max_chars=int(wiki_cfg.index_summary_max_chars or 80),
                )

                # 2. LLM 选页
                selected_slugs = await self._select_slugs_via_llm(
                    query=query,
                    index_text=index_text,
                    max_pages=eff_max_pages,
                )
                if not selected_slugs:
                    logger.info(
                        "[WikiNavigator] LLM 未选中任何 slug，workspace=%s query=%s",
                        workspace_id, query[:60],
                    )
                    return []

                # 3. 装载选中页（保持顺序：按 LLM 给出的相关度从高到低）
                chunks = await self._load_pages_as_chunks(
                    session,
                    workspace_id=workspace_id,
                    slugs=selected_slugs,
                    per_page_max_chars=eff_per_page,
                    source_scope=source_scope,
                )
                logger.info(
                    "[WikiNavigator] 已装载 %d 页 workspace=%s slugs=%s",
                    len(chunks), workspace_id, selected_slugs,
                )
                return chunks
        except Exception as exc:  # noqa: BLE001
            logger.exception("[WikiNavigator] 导航失败: %s", exc)
            return []
        finally:
            set_current_workspace(previous_ws or "")

    # ------------------------------------------------------------------
    # 内部步骤 1：拉 INDEX 用的精简页列表
    # ------------------------------------------------------------------

    async def _fetch_index_pages(
        self,
        session: AsyncSession,
        workspace_id: str,
        *,
        domains: Optional[list[str]] = None,
        source_scope: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """拉取工作区 published 实体页的精简字段。

        Args:
            domains: [M3.5] 非空时仅返回这些 domain 下的实体页（in 过滤）。
        """
        stmt = (
            select(
                WikiPage.id,
                WikiPage.slug,
                WikiPage.title,
                WikiPage.summary,
                WikiPage.domain,
            )
            .where(WikiPage.workspace_id == workspace_id)
            .where(WikiPage.status.in_(TRUSTED_WIKI_PAGE_STATUSES))
            .order_by(WikiPage.domain.asc(), WikiPage.title.asc())
        )
        if domains:
            stmt = stmt.where(WikiPage.domain.in_(domains))
        if self._has_source_scope(source_scope):
            conditions = self._source_scope_conditions(source_scope or {})
            stmt = (
                stmt.join(
                    WikiPageSource,
                    and_(
                        WikiPageSource.page_id == WikiPage.id,
                        WikiPageSource.workspace_id == workspace_id,
                    ),
                )
                .join(File, File.id == WikiPageSource.file_id)
                .where(*conditions)
                .distinct()
            )
        rows = (await session.execute(stmt)).all()
        return [
            {
                "id": r.id,
                "slug": r.slug,
                "title": r.title,
                "summary": r.summary or "",
                "domain": r.domain or "general",
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 内部步骤 2：LLM 选页
    # ------------------------------------------------------------------

    async def _select_slugs_via_llm(
        self,
        *,
        query: str,
        index_text: str,
        max_pages: int,
    ) -> list[str]:
        """调用 fast 模型从 INDEX 中挑选 slug。失败时返回空列表。"""
        settings = get_settings()
        wiki_cfg = settings.wiki
        try:
            llm = get_async_llm()
            parsed = await llm.generate_structured(
                messages=[
                    {
                        "role": "system",
                        "content": _SELECT_SYSTEM_PROMPT.format(
                            index_text=index_text,
                            max_pages=max_pages,
                        ),
                    },
                    {"role": "user", "content": f"用户问题：{query}"},
                ],
                tool_schema=_build_select_tool_schema(max_pages),
                model=wiki_cfg.router_model or settings.llm.fast_model,
                temperature=0.0,
                max_tokens=200,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[WikiNavigator] LLM 选页失败: %s", exc)
            return []

        if not isinstance(parsed, dict):
            return []
        slugs = parsed.get("selected_slugs") or []
        if not isinstance(slugs, list):
            return []
        # 清洗 + 去重 + 截断
        cleaned: list[str] = []
        seen: set[str] = set()
        for s in slugs:
            if not isinstance(s, str):
                continue
            s = s.strip().lower()
            if not s or s in seen:
                continue
            seen.add(s)
            cleaned.append(s)
            if len(cleaned) >= max_pages:
                break
        return cleaned

    # ------------------------------------------------------------------
    # 内部步骤 3：装载选中页（含一阶 incoming 链接 + 出处文件名）
    # ------------------------------------------------------------------

    async def _load_pages_as_chunks(
        self,
        session: AsyncSession,
        *,
        workspace_id: str,
        slugs: list[str],
        per_page_max_chars: int,
        source_scope: Optional[dict[str, Any]] = None,
    ) -> list[DocumentChunk]:
        """把选中 slug 的页装载为 DocumentChunk 列表，按 slugs 顺序返回。"""
        if not slugs:
            return []

        # 主页查询
        page_rows = (
            await session.execute(
                select(WikiPage).where(
                    WikiPage.workspace_id == workspace_id,
                    WikiPage.slug.in_(slugs),
                    WikiPage.status.in_(TRUSTED_WIKI_PAGE_STATUSES),
                )
            )
        ).scalars().all()
        page_by_slug: dict[str, WikiPage] = {p.slug: p for p in page_rows}

        # 入链（其他页 → 当前页）：仅取 title + slug
        page_ids = [p.id for p in page_rows]
        incoming_by_target: dict[str, list[dict[str, str]]] = {}
        if page_ids:
            inc_stmt = (
                select(
                    WikiLink.target_page_id,
                    WikiPage.slug,
                    WikiPage.title,
                )
                .join(WikiPage, WikiPage.id == WikiLink.source_page_id)
                .where(
                    WikiLink.workspace_id == workspace_id,
                    WikiLink.target_page_id.in_(page_ids),
                    WikiLink.status == "active",
                    WikiPage.status.in_(TRUSTED_WIKI_PAGE_STATUSES),
                )
            )
            for target_id, src_slug, src_title in (await session.execute(inc_stmt)).all():
                incoming_by_target.setdefault(target_id, []).append(
                    {"slug": src_slug, "title": src_title}
                )

        # 出处文件名（每页关联的原始文件）
        sources_by_page: dict[str, list[str]] = {}
        if page_ids:
            src_stmt = (
                select(WikiPageSource.page_id, File.name)
                .join(File, File.id == WikiPageSource.file_id)
                .where(
                    WikiPageSource.workspace_id == workspace_id,
                    WikiPageSource.page_id.in_(page_ids),
                    File.is_deleted.is_(False),
                )
            )
            if self._has_source_scope(source_scope):
                src_stmt = src_stmt.where(*self._source_scope_conditions(source_scope or {}))
            for page_id, file_name in (await session.execute(src_stmt)).all():
                if not file_name:
                    continue
                lst = sources_by_page.setdefault(page_id, [])
                if file_name not in lst:
                    lst.append(file_name)

        if self._has_source_scope(source_scope):
            allowed_page_ids = set(sources_by_page.keys())
            page_by_slug = {
                slug: page
                for slug, page in page_by_slug.items()
                if page.id in allowed_page_ids
            }

        # 按 LLM 给出的 slug 顺序（相关度顺序）输出，跳过 DB 不存在的 slug
        chunks: list[DocumentChunk] = []
        total = len(slugs)
        for idx, slug in enumerate(slugs):
            page = page_by_slug.get(slug)
            if page is None:
                logger.info("[WikiNavigator] slug=%s 在 DB 中找不到，跳过", slug)
                continue

            # 截断 markdown
            body = (page.markdown_body or "").lstrip()
            truncated = False
            if per_page_max_chars > 0 and len(body) > per_page_max_chars:
                body = body[: per_page_max_chars - 1] + "…"
                truncated = True

            incoming = incoming_by_target.get(page.id, [])
            sources = sources_by_page.get(page.id, [])

            # 装载策略：信任 WikiCompiler 编译产物本身已含 H1+摘要+结构化属性，
            # navigator 不再前置注入 title/summary，避免重复（title 信息已通过 metadata 暴露给下游）。
            content_parts: list[str] = [body]
            if incoming:
                links_str = ", ".join(
                    f"[[{lk['slug']}|{lk['title']}]]" for lk in incoming[:20]
                )
                content_parts.append("")
                content_parts.append(f"**相关条目（被引用于）**: {links_str}")
            if sources:
                content_parts.append("")
                content_parts.append(f"**出处文件**: {', '.join(sources[:10])}")

            content = "\n".join(content_parts).strip()

            # 相关度分数：按 LLM 排序逐位递减（主要为了下游排序，绝对值不重要）
            score = max(0.5, 1.0 - 0.05 * idx)

            chunks.append(
                DocumentChunk(
                    content=content,
                    source_file=f"wiki:{slug}",
                    page_number=None,
                    chunk_id=f"wiki_page:{page.id}",
                    score=score,
                    metadata={
                        "kind": "wiki_page",
                        "page_id": page.id,
                        "slug": slug,
                        "title": page.title,
                        "domain": page.domain,
                        "status": page.status,
                        "version": page.version,
                        "incoming_links": incoming[:20],
                        "source_files": sources[:10],
                        "truncated": truncated,
                        "llm_rank": idx,
                        "llm_total_selected": total,
                    },
                    summary=page.summary,
                    header_path=f"Wiki / {page.domain or 'general'}",
                    rerank_score=score,
                )
            )
        return chunks


# ============ 工厂函数 ============

_navigator_singleton: Optional[WikiNavigator] = None


def get_wiki_navigator() -> WikiNavigator:
    """获取 WikiNavigator 单例。"""
    global _navigator_singleton
    if _navigator_singleton is None:
        _navigator_singleton = WikiNavigator()
    return _navigator_singleton
