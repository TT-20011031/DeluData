"""[治理] WikiService.batch_archive_pages 单元测试。

覆盖：
1. 缺参数 slugs+domain 双空 → error
2. slugs 列表全空字符串 → error
3. 按 slugs 归档 (正常) → archived=2 / skipped=0
4. 按 domain 归档 → archived=3
5. 部分 slug 不存在 → archived=1 / skipped=1
6. 全部已经是 archived → archived=0 / skipped=0 (不会复写)

不依赖真实 DB，使用项目既有 _SeqSession 模式。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


# ============ 共用 fakes ============


class _SeqSession:
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
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = list(pages)
    m = MagicMock()
    m.scalars.return_value = scalars_mock
    return m


def _make_page(
    *,
    page_id: str,
    slug: str,
    title: str = "",
    status: str = "published",
    version: int = 3,
    domain: str = "general",
    markdown: str = "",
    summary: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=page_id,
        slug=slug,
        title=title or slug.title(),
        status=status,
        version=version,
        domain=domain,
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
class TestBatchArchive:
    async def test_缺参数_双空(self, monkeypatch):
        from app.services.wiki_service import WikiService

        session = _SeqSession(results=[])
        _patch_db(monkeypatch, session)

        result = await WikiService().batch_archive_pages(
            workspace_id="ws-1",
            triggered_by="user:u1",
        )

        assert result["success"] is False
        assert "必须提供" in result["error"]
        assert session.adds == []

    async def test_slugs_空列表(self, monkeypatch):
        from app.services.wiki_service import WikiService

        session = _SeqSession(results=[])
        _patch_db(monkeypatch, session)

        result = await WikiService().batch_archive_pages(
            workspace_id="ws-1",
            slugs=["  ", ""],
            triggered_by="user:u1",
        )

        assert result["success"] is False
        assert "为空" in result["error"]

    async def test_domain_空字符串(self, monkeypatch):
        from app.services.wiki_service import WikiService

        session = _SeqSession(results=[])
        _patch_db(monkeypatch, session)

        result = await WikiService().batch_archive_pages(
            workspace_id="ws-1",
            domain="  ",
            triggered_by="user:u1",
        )

        assert result["success"] is False
        assert "为空" in result["error"]

    async def test_按slugs归档_全部命中(self, monkeypatch):
        from app.services.wiki_service import WikiService

        page_a = _make_page(page_id="p-a", slug="alpha", version=2)
        page_b = _make_page(page_id="p-b", slug="beta", version=5)
        session = _SeqSession(results=[_make_pages_result([page_a, page_b])])
        _patch_db(monkeypatch, session)

        result = await WikiService().batch_archive_pages(
            workspace_id="ws-1",
            slugs=["alpha", "beta"],
            triggered_by="user:u1",
        )

        assert result["workspace_id"] == "ws-1"
        assert result["requested_count"] == 2
        assert result["archived_count"] == 2
        assert result["skipped_count"] == 0
        assert result["skipped_slugs"] == []

        assert page_a.status == "archived"
        assert page_a.version == 3
        assert page_b.status == "archived"
        assert page_b.version == 6

        archived_slugs = {it["slug"] for it in result["archived"]}
        assert archived_slugs == {"alpha", "beta"}
        assert all(it["reason"] == "batch_archive" for it in result["archived"])

        assert len(session.adds) == 2
        for rev in session.adds:
            assert rev.commit_message == "batch_archive"
            assert rev.committed_by == "user:u1"

    async def test_按domain归档(self, monkeypatch):
        from app.services.wiki_service import WikiService

        p1 = _make_page(page_id="p-1", slug="one", domain="policy")
        p2 = _make_page(page_id="p-2", slug="two", domain="policy")
        p3 = _make_page(page_id="p-3", slug="three", domain="policy")
        session = _SeqSession(results=[_make_pages_result([p1, p2, p3])])
        _patch_db(monkeypatch, session)

        result = await WikiService().batch_archive_pages(
            workspace_id="ws-1",
            domain="policy",
            triggered_by="user:u1",
        )

        assert result["archived_count"] == 3
        assert result["skipped_count"] == 0
        assert {it["slug"] for it in result["archived"]} == {"one", "two", "three"}
        assert len(session.adds) == 3

    async def test_部分slug不存在(self, monkeypatch):
        from app.services.wiki_service import WikiService

        page_a = _make_page(page_id="p-a", slug="alpha")
        # slug "gamma" 不在 DB 里
        session = _SeqSession(results=[_make_pages_result([page_a])])
        _patch_db(monkeypatch, session)

        result = await WikiService().batch_archive_pages(
            workspace_id="ws-1",
            slugs=["alpha", "gamma"],
            triggered_by="user:u1",
        )

        assert result["archived_count"] == 1
        assert result["skipped_count"] == 1
        assert result["skipped_slugs"] == ["gamma"]
        assert len(session.adds) == 1

    async def test_全部已归档_不影响(self, monkeypatch):
        """已 archived 的页不会再被修改（查询时已过滤 archived）。"""
        from app.services.wiki_service import WikiService

        # 两个页都已是 archived，查询不会返回它们
        session = _SeqSession(results=[_make_pages_result([])])
        _patch_db(monkeypatch, session)

        result = await WikiService().batch_archive_pages(
            workspace_id="ws-1",
            slugs=["alpha", "beta"],
            triggered_by="user:u1",
        )

        assert result["archived_count"] == 0
        assert result["skipped_count"] == 2
        assert set(result["skipped_slugs"]) == {"alpha", "beta"}
        assert session.adds == []

    async def test_mixed_status_partial_skip(self, monkeypatch):
        """已 archived 的 slug 被跳过，非 archived 的被处理。"""
        from app.services.wiki_service import WikiService

        # "alpha" 已 archived → 查询只返回 "beta"
        page_b = _make_page(page_id="p-b", slug="beta", status="published")
        session = _SeqSession(results=[_make_pages_result([page_b])])
        _patch_db(monkeypatch, session)

        result = await WikiService().batch_archive_pages(
            workspace_id="ws-1",
            slugs=["alpha", "beta"],
            triggered_by="user:u1",
        )

        assert result["archived_count"] == 1
        assert result["skipped_count"] == 1
        assert "alpha" in result["skipped_slugs"]
        assert page_b.status == "archived"
