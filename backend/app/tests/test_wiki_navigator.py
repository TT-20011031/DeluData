"""WikiNavigator 单元测试 (M3.2)。

策略：
- 纯函数 `_format_index_text` 直接测
- `_select_slugs_via_llm` 通过 mock get_async_llm 测
- `navigate` 通过覆盖私有 _fetch_index_pages / _select_slugs_via_llm / _load_pages_as_chunks
  的方式测主流程编排（避免直接 mock SQLAlchemy session_scope）
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.wiki.navigator import (
    WikiNavigator,
    _format_index_text,
)
from app.models.common.execution import DocumentChunk


# ============ 公共夹具 ============

def _build_settings(
    *,
    fast_model: str = "test-flash",
    summary_max: int = 80,
    top_k: int = 5,
    max_page_chars: int = 8000,
) -> SimpleNamespace:
    return SimpleNamespace(
        wiki=SimpleNamespace(
            router_model=fast_model,
            index_summary_max_chars=summary_max,
            index_load_top_k=top_k,
            max_page_chars=max_page_chars,
        ),
        llm=SimpleNamespace(fast_model=fast_model),
    )


# ============ 纯函数测试：_format_index_text ============

class TestFormatIndexText:
    def test_空列表(self):
        text = _format_index_text([], summary_max_chars=80)
        assert "(空" in text

    def test_单_domain_单页(self):
        pages = [{"slug": "abc", "title": "中文标题", "summary": "一行摘要", "domain": "policy"}]
        text = _format_index_text(pages, summary_max_chars=80)
        assert "[POLICY]" in text
        assert "- abc: 中文标题 — 一行摘要" in text

    def test_多_domain_分组(self):
        pages = [
            {"slug": "p1", "title": "P1", "summary": "", "domain": "policy"},
            {"slug": "g1", "title": "G1", "summary": "g 摘要", "domain": "general"},
        ]
        text = _format_index_text(pages, summary_max_chars=80)
        # 按 domain 字典序：general 在 policy 之前
        assert text.index("[GENERAL]") < text.index("[POLICY]")
        assert "- p1: P1" in text
        assert "- g1: G1 — g 摘要" in text

    def test_summary_截断(self):
        long = "a" * 200
        pages = [{"slug": "x", "title": "T", "summary": long, "domain": "general"}]
        text = _format_index_text(pages, summary_max_chars=10)
        assert "aaaaaaaaa…" in text
        assert "a" * 50 not in text  # 长串被截断

    def test_summary_含换行被压平(self):
        pages = [{"slug": "x", "title": "T", "summary": "第一行\n第二行", "domain": "general"}]
        text = _format_index_text(pages, summary_max_chars=80)
        assert "第一行 第二行" in text
        assert "第一行\n第二行" not in text

    def test_无_summary_不带破折号(self):
        pages = [{"slug": "x", "title": "T", "summary": "", "domain": "general"}]
        text = _format_index_text(pages, summary_max_chars=80)
        assert "- x: T" in text
        assert " — " not in text  # 没有 summary 不应出现破折号


# ============ _select_slugs_via_llm 测试 ============

@pytest.mark.asyncio
class TestSelectSlugsViaLLM:
    @patch("app.core.wiki.navigator.get_settings")
    @patch("app.core.wiki.navigator.get_async_llm")
    async def test_LLM_返回合法_slug_列表(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings()
        mock_llm_instance = AsyncMock()
        mock_llm_instance.generate_structured = AsyncMock(
            return_value={"selected_slugs": ["a", "b"], "reason": "ok"}
        )
        mock_llm.return_value = mock_llm_instance

        nav = WikiNavigator()
        slugs = await nav._select_slugs_via_llm(
            query="差旅怎么报销", index_text="[POLICY]\n- a: T", max_pages=5,
        )
        assert slugs == ["a", "b"]

    @patch("app.core.wiki.navigator.get_settings")
    @patch("app.core.wiki.navigator.get_async_llm")
    async def test_LLM_返回去重并截断(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings()
        mock_llm_instance = AsyncMock()
        mock_llm_instance.generate_structured = AsyncMock(
            return_value={"selected_slugs": ["A", "a", "B", "C", "D"], "reason": "ok"}
        )
        mock_llm.return_value = mock_llm_instance

        nav = WikiNavigator()
        slugs = await nav._select_slugs_via_llm(
            query="q", index_text="x", max_pages=2,
        )
        # 大小写归一化 + 去重 + 截断到 max_pages
        assert slugs == ["a", "b"]

    @patch("app.core.wiki.navigator.get_settings")
    @patch("app.core.wiki.navigator.get_async_llm")
    async def test_LLM_抛异常_返回空(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings()
        mock_llm_instance = AsyncMock()
        mock_llm_instance.generate_structured = AsyncMock(side_effect=RuntimeError("boom"))
        mock_llm.return_value = mock_llm_instance

        nav = WikiNavigator()
        slugs = await nav._select_slugs_via_llm(
            query="q", index_text="x", max_pages=5,
        )
        assert slugs == []

    @patch("app.core.wiki.navigator.get_settings")
    @patch("app.core.wiki.navigator.get_async_llm")
    async def test_LLM_返回非_dict_返回空(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings()
        mock_llm_instance = AsyncMock()
        mock_llm_instance.generate_structured = AsyncMock(return_value=None)
        mock_llm.return_value = mock_llm_instance

        nav = WikiNavigator()
        slugs = await nav._select_slugs_via_llm(query="q", index_text="x", max_pages=5)
        assert slugs == []


# ============ navigate 编排测试（覆盖私有方法）============

@pytest.mark.asyncio
class TestNavigate:
    async def test_workspace_id_为空_直接返回(self):
        nav = WikiNavigator()
        result = await nav.navigate(workspace_id="", query="q")
        assert result == []

    @patch("app.core.wiki.navigator.get_settings")
    @patch("app.core.wiki.navigator.get_async_db_manager")
    async def test_INDEX_为空_直接返回(self, mock_db, mock_settings):
        mock_settings.return_value = _build_settings()
        # session_scope 返回一个有效 async cm，不会被深入使用（_fetch_index_pages 被 mock）
        mock_db.return_value = _make_fake_db_manager()

        nav = WikiNavigator()
        nav._fetch_index_pages = AsyncMock(return_value=[])  # type: ignore[method-assign]
        nav._select_slugs_via_llm = AsyncMock()  # type: ignore[method-assign]
        nav._load_pages_as_chunks = AsyncMock()  # type: ignore[method-assign]

        result = await nav.navigate(workspace_id="ws-1", query="q")
        assert result == []
        # INDEX 空时不应触发 LLM 选页
        nav._select_slugs_via_llm.assert_not_called()
        nav._load_pages_as_chunks.assert_not_called()

    @patch("app.core.wiki.navigator.get_settings")
    @patch("app.core.wiki.navigator.get_async_db_manager")
    async def test_LLM_未选中任何_slug_直接返回(self, mock_db, mock_settings):
        mock_settings.return_value = _build_settings()
        mock_db.return_value = _make_fake_db_manager()

        nav = WikiNavigator()
        nav._fetch_index_pages = AsyncMock(return_value=[
            {"id": "1", "slug": "a", "title": "A", "summary": "", "domain": "general"},
        ])  # type: ignore[method-assign]
        nav._select_slugs_via_llm = AsyncMock(return_value=[])  # type: ignore[method-assign]
        nav._load_pages_as_chunks = AsyncMock()  # type: ignore[method-assign]

        result = await nav.navigate(workspace_id="ws-1", query="q")
        assert result == []
        nav._load_pages_as_chunks.assert_not_called()

    @patch("app.core.wiki.navigator.get_settings")
    @patch("app.core.wiki.navigator.get_async_db_manager")
    async def test_完整流程_返回_chunks(self, mock_db, mock_settings):
        mock_settings.return_value = _build_settings(top_k=3, max_page_chars=5000)
        mock_db.return_value = _make_fake_db_manager()

        fake_chunks = [
            DocumentChunk(
                content="# Title\n\n正文",
                source_file="wiki:a",
                chunk_id="wiki_page:1",
                score=1.0,
                metadata={"kind": "wiki_page", "slug": "a"},
            )
        ]

        nav = WikiNavigator()
        nav._fetch_index_pages = AsyncMock(return_value=[
            {"id": "1", "slug": "a", "title": "A", "summary": "s", "domain": "policy"},
        ])  # type: ignore[method-assign]
        nav._select_slugs_via_llm = AsyncMock(return_value=["a"])  # type: ignore[method-assign]
        nav._load_pages_as_chunks = AsyncMock(return_value=fake_chunks)  # type: ignore[method-assign]

        result = await nav.navigate(workspace_id="ws-1", query="差旅怎么报销")
        assert result == fake_chunks
        nav._select_slugs_via_llm.assert_awaited_once()
        nav._load_pages_as_chunks.assert_awaited_once()

    @patch("app.core.wiki.navigator.get_settings")
    @patch("app.core.wiki.navigator.get_async_db_manager")
    async def test_DB_异常_软降级_返回空(self, mock_db, mock_settings):
        mock_settings.return_value = _build_settings()
        mock_db.return_value = _make_fake_db_manager()

        nav = WikiNavigator()
        nav._fetch_index_pages = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]

        result = await nav.navigate(workspace_id="ws-1", query="q")
        # 异常路径必须软降级，不能抛
        assert result == []

    @patch("app.core.wiki.navigator.get_settings")
    @patch("app.core.wiki.navigator.get_async_db_manager")
    async def test_max_pages_参数被钳制(self, mock_db, mock_settings):
        mock_settings.return_value = _build_settings(top_k=5)
        mock_db.return_value = _make_fake_db_manager()

        nav = WikiNavigator()
        nav._fetch_index_pages = AsyncMock(return_value=[
            {"id": "1", "slug": "a", "title": "A", "summary": "", "domain": "general"},
        ])  # type: ignore[method-assign]
        captured = {}

        async def _fake_select(*, query, index_text, max_pages):
            captured["max_pages"] = max_pages
            return []
        nav._select_slugs_via_llm = _fake_select  # type: ignore[method-assign]
        nav._load_pages_as_chunks = AsyncMock(return_value=[])  # type: ignore[method-assign]

        # 显式传 100 应被钳制为 20（设计上限）
        await nav.navigate(workspace_id="ws-1", query="q", max_pages=100)
        assert captured["max_pages"] == 20

        # 显式传 0 / 负数应钳到 1
        await nav.navigate(workspace_id="ws-1", query="q", max_pages=-3)
        assert captured["max_pages"] == 1


# ============ helper：构造 fake db_manager.session_scope() ============

def _make_fake_db_manager():
    """
    构造一个 async context manager，session_scope() 返回一个可被 with 的对象。
    navigate 内部仅在 INDEX 非空时使用 session 实例，本套测试都 mock 了 _fetch / _load，
    所以这里只需要一个可进入退出的占位对象。
    """
    fake_session = MagicMock()

    class _Scope:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_db_manager = MagicMock()
    fake_db_manager.session_scope = lambda: _Scope()
    return fake_db_manager
