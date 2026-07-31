"""WikiLinter - 健康度巡检（规则优先）。

四类问题：
1. **孤儿页（orphan）**：没有任何入链的 published 页（且建立时间已过 grace_days）
2. **断链（broken_link）**：实际不会发生（FK 已保证），但保留对 markdown 内 [[xx]]
   引用了不存在 slug 的检查
3. **冲突未决（pending_conflict）**：link_type='contradicts' 且 status='pending_review'
   超过 conflict_open_threshold 项时升级告警
4. **过期（stale）**：last_compiled_at 超过 stale_days 未更新

设计原则：
- 报告对象是结构化字典（issues），调用方决定是否落库 / 暴露给前端
- LLM 不是必须：默认仅返回规则报告；调用方按需触发 lint_audit prompt 给修复建议
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.wiki.linker import WikiLinker
from app.core.wiki.slug import normalize_slug
from app.models.wiki.wiki_link import WikiLink
from app.models.wiki.wiki_page import WikiPage

logger = logging.getLogger("wiki.linter")


# =============================================================================
# 数据结构
# =============================================================================

ISSUE_ORPHAN = "orphan"
ISSUE_BROKEN_LINK = "broken_link"
ISSUE_OPEN_CONFLICT = "open_conflict"
ISSUE_STALE = "stale"


@dataclass
class LintIssue:
    issue_id: str
    issue_type: str
    severity: str  # low | medium | high
    page_id: Optional[str] = None
    page_slug: Optional[str] = None
    page_title: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class LintReport:
    workspace_id: str
    generated_at: datetime
    page_count: int = 0
    link_count: int = 0
    open_conflicts: int = 0
    issues: list[LintIssue] = field(default_factory=list)

    def by_type(self) -> dict[str, list[LintIssue]]:
        out: dict[str, list[LintIssue]] = {}
        for issue in self.issues:
            out.setdefault(issue.issue_type, []).append(issue)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "generated_at": self.generated_at.isoformat(),
            "page_count": self.page_count,
            "link_count": self.link_count,
            "open_conflicts": self.open_conflicts,
            "issues": [
                {
                    "issue_id": i.issue_id,
                    "issue_type": i.issue_type,
                    "severity": i.severity,
                    "page_id": i.page_id,
                    "page_slug": i.page_slug,
                    "page_title": i.page_title,
                    "payload": i.payload,
                }
                for i in self.issues
            ],
        }


# =============================================================================
# WikiLinter
# =============================================================================


class WikiLinter:
    """健康度巡检器（纯规则）。"""

    def __init__(self) -> None:
        self._wiki_settings = get_settings().wiki

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def run(
        self,
        session: AsyncSession,
        *,
        workspace_id: str,
        max_issues_per_type: int = 50,
    ) -> LintReport:
        now = datetime.now(timezone.utc)
        report = LintReport(workspace_id=workspace_id, generated_at=now)

        # ---- 全局计数 ----
        page_count = (
            await session.execute(
                select(func.count(WikiPage.id)).where(
                    WikiPage.workspace_id == workspace_id,
                )
            )
        ).scalar_one()
        link_count = (
            await session.execute(
                select(func.count(WikiLink.id)).where(
                    WikiLink.workspace_id == workspace_id,
                )
            )
        ).scalar_one()
        open_conflicts = (
            await session.execute(
                select(func.count(WikiLink.id)).where(
                    WikiLink.workspace_id == workspace_id,
                    WikiLink.link_type == "contradicts",
                    WikiLink.status == "pending_review",
                )
            )
        ).scalar_one()

        report.page_count = int(page_count or 0)
        report.link_count = int(link_count or 0)
        report.open_conflicts = int(open_conflicts or 0)

        # ---- 1. 孤儿页 ----
        await self._collect_orphans(
            session, report, workspace_id=workspace_id, now=now, limit=max_issues_per_type
        )

        # ---- 2. 内联断链（markdown 中 [[xx]] 找不到目标页） ----
        await self._collect_broken_links(
            session, report, workspace_id=workspace_id, limit=max_issues_per_type
        )

        # ---- 3. 冲突未决 ----
        await self._collect_open_conflicts(
            session, report, workspace_id=workspace_id, limit=max_issues_per_type
        )

        # ---- 4. 过期 ----
        await self._collect_stale(
            session,
            report,
            workspace_id=workspace_id,
            now=now,
            limit=max_issues_per_type,
        )

        return report

    # ------------------------------------------------------------------
    # 各类问题
    # ------------------------------------------------------------------

    async def _collect_orphans(
        self,
        session: AsyncSession,
        report: LintReport,
        *,
        workspace_id: str,
        now: datetime,
        limit: int,
    ) -> None:
        grace_days = max(0, int(self._wiki_settings.lint_orphan_grace_days))
        grace_cutoff = now - timedelta(days=grace_days)

        # 没有任何入链 + 状态非 archived + 创建时间已过 grace
        incoming_exists = (
            select(WikiLink.id)
            .where(
                WikiLink.workspace_id == workspace_id,
                WikiLink.target_page_id == WikiPage.id,
                WikiLink.status == "active",
            )
            .correlate(WikiPage)
        )
        stmt = (
            select(WikiPage.id, WikiPage.slug, WikiPage.title, WikiPage.created_at)
            .where(
                WikiPage.workspace_id == workspace_id,
                WikiPage.status != "archived",
                WikiPage.created_at < grace_cutoff,
                ~exists(incoming_exists),
            )
            .order_by(WikiPage.created_at.asc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        for row in rows:
            page_id, slug, title, created_at = row
            report.issues.append(
                LintIssue(
                    issue_id=f"{ISSUE_ORPHAN}:{page_id}",
                    issue_type=ISSUE_ORPHAN,
                    severity="low",
                    page_id=page_id,
                    page_slug=slug,
                    page_title=title,
                    payload={"created_at": created_at.isoformat() if created_at else None},
                )
            )

    async def _collect_broken_links(
        self,
        session: AsyncSession,
        report: LintReport,
        *,
        workspace_id: str,
        limit: int,
    ) -> None:
        # 取出所有页的 markdown_body，提取 [[xx]] 然后批量查 slug
        pages_stmt = (
            select(WikiPage.id, WikiPage.slug, WikiPage.title, WikiPage.markdown_body)
            .where(
                WikiPage.workspace_id == workspace_id,
                WikiPage.status != "archived",
            )
            .limit(2000)  # 防止单次 lint 拉过多
        )
        rows = (await session.execute(pages_stmt)).all()
        if not rows:
            return

        page_inline_refs: list[tuple[str, str, str, list[str]]] = []
        all_slugs: set[str] = set()
        for row in rows:
            page_id, slug, title, body = row
            inline = WikiLinker.parse_inline_links(body or "")
            if not inline:
                continue
            page_inline_refs.append((page_id, slug, title, inline))
            all_slugs.update(inline)

        if not all_slugs:
            return

        existing = (
            await session.execute(
                select(WikiPage.slug).where(
                    WikiPage.workspace_id == workspace_id,
                    WikiPage.slug.in_(list(all_slugs)),
                )
            )
        ).all()
        known = {row[0] for row in existing}

        emitted = 0
        for page_id, slug, title, inline in page_inline_refs:
            broken = [s for s in inline if s not in known]
            if not broken:
                continue
            report.issues.append(
                LintIssue(
                    issue_id=f"{ISSUE_BROKEN_LINK}:{page_id}",
                    issue_type=ISSUE_BROKEN_LINK,
                    severity="medium",
                    page_id=page_id,
                    page_slug=slug,
                    page_title=title,
                    payload={"missing_slugs": broken},
                )
            )
            emitted += 1
            if emitted >= limit:
                break

    async def _collect_open_conflicts(
        self,
        session: AsyncSession,
        report: LintReport,
        *,
        workspace_id: str,
        limit: int,
    ) -> None:
        stmt = (
            select(
                WikiLink.id,
                WikiLink.source_page_id,
                WikiLink.target_page_id,
                WikiLink.note,
                WikiLink.created_at,
            )
            .where(
                WikiLink.workspace_id == workspace_id,
                WikiLink.link_type == "contradicts",
                WikiLink.status == "pending_review",
            )
            .order_by(WikiLink.created_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        threshold = int(self._wiki_settings.lint_conflict_open_threshold)
        severity = "high" if len(rows) >= threshold else "medium"
        for row in rows:
            link_id, source_id, target_id, note, created_at = row
            report.issues.append(
                LintIssue(
                    issue_id=f"{ISSUE_OPEN_CONFLICT}:{link_id}",
                    issue_type=ISSUE_OPEN_CONFLICT,
                    severity=severity,
                    page_id=source_id,
                    payload={
                        "link_id": link_id,
                        "target_page_id": target_id,
                        "note": note,
                        "created_at": created_at.isoformat() if created_at else None,
                    },
                )
            )

    async def _collect_stale(
        self,
        session: AsyncSession,
        report: LintReport,
        *,
        workspace_id: str,
        now: datetime,
        limit: int,
    ) -> None:
        stale_days = max(1, int(self._wiki_settings.lint_stale_days))
        cutoff = now - timedelta(days=stale_days)
        stmt = (
            select(WikiPage.id, WikiPage.slug, WikiPage.title, WikiPage.last_compiled_at)
            .where(
                WikiPage.workspace_id == workspace_id,
                WikiPage.status == "published",
                or_(
                    WikiPage.last_compiled_at.is_(None),
                    WikiPage.last_compiled_at < cutoff,
                ),
            )
            .order_by(WikiPage.last_compiled_at.asc().nullsfirst())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        for row in rows:
            page_id, slug, title, last_compiled_at = row
            report.issues.append(
                LintIssue(
                    issue_id=f"{ISSUE_STALE}:{page_id}",
                    issue_type=ISSUE_STALE,
                    severity="low",
                    page_id=page_id,
                    page_slug=slug,
                    page_title=title,
                    payload={
                        "last_compiled_at": (
                            last_compiled_at.isoformat() if last_compiled_at else None
                        ),
                        "stale_days_threshold": stale_days,
                    },
                )
            )


_linter: Optional[WikiLinter] = None


def get_wiki_linter() -> WikiLinter:
    global _linter
    if _linter is None:
        _linter = WikiLinter()
    return _linter
