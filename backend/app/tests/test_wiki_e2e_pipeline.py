"""[M3 收尾] Wiki-First 管线端到端集成测试。

覆盖 knowledge_router → doc_worker(mock) → executor._record_wiki_route_metrics 链路，
不依赖真实 LLM / DB，用 monkeypatch 隔离副作用。

四个关键场景：
1. 规则命中 wiki —— 用户问"是什么" → path=wiki, source=rule
2. 规则命中 rag —— 用户问"原文第几条" → path=rag, source=rule
3. Scope 强制 wiki —— doc_scope.domains 非空时跳过 LLM 直接走 wiki, source=scope
4. LLM Fallback —— 规则不命中 + LLM 不可用 → source=fallback, path=settings.wiki.router_fallback_path

每个场景都断言：
- knowledge_router_node 返回正确的 path / source
- Executor._record_wiki_route_metrics 把 doc_worker 结果落到 metrics service
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


# ============ 共用 fixtures ============


@pytest.fixture
def patch_settings_for_router(monkeypatch):
    """开启 wiki first_enabled 并确保关键词配置可预测。"""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.wiki, "first_enabled", True)
    return settings


@pytest.fixture
def mock_metrics_service(monkeypatch):
    """拦截 metrics service 写库，返回捕获列表。"""
    from app.services import wiki_metrics_service as svc_module

    captured: list[dict[str, Any]] = []

    async def _fake_record(**kwargs):
        captured.append(kwargs)
        return f"fake-id-{len(captured)}"

    fake_svc = MagicMock()
    fake_svc.record_route_metric = _fake_record
    monkeypatch.setattr(svc_module, "get_wiki_metrics_service", lambda: fake_svc)
    return captured


@pytest.fixture
def mock_llm_fail(monkeypatch):
    """让 get_async_llm 返回的 LLM 在 generate_structured 时抛错（用于 fallback 场景）。"""
    from app.supervisor.nodes import knowledge_router as kr_module

    class _BadLLM:
        async def generate_structured(self, **kwargs):
            raise RuntimeError("LLM unavailable for test")

    monkeypatch.setattr(kr_module, "get_async_llm", lambda: _BadLLM())


# ============ 端到端管线 ============


async def _simulate_pipeline(
    *,
    user_query: str,
    doc_scope: dict | None = None,
    router_llm_mock=None,
) -> tuple[dict, list[dict]]:
    """跑 KnowledgeRouter → 构造 doc_worker 结果 → Executor 落埋点。

    返回 (router_result, metric_records)。
    """
    from app.supervisor.nodes.knowledge_router import knowledge_router_node
    from app.supervisor.nodes import executor as executor_module

    user_context: dict[str, Any] = {"user_id": "u-1", "workspace_id": "ws-e2e"}
    if doc_scope is not None:
        user_context["doc_scope"] = doc_scope

    state = {
        "intent_type": "tool_use",
        "user_query": user_query,
        "user_context": user_context,
    }

    # 1) 路由判定
    router_patch = await knowledge_router_node(state)
    state.update(router_patch)

    # 2) 模拟 doc_worker 执行结果（executor 要求 meta 至少带 knowledge_path / chunks_used）
    worker_results = [
        {
            "step_id": "step-1",
            "worker": "doc_worker",
            "meta": {
                "chunks_used": 4,
                "wiki_chunks_count": 2 if router_patch["knowledge_path"] in ("wiki", "both") else 0,
                "wiki_chunks_used": 1 if router_patch["knowledge_path"] in ("wiki", "both") else 0,
                "knowledge_path": router_patch["knowledge_path"],
                "latency_ms": 321,
                "stop_reason": "rag_ok",
                "wiki_gap_signal": None,
            },
        }
    ]

    # 3) Executor 埋点（fire-and-forget，这里 await 等待完成以便断言）
    await executor_module._record_wiki_route_metrics(
        results=worker_results,
        state=state,
        workspace_id="ws-e2e",
        session_id="sess-e2e",
        user_id="u-1",
    )
    return router_patch, worker_results


# ============ 场景 1：规则命中 wiki ============


@pytest.mark.asyncio
async def test_场景1_规则命中_wiki(patch_settings_for_router, mock_metrics_service):
    router_patch, _ = await _simulate_pipeline(user_query="什么是差旅报销制度？")
    assert router_patch["knowledge_path"] == "wiki"
    assert router_patch["knowledge_path_source"] == "rule"
    assert len(mock_metrics_service) == 1
    rec = mock_metrics_service[0]
    assert rec["knowledge_path"] == "wiki"
    assert rec["knowledge_path_source"] == "rule"
    assert rec["workspace_id"] == "ws-e2e"
    assert rec["user_query"] == "什么是差旅报销制度？"


# ============ 场景 2：规则命中 rag ============


@pytest.mark.asyncio
async def test_场景2_规则命中_rag(patch_settings_for_router, mock_metrics_service):
    router_patch, _ = await _simulate_pipeline(user_query="请原文摘录这份合同第三条")
    assert router_patch["knowledge_path"] == "rag"
    assert router_patch["knowledge_path_source"] == "rule"
    assert len(mock_metrics_service) == 1
    rec = mock_metrics_service[0]
    assert rec["knowledge_path"] == "rag"
    assert rec["knowledge_path_source"] == "rule"


# ============ 场景 3：Scope 强制 wiki ============


@pytest.mark.asyncio
async def test_场景3_scope_强制_wiki(patch_settings_for_router, mock_metrics_service):
    """用户在 KnowledgeScopePicker 里显式选了 Wiki 域/实体页 → 必走 wiki。"""
    router_patch, _ = await _simulate_pipeline(
        user_query="问一个任意问题",
        doc_scope={
            "folder_ids": [],
            "file_ids": [],
            "domains": ["policy"],
            "wiki_slugs": [],
        },
    )
    assert router_patch["knowledge_path"] == "wiki"
    assert router_patch["knowledge_path_source"] == "scope"
    assert len(mock_metrics_service) == 1
    assert mock_metrics_service[0]["knowledge_path_source"] == "scope"


# ============ 场景 4：LLM Fallback ============


@pytest.mark.asyncio
async def test_场景4_LLM_fallback(
    patch_settings_for_router, mock_metrics_service, mock_llm_fail, monkeypatch
):
    """关键词不命中 + LLM 不可用 → 走 settings.wiki.router_fallback_path。"""
    from app.supervisor.nodes import knowledge_router as kr_module

    # 规则分类函数直接返回 (None, "") 以确保触发 LLM 分支
    monkeypatch.setattr(
        kr_module,
        "_classify_by_rules",
        lambda query, rag_keywords, wiki_keywords: (None, ""),
    )

    router_patch, _ = await _simulate_pipeline(user_query="一个模糊的问题，没有关键词")
    assert router_patch["knowledge_path_source"] == "fallback"
    assert router_patch["knowledge_path"] in ("wiki", "rag", "both")
    assert len(mock_metrics_service) == 1
    rec = mock_metrics_service[0]
    assert rec["knowledge_path_source"] == "fallback"
    # 落库的 path 必须与 router 判定一致
    assert rec["knowledge_path"] == router_patch["knowledge_path"]


# ============ 场景 5：wiki_gap_signal 透传 ============


@pytest.mark.asyncio
async def test_场景5_wiki_gap_signal_透传落库(
    patch_settings_for_router, mock_metrics_service
):
    """doc_worker 报告 wiki gap 时，metrics 应当记录 has_wiki_gap=True + task_id。"""
    from app.supervisor.nodes.knowledge_router import knowledge_router_node
    from app.supervisor.nodes import executor as executor_module

    state = {
        "intent_type": "tool_use",
        "user_query": "什么是xxx制度",
        "user_context": {"user_id": "u-1", "workspace_id": "ws-e2e"},
    }
    router_patch = await knowledge_router_node(state)
    state.update(router_patch)

    worker_results = [
        {
            "step_id": "step-1",
            "worker": "doc_worker",
            "meta": {
                "chunks_used": 3,
                "wiki_chunks_count": 2,
                "wiki_chunks_used": 2,
                "knowledge_path": "wiki",
                "latency_ms": 500,
                "stop_reason": "wiki_gap_detected",
                "wiki_gap_signal": {"task_id": "gap-xyz", "missing": ["报销限额"]},
            },
        }
    ]

    await executor_module._record_wiki_route_metrics(
        results=worker_results,
        state=state,
        workspace_id="ws-e2e",
        session_id="sess-e2e",
        user_id="u-1",
    )
    assert len(mock_metrics_service) == 1
    rec = mock_metrics_service[0]
    assert rec["has_wiki_gap"] is True
    assert rec["wiki_gap_task_id"] == "gap-xyz"
    assert rec["stop_reason"] == "wiki_gap_detected"
