"""WikiLinker - 双向链接解析与维护。

负责两件事：
1. **从 Markdown 中解析 [[slug-or-title]] 标记**：与 LLM 显式给出的 outgoing_links
   合并去重，得到一份"声明的链接清单"
2. **写入数据库 + 反向链接补全**：把声明的链接落库，并在目标页存在时同步建立
   反向（仅 `mentions` / `related` 两类做隐式反向，`supersedes` / `contradicts`
   保留单向语义）

设计原则：
- 与 SQLAlchemy session 解耦：所有 IO 操作走传入的 session
- 不在这里启事务，事务由 WikiService 顶层把控
- 不直接调 LLM；输入完全由 WikiCompiler 的 CompiledPage 提供
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.wiki.slug import normalize_slug
from app.models.wiki.wiki_link import WikiLink
from app.models.wiki.wiki_page import WikiPage

logger = logging.getLogger("wiki.linker")

# [[xxx]] 或 [[xxx|展示文本]]，xxx 可为 slug、title 或别名
_WIKI_LINK_PATTERN = re.compile(r"\[\[([^\[\]\|\n]{1,128})(?:\|[^\]]+)?\]\]")

_BIDIRECTIONAL_TYPES = {"mentions", "related"}
_VALID_TYPES = {"mentions", "related", "supersedes", "contradicts"}


@dataclass
class ResolvedLink:
    target_page_id: str
    target_slug: str
    link_type: str
    note: str = ""
    evidence_chunk_ids: list[str] | None = None
    status: str = "active"


class WikiLinker:
    """实体页双向链接管理器。"""

    @staticmethod
    def parse_inline_links(markdown_body: str) -> list[str]:
        """从 Markdown 中提取所有 [[xxx]] 标记，返回去重后的标准化 slug 列表。"""
        if not markdown_body:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for match in _WIKI_LINK_PATTERN.finditer(markdown_body):
            raw = match.group(1).strip()
            slug = normalize_slug(raw)
            if slug and slug not in seen:
                seen.add(slug)
                out.append(slug)
        return out

    @staticmethod
    def merge_link_declarations(
        *,
        outgoing_links: list[dict],
        inline_slugs: list[str],
    ) -> list[dict]:
        """合并 LLM 显式 outgoing_links 与 markdown 内联 [[slug]] 引用。"""
        merged: dict[tuple[str, str], dict] = {}

        # 显式声明优先（带 link_type / note）
        for item in outgoing_links or []:
            slug = normalize_slug(str(item.get("target_slug") or ""))
            if not slug:
                continue
            link_type = str(item.get("link_type") or "mentions").lower()
            if link_type not in _VALID_TYPES:
                link_type = "mentions"
            merged[(slug, link_type)] = {
                "target_slug": slug,
                "link_type": link_type,
                "note": str(item.get("note") or "")[:512],
            }

        # 内联引用：默认 mentions
        for slug in inline_slugs:
            key = (slug, "mentions")
            if key not in merged:
                merged[key] = {
                    "target_slug": slug,
                    "link_type": "mentions",
                    "note": "from-markdown-inline",
                }

        return list(merged.values())

    # ------------------------------------------------------------------
    # 数据库操作
    # ------------------------------------------------------------------

    @staticmethod
    async def resolve_targets(
        session: AsyncSession,
        *,
        workspace_id: str,
        target_slugs: Iterable[str],
    ) -> dict[str, str]:
        """把目标 slug 列表解析为 {slug: page_id} 映射。

        slug 不存在时不会报错，仅缺省该项（链接将以 broken 状态搁置）。
        """
        slugs = [normalize_slug(s) for s in target_slugs if normalize_slug(s)]
        if not slugs:
            return {}
        stmt = select(WikiPage.slug, WikiPage.id).where(
            WikiPage.workspace_id == workspace_id,
            WikiPage.slug.in_(slugs),
        )
        result = await session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    @staticmethod
    async def replace_outgoing_links(
        session: AsyncSession,
        *,
        workspace_id: str,
        source_page_id: str,
        link_declarations: list[dict],
        evidence_chunk_ids: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """对单个源页，重置其全部 outgoing 链接。

        步骤：
        1. 解析 target_slug → page_id（缺失的归为 broken）
        2. 删除 source_page_id 当前的全部 outgoing 链接（mentions/related/...）
        3. 批量插入新链接
        4. 对 _BIDIRECTIONAL_TYPES 类型链接，确保反向 mentions 链接存在

        返回统计：{created: int, deleted: int, broken: int, reverse_added: int}
        """
        slug_to_id = await WikiLinker.resolve_targets(
            session,
            workspace_id=workspace_id,
            target_slugs=[d["target_slug"] for d in link_declarations],
        )

        # 删除当前所有以 source 为起点的链接（不区分类型）
        del_result = await session.execute(
            delete(WikiLink).where(
                and_(
                    WikiLink.workspace_id == workspace_id,
                    WikiLink.source_page_id == source_page_id,
                )
            )
        )
        deleted_count = int(del_result.rowcount or 0)

        created = 0
        broken = 0
        reverse_added = 0
        seen_keys: set[tuple[str, str, str]] = set()
        # 反向补链同样需要在 session 内去重：
        # autoflush=False 时，多次 select 看不到 pending 的 INSERT，
        # 必须靠本地集合避免重复 add，否则 flush 会触发唯一键冲突。
        reverse_seen_targets: set[str] = set()

        for decl in link_declarations:
            target_slug = decl["target_slug"]
            target_id = slug_to_id.get(target_slug)
            if target_id is None:
                broken += 1
                continue
            link_type = decl.get("link_type", "mentions")
            key = (source_page_id, target_id, link_type)
            if key in seen_keys or source_page_id == target_id:
                # 防止自环或重复
                continue
            seen_keys.add(key)
            session.add(
                WikiLink(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    source_page_id=source_page_id,
                    target_page_id=target_id,
                    link_type=link_type,
                    note=decl.get("note") or None,
                    evidence_chunk_ids=evidence_chunk_ids or None,
                    confidence=1.0,
                    status="active",
                )
            )
            created += 1

            if link_type in _BIDIRECTIONAL_TYPES:
                # 同一 source 在本次调用中已为 target 补过反向 mentions，则跳过
                if target_id in reverse_seen_targets:
                    continue
                # 反向：保证 target → source 也存在 mentions 链接（不重复）
                exists_stmt = select(WikiLink.id).where(
                    WikiLink.workspace_id == workspace_id,
                    WikiLink.source_page_id == target_id,
                    WikiLink.target_page_id == source_page_id,
                    WikiLink.link_type == "mentions",
                )
                exists = (await session.execute(exists_stmt)).scalar_one_or_none()
                if exists is None:
                    session.add(
                        WikiLink(
                            id=str(uuid.uuid4()),
                            workspace_id=workspace_id,
                            source_page_id=target_id,
                            target_page_id=source_page_id,
                            link_type="mentions",
                            note="auto-reverse",
                            confidence=0.8,
                            status="active",
                        )
                    )
                    reverse_added += 1
                reverse_seen_targets.add(target_id)

        await session.flush()
        return {
            "created": created,
            "deleted": deleted_count,
            "broken": broken,
            "reverse_added": reverse_added,
        }

    @staticmethod
    async def write_contradicts(
        session: AsyncSession,
        *,
        workspace_id: str,
        source_page_id: str,
        target_slug: str,
        new_claim: str,
        existing_claim: str,
        evidence_chunk_ids: Optional[list[str]] = None,
    ) -> Optional[str]:
        """记录单条冲突链接，状态 pending_review。

        若目标 slug 不存在则返回 None；否则返回 wiki_link.id。
        """
        slug = normalize_slug(target_slug)
        if not slug:
            return None
        target_id = (
            await session.execute(
                select(WikiPage.id).where(
                    WikiPage.workspace_id == workspace_id,
                    WikiPage.slug == slug,
                )
            )
        ).scalar_one_or_none()
        if target_id is None:
            return None

        link_id = str(uuid.uuid4())
        note = f"new_claim={new_claim[:200]} | existing_claim={existing_claim[:200]}"
        session.add(
            WikiLink(
                id=link_id,
                workspace_id=workspace_id,
                source_page_id=source_page_id,
                target_page_id=target_id,
                link_type="contradicts",
                note=note[:512],
                evidence_chunk_ids=evidence_chunk_ids or None,
                confidence=0.6,
                status="pending_review",
            )
        )
        await session.flush()
        return link_id


_linker: Optional[WikiLinker] = None


def get_wiki_linker() -> WikiLinker:
    global _linker
    if _linker is None:
        _linker = WikiLinker()
    return _linker
