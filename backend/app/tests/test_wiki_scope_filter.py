"""[M3.5] 后端范围过滤单元测试。

覆盖：
- normalize_doc_scope: 保留 / 截断 domains / wiki_slugs
- KnowledgeRouter: doc_scope.domains / wiki_slugs 命中时强制 wiki 路径
- WikiNavigator: domains 过滤 INDEX；wiki_slugs 跳过 LLM 选页
- DocSkill.wiki_navigate: 透传新参数到 navigator
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ============ normalize_doc_scope ============


class TestNormalizeDocScope:
    def test_保留_domains_与_wiki_slugs(self):
        from app.services.doc_scope import normalize_doc_scope

        result = normalize_doc_scope(
            {
                "folder_ids": [],
                "file_ids": [],
                "domains": ["policy", "product"],
                "wiki_slugs": ["a", "b"],
            }
        )
        assert result is not None
        assert result["domains"] == ["policy", "product"]
        assert result["wiki_slugs"] == ["a", "b"]

    def test_所有字段全空时_drop_empty_返回_None(self):
        from app.services.doc_scope import normalize_doc_scope

        result = normalize_doc_scope({"folder_ids": [], "file_ids": []}, drop_empty=True)
        assert result is None

    def test_仅有_domains_时_不被_drop_empty_丢弃(self):
        from app.services.doc_scope import normalize_doc_scope

        result = normalize_doc_scope({"domains": ["x"]}, drop_empty=True)
        assert result is not None
        assert result["domains"] == ["x"]

    def test_去重_并截断超限(self):
        from app.services.doc_scope import normalize_doc_scope, MAX_WIKI_SLUGS

        slugs = [f"s-{i}" for i in range(MAX_WIKI_SLUGS + 50)] + ["s-0"]  # 重复 + 超限
        result = normalize_doc_scope({"wiki_slugs": slugs}, drop_empty=False)
        assert result is not None
        assert len(result["wiki_slugs"]) == MAX_WIKI_SLUGS
        # s-0 仅出现一次
        assert result["wiki_slugs"].count("s-0") == 1


# ============ KnowledgeRouter ============


@pytest.mark.asyncio
class TestKnowledgeRouterScopeRule:
    async def test_doc_scope_含_wiki_slugs_时_强制_wiki_路径(self, monkeypatch):
        from app.supervisor.nodes.knowledge_router import knowledge_router_node
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings.wiki, "first_enabled", True)

        result = await knowledge_router_node({
            "intent_type": "tool_use",
            "user_query": "随便问一句",
            "user_context": {
                "doc_scope": {
                    "wiki_slugs": ["page-a"],
                    "domains": [],
                }
            },
        })
        assert result["knowledge_path"] == "wiki"
        assert result["knowledge_path_source"] == "scope"

    async def test_doc_scope_含_domains_时_强制_wiki_路径(self, monkeypatch):
        from app.supervisor.nodes.knowledge_router import knowledge_router_node
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings.wiki, "first_enabled", True)

        result = await knowledge_router_node({
            "intent_type": "tool_use",
            "user_query": "什么是xxx?",
            "user_context": {"doc_scope": {"domains": ["policy"]}},
        })
        assert result["knowledge_path"] == "wiki"
        assert result["knowledge_path_source"] == "scope"

    async def test_doc_scope_仅含_folder_ids_不影响判定(self, monkeypatch):
        """选了文件夹但没选 wiki 范围 → scope 规则不触发，由后续规则/LLM 决定。"""
        from app.supervisor.nodes import knowledge_router as kr_module
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings.wiki, "first_enabled", True)
        # 直接 mock 规则分类函数，避免依赖 pydantic computed 属性
        monkeypatch.setattr(
            kr_module,
            "_classify_by_rules",
            lambda query, rag_keywords, wiki_keywords: ("rag", "规则模拟命中"),
        )

        result = await kr_module.knowledge_router_node({
            "intent_type": "tool_use",
            "user_query": "请原文摘录第几条",
            "user_context": {"doc_scope": {"folder_ids": ["f-1"]}},
        })
        assert result["knowledge_path"] == "rag"
        assert result["knowledge_path_source"] == "rule"


# ============ WikiNavigator domains / wiki_slugs ============


@pytest.mark.asyncio
class TestWikiNavigatorScope:
    async def _patch_db(self, monkeypatch, *, fetch_pages=None, load_chunks=None):
        """共用 helper：mock navigator 内部的 _fetch_index_pages + _load_pages_as_chunks。"""
        from app.core.wiki import navigator as nav_module

        nav_module.set_current_workspace = lambda *a, **kw: None  # noqa: E731
        nav_module.get_current_workspace = lambda: ""

        class _FakeMgr:
            def session_scope(self):
                class _Ctx:
                    async def __aenter__(self_inner):
                        return MagicMock()

                    async def __aexit__(self_inner, *args):
                        return False

                return _Ctx()

        monkeypatch.setattr(nav_module, "get_async_db_manager", lambda: _FakeMgr())

        nav = nav_module.WikiNavigator()

        if fetch_pages is not None:
            async def _fake_fetch(self, session, workspace_id, *, domains=None, source_scope=None):
                return fetch_pages(domains=domains)
            monkeypatch.setattr(
                nav_module.WikiNavigator, "_fetch_index_pages", _fake_fetch
            )

        if load_chunks is not None:
            async def _fake_load(self, session, *, workspace_id, slugs, per_page_max_chars, source_scope=None):
                return load_chunks(slugs=slugs)
            monkeypatch.setattr(
                nav_module.WikiNavigator, "_load_pages_as_chunks", _fake_load
            )
        return nav

    async def test_wiki_slugs_非空_跳过_LLM_选页_直接装载(self, monkeypatch):
        loaded_slugs: list[list[str]] = []

        def _load(slugs):
            loaded_slugs.append(list(slugs))
            return [SimpleNamespace(chunk_id=f"wiki:{s}") for s in slugs]

        from app.core.wiki import navigator as nav_module

        # 监控 LLM 不应被调用
        select_calls = []
        async def _fail_select(*args, **kwargs):
            select_calls.append(kwargs)
            return ["should-not-be-used"]
        monkeypatch.setattr(
            nav_module.WikiNavigator, "_select_slugs_via_llm", _fail_select
        )

        nav = await self._patch_db(monkeypatch, load_chunks=_load)

        chunks = await nav.navigate(
            workspace_id="ws-1",
            query="随便",
            wiki_slugs=["page-a", "page-b"],
        )
        assert len(chunks) == 2
        assert loaded_slugs == [["page-a", "page-b"]]
        assert select_calls == []  # LLM 选页未被调用

    async def test_domains_收窄_INDEX_构建(self, monkeypatch):
        captured: dict[str, Any] = {}

        def _fetch(domains):
            captured["domains"] = domains
            return [
                {"id": "1", "slug": "p1", "title": "T1", "summary": "", "domain": "policy"},
            ]

        def _load(slugs):
            return [SimpleNamespace(chunk_id=f"wiki:{s}") for s in slugs]

        from app.core.wiki import navigator as nav_module

        async def _fake_select(self, *, query, index_text, max_pages):
            return ["p1"]
        monkeypatch.setattr(
            nav_module.WikiNavigator, "_select_slugs_via_llm", _fake_select
        )

        nav = await self._patch_db(
            monkeypatch, fetch_pages=_fetch, load_chunks=_load,
        )

        chunks = await nav.navigate(
            workspace_id="ws-1",
            query="差旅?",
            domains=["policy", "product"],
        )
        assert len(chunks) == 1
        assert captured["domains"] == ["policy", "product"]


# ============ DocSkill.wiki_navigate 透传 ============


@pytest.mark.asyncio
class TestDocSkillWikiNavigateTransfer:
    async def test_透传_domains_与_wiki_slugs_到_navigator(self, monkeypatch):
        from app.skills.doc_skill import DocSkill
        from app.models.common.context import UserContext
        from app.core.wiki import navigator as nav_module

        captured: dict[str, Any] = {}

        class _FakeNavigator:
            async def navigate(self, **kwargs):
                captured.update(kwargs)
                return []

        # navigator 在 doc_skill 函数体内 import，故必须 patch 源模块
        monkeypatch.setattr(nav_module, "get_wiki_navigator", lambda: _FakeNavigator())

        skill = DocSkill()
        user_ctx = UserContext(user_id="u-1", workspace_id="ws-1")
        await skill.wiki_navigate(
            query="q",
            user_context=user_ctx,
            domains=["policy"],
            wiki_slugs=["page-a"],
        )
        assert captured.get("domains") == ["policy"]
        assert captured.get("wiki_slugs") == ["page-a"]
