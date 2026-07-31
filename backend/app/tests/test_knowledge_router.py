"""KnowledgeRouter 节点单元测试 (M3.1)。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.supervisor.nodes.knowledge_router import (
    _classify_by_rules,
    knowledge_router_node,
)


# ============ 测试夹具 ============

def _build_settings(
    *,
    enabled: bool = True,
    rag_keywords: list[str] | None = None,
    wiki_keywords: list[str] | None = None,
    fallback: str = "both",
    router_model: str = "test-flash",
) -> SimpleNamespace:
    return SimpleNamespace(
        wiki=SimpleNamespace(
            first_enabled=enabled,
            router_model=router_model,
            router_keywords_rag_list=rag_keywords if rag_keywords is not None else ["原文", "第几条", "多少元"],
            router_keywords_wiki_list=wiki_keywords if wiki_keywords is not None else ["是什么", "区别", "总结"],
            router_fallback_path=fallback,
        ),
        llm=SimpleNamespace(fast_model="default-fast"),
    )


def _build_state(query: str, *, intent_type: str = "tool_use") -> dict:
    return {
        "user_query": query,
        "intent_type": intent_type,
    }


# ============ 纯函数测试：_classify_by_rules ============

class TestClassifyByRules:
    def test_空查询返回_None(self):
        path, reason = _classify_by_rules("", ["原文"], ["是什么"])
        assert path is None
        assert reason == ""

    def test_命中_rag_关键词(self):
        path, reason = _classify_by_rules("第三条原文具体是什么内容", ["原文", "第几条"], ["介绍"])
        assert path == "rag"
        assert "原文" in reason

    def test_命中_wiki_关键词(self):
        path, reason = _classify_by_rules("差旅报销制度是什么", ["原文"], ["是什么", "区别"])
        assert path == "wiki"
        assert "是什么" in reason

    def test_同时命中走_both(self):
        path, reason = _classify_by_rules("总结一下并给出原文出处", ["原文"], ["总结"])
        assert path == "both"
        assert "原文" in reason and "总结" in reason

    def test_都不命中返回_None(self):
        path, reason = _classify_by_rules("帮我做点别的事", ["原文"], ["是什么"])
        assert path is None

    def test_大小写不敏感(self):
        path, _ = _classify_by_rules("WHO is 张三", [], ["who"])
        assert path == "wiki"

    def test_公式计算类问题优先走_rag(self):
        path, reason = _classify_by_rules(
            "请你告诉我螺母支承面力矩是如何计算的",
            [],
            ["如何", "是什么"],
        )
        assert path == "rag"
        assert "公式/计算类" in reason


# ============ 节点行为测试 ============

@pytest.mark.asyncio
class TestKnowledgeRouterNode:
    @patch("app.supervisor.nodes.knowledge_router.get_settings")
    @patch("app.supervisor.nodes.knowledge_router.get_async_llm")
    async def test_开关关闭_直通_rag_不调用_LLM(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings(enabled=False)
        mock_llm_instance = AsyncMock()
        mock_llm.return_value = mock_llm_instance

        result = await knowledge_router_node(_build_state("差旅报销制度是什么"))

        assert result["knowledge_path"] == "rag"
        assert result["knowledge_path_source"] == "disabled"
        # 关键：开关关闭时，绝不允许调用 LLM（性能/成本回归保护）
        mock_llm_instance.generate_structured.assert_not_called()

    @patch("app.supervisor.nodes.knowledge_router.get_settings")
    @patch("app.supervisor.nodes.knowledge_router.get_async_llm")
    async def test_非_tool_use_仍参与知识路由(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings(enabled=True)
        mock_llm_instance = AsyncMock()
        mock_llm.return_value = mock_llm_instance

        result = await knowledge_router_node(_build_state("差旅报销制度是什么", intent_type="chitchat"))

        assert result["knowledge_path"] == "wiki"
        assert result["knowledge_path_source"] == "rule"
        mock_llm_instance.generate_structured.assert_not_called()

    @patch("app.supervisor.nodes.knowledge_router.get_settings")
    @patch("app.supervisor.nodes.knowledge_router.get_async_llm")
    async def test_规则命中_rag_不调用_LLM(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings(enabled=True)
        mock_llm_instance = AsyncMock()
        mock_llm.return_value = mock_llm_instance

        # 仅含 RAG 关键词「第几条」「原文」，不含任何 Wiki 关键词
        result = await knowledge_router_node(_build_state("第几条规定了原文条款"))

        assert result["knowledge_path"] == "rag"
        assert result["knowledge_path_source"] == "rule"
        mock_llm_instance.generate_structured.assert_not_called()

    @patch("app.supervisor.nodes.knowledge_router.get_settings")
    @patch("app.supervisor.nodes.knowledge_router.get_async_llm")
    async def test_公式计算类问题_覆盖_如何_wiki_关键词(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings(
            enabled=True,
            rag_keywords=[],
            wiki_keywords=["如何", "是什么"],
        )
        mock_llm_instance = AsyncMock()
        mock_llm.return_value = mock_llm_instance

        result = await knowledge_router_node(
            _build_state("请你告诉我螺母支承面力矩是如何计算的")
        )

        assert result["knowledge_path"] == "rag"
        assert result["knowledge_path_source"] == "rule"
        assert "公式/计算类" in result["knowledge_path_reason"]
        mock_llm_instance.generate_structured.assert_not_called()

    @patch("app.supervisor.nodes.knowledge_router.get_settings")
    @patch("app.supervisor.nodes.knowledge_router.get_async_llm")
    async def test_规则命中_wiki_不调用_LLM(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings(enabled=True)
        mock_llm_instance = AsyncMock()
        mock_llm.return_value = mock_llm_instance

        result = await knowledge_router_node(_build_state("德路平台和竞品的区别"))

        assert result["knowledge_path"] == "wiki"
        assert result["knowledge_path_source"] == "rule"
        mock_llm_instance.generate_structured.assert_not_called()

    @patch("app.supervisor.nodes.knowledge_router.get_settings")
    @patch("app.supervisor.nodes.knowledge_router.get_async_llm")
    async def test_规则同时命中_both(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings(enabled=True)
        mock_llm_instance = AsyncMock()
        mock_llm.return_value = mock_llm_instance

        result = await knowledge_router_node(_build_state("总结差旅制度并列出原文条款"))

        assert result["knowledge_path"] == "both"
        assert result["knowledge_path_source"] == "rule"
        mock_llm_instance.generate_structured.assert_not_called()

    @patch("app.supervisor.nodes.knowledge_router.get_settings")
    @patch("app.supervisor.nodes.knowledge_router.get_async_llm")
    async def test_规则未命中_LLM_返回_wiki(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings(enabled=True)
        mock_llm_instance = AsyncMock()
        mock_llm_instance.generate_structured = AsyncMock(
            return_value={"path": "wiki", "reason": "概念性问题"},
        )
        mock_llm.return_value = mock_llm_instance

        result = await knowledge_router_node(_build_state("讲讲张三这个人"))

        assert result["knowledge_path"] == "wiki"
        assert result["knowledge_path_source"] == "llm"
        assert result["knowledge_path_reason"] == "概念性问题"
        mock_llm_instance.generate_structured.assert_called_once()

    @patch("app.supervisor.nodes.knowledge_router.get_settings")
    @patch("app.supervisor.nodes.knowledge_router.get_async_llm")
    async def test_LLM_返回非法值_走_fallback(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings(enabled=True, fallback="both")
        mock_llm_instance = AsyncMock()
        mock_llm_instance.generate_structured = AsyncMock(
            return_value={"path": "invalid_value", "reason": "不应被采纳"},
        )
        mock_llm.return_value = mock_llm_instance

        result = await knowledge_router_node(_build_state("讲讲张三这个人"))

        assert result["knowledge_path"] == "both"
        assert result["knowledge_path_source"] == "fallback"

    @patch("app.supervisor.nodes.knowledge_router.get_settings")
    @patch("app.supervisor.nodes.knowledge_router.get_async_llm")
    async def test_LLM_抛异常_走_fallback(self, mock_llm, mock_settings):
        mock_settings.return_value = _build_settings(enabled=True, fallback="rag")
        mock_llm_instance = AsyncMock()
        mock_llm_instance.generate_structured = AsyncMock(side_effect=RuntimeError("network down"))
        mock_llm.return_value = mock_llm_instance

        result = await knowledge_router_node(_build_state("讲讲张三这个人"))

        assert result["knowledge_path"] == "rag"
        assert result["knowledge_path_source"] == "fallback"

    @patch("app.supervisor.nodes.knowledge_router.get_settings")
    @patch("app.supervisor.nodes.knowledge_router.get_async_llm")
    async def test_fallback_配置非法值兜底为_both(self, mock_llm, mock_settings):
        # 配置错误时绝不让 path 留空或非法
        mock_settings.return_value = _build_settings(enabled=True, fallback="garbage")
        mock_llm_instance = AsyncMock()
        mock_llm_instance.generate_structured = AsyncMock(side_effect=RuntimeError())
        mock_llm.return_value = mock_llm_instance

        result = await knowledge_router_node(_build_state("讲讲张三这个人"))

        assert result["knowledge_path"] == "both"
        assert result["knowledge_path_source"] == "fallback"
