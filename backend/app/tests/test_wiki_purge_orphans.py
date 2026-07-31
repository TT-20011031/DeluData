"""[治理] WikiService.purge_orphans_for_deleted_files 单元测试。

覆盖：
1. 空工作区（无候选孤儿页） → scanned=0 / purged=0
2. 正常清理（所有源文件 is_deleted=True） → page.status 改为 archived，version+1，新增 WikiRevision
3. 混合场景（部分源文件丢失 + 部分 deleted） → reason 含两类描述
4. 审计字段验证：commit_message 含 'purge_orphans:'，snapshot_markdown 同步保留

不依赖真实 DB，使用项目既有 _SeqSession 模式。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


# ============ 共用 fakes ============


class _SeqSession:
    """按调用顺序返回预设 execute result 的 fake AsyncSession。"""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self._idx = 0
        self.adds: list[Any] = []

    def add(self, obj: Any) -> None:
        self.adds.append(obj)

    async def flush(self) -> None:
        pass

    async def execute(self, stmt: Any) -> Any:
        if self._idx >= len(self._results):
            raise AssertionError(
                f"_SeqSession 调用 execute 超出预设次数 ({self._idx} >= {len(self._results)})"
            )
        item = self._results[self._idx]
        self._idx += 1
        return item


class _FakeScope:
    def __init__(self, session: _SeqSession) -> None:
        self._session = session

    async def __aenter__(self) -> _SeqSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _make_pages_result(pages: list[Any]) -> MagicMock:
    """模拟 select(WikiPage) → .scalars().all() 链式调用。"""
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = list(pages)
    m = MagicMock()
    m.scalars.return_value = scalars_mock
    return m


def _make_rows_result(rows: list[tuple]) -> MagicMock:
    """模拟 select(...) → .all() 链式调用（用于源文件查询）。"""
    m = MagicMock()
    m.all.return_value = list(rows)
    return m


def _make_page(
    *,
    page_id: str,
    slug: str,
    title: str = "",
    status: str = "published",
    version: int = 3,
    markdown: str = "",
    summary: str = "",
) -> SimpleNamespace:
    """轻量 WikiPage 替身（只暴露 service 实际访问的字段）。"""
    return SimpleNamespace(
        id=page_id,
        slug=slug,
        title=title or slug.title(),
        status=status,
        version=version,
        markdown_body=markdown or f"# {slug}\n\nbody",
        summary=summary or f"summary of {slug}",
    )


def _patch_db(monkeypatch, session: _SeqSession) -> None:
    from app.services import wiki_service as svc_module

    fake_mgr = MagicMock()
    fake_mgr.session_scope = lambda: _FakeScope(session)
    monkeypatch.setattr(svc_module, "get_async_db_manager", lambda: fake_mgr)
    monkeypatch.setattr(svc_module, "set_current_workspace", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc_module, "get_current_workspace", lambda: "")


# ============ 测试 ============


@pytest.mark.asyncio
class TestPurgeOrphans:
    async def test_空工作区_无候选(self, monkeypatch):
        from app.services.wiki_service import WikiService

        session = _SeqSession(results=[_make_pages_result([])])
        _patch_db(monkeypatch, session)

        result = await WikiService().purge_orphans_for_deleted_files(
            workspace_id="ws-1",
            triggered_by="user:u1",
        )

        assert result["scanned_count"] == 0
        assert result["purged_count"] == 0
        assert result["purged"] == []
        assert session.adds == []  # 没有 WikiRevision 写入

    async def test_所有源已删_页被archive(self, monkeypatch):
        from app.services.wiki_service import WikiService

        page = _make_page(page_id="p-a", slug="alpha", version=2)
        # execute 顺序：1) 候选 pages；2) 该页的源文件查询
        session = _SeqSession(
            results=[
                _make_pages_result([page]),
                _make_rows_result(
                    [
                        ("file-1", "report-2024.docx", True),
                        ("file-2", "policy.md", True),
                    ]
                ),
            ]
        )
        _patch_db(monkeypatch, session)

        result = await WikiService().purge_orphans_for_deleted_files(
            workspace_id="ws-1",
            triggered_by="user:u1",
        )

        assert result["scanned_count"] == 1
        assert result["purged_count"] == 1
        item = result["purged"][0]
        assert item["slug"] == "alpha"
        assert item["previous_status"] == "published"
        assert "report-2024.docx" in item["reason"]
        assert "policy.md" in item["reason"]

        # 页本身已被改写
        assert page.status == "archived"
        assert page.version == 3  # 2 + 1

        # 写入了一条 WikiRevision
        assert len(session.adds) == 1
        rev = session.adds[0]
        assert rev.page_id == "p-a"
        assert rev.version == 3
        assert rev.committed_by == "user:u1"
        assert rev.commit_message.startswith("purge_orphans:")
        assert "report-2024.docx" in rev.commit_message
        # snapshot 保留原 markdown 与 summary
        assert rev.snapshot_markdown == page.markdown_body
        assert rev.snapshot_summary == page.summary

    async def test_混合_已删与丢失(self, monkeypatch):
        from app.services.wiki_service import WikiService

        page = _make_page(page_id="p-b", slug="beta")
        session = _SeqSession(
            results=[
                _make_pages_result([page]),
                _make_rows_result(
                    [
                        ("file-x", "deleted.md", True),
                        ("file-missing", None, None),  # 该 file 已被 SQL 物理删除
                    ]
                ),
            ]
        )
        _patch_db(monkeypatch, session)

        result = await WikiService().purge_orphans_for_deleted_files(
            workspace_id="ws-1",
            triggered_by="cron",
        )

        item = result["purged"][0]
        assert "deleted.md" in item["reason"]
        assert "丢失" in item["reason"]  # 含丢失计数
        assert page.status == "archived"

    async def test_多个孤儿页_批量处理(self, monkeypatch):
        from app.services.wiki_service import WikiService

        p1 = _make_page(page_id="p-1", slug="one", version=1)
        p2 = _make_page(page_id="p-2", slug="two", version=5)
        session = _SeqSession(
            results=[
                _make_pages_result([p1, p2]),
                _make_rows_result([("f-a", "a.md", True)]),
                _make_rows_result([("f-b", "b.md", True), ("f-c", "c.md", True)]),
            ]
        )
        _patch_db(monkeypatch, session)

        result = await WikiService().purge_orphans_for_deleted_files(
            workspace_id="ws-1",
            triggered_by="user:u1",
        )

        assert result["scanned_count"] == 2
        assert result["purged_count"] == 2
        assert {it["slug"] for it in result["purged"]} == {"one", "two"}
        assert p1.status == "archived" and p1.version == 2
        assert p2.status == "archived" and p2.version == 6
        assert len(session.adds) == 2  # 两条 WikiRevision

    async def test_全部丢失_reason_兜底(self, monkeypatch):
        from app.services.wiki_service import WikiService

        page = _make_page(page_id="p-c", slug="gamma")
        session = _SeqSession(
            results=[
                _make_pages_result([page]),
                _make_rows_result(
                    [
                        ("file-m1", None, None),
                        ("file-m2", None, None),
                    ]
                ),
            ]
        )
        _patch_db(monkeypatch, session)

        result = await WikiService().purge_orphans_for_deleted_files(
            workspace_id="ws-1",
            triggered_by="user:u1",
        )

        item = result["purged"][0]
        assert "丢失 2 份" in item["reason"]
        assert page.status == "archived"
