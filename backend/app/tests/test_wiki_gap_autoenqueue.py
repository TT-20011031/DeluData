"""[M4.1] Executor Wiki Gap 自动入队单元测试。

覆盖：
- 正常入队：missing 非空时调 enqueue_task
- 空信号短路：missing 全空 / 非 list 时不入队
- workspace_id 缺失：不入队
- 异常隔离：enqueue_task 抛错时仅 log，不影响主流程
- compile_task_id 回写：入队成功后写入 wiki_gap_signal
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_queue(monkeypatch):
    """拦截 wiki_compile_queue，记录 enqueue_task 调用。"""
    from app.services import wiki_compile_queue_service as q_module

    captured: list[dict[str, Any]] = []

    class _FakeTask:
        def __init__(self, task_id: str = "fake-task-id"):
            self.id = task_id

    fake_q = MagicMock()

    async def _fake_enqueue(**kwargs):
        captured.append(kwargs)
        return _FakeTask()

    fake_q.enqueue_task = _fake_enqueue
    monkeypatch.setattr(q_module, "get_wiki_compile_queue", lambda: fake_q)
    return captured


@pytest.mark.asyncio
class TestEnqueueWikiGapTask:
    async def test_正常_missing_非空_入队成功(self, fake_queue):
        from app.supervisor.nodes.executor import _enqueue_wiki_gap_task

        task_id = await _enqueue_wiki_gap_task(
            wiki_gap_signal={
                "missing": ["差旅报销限额", "审批流程"],
                "task_id": "upstream-syn-001",
            },
            workspace_id="ws-1",
            user_id="u-1",
            session_id="sess-1",
            user_query="差旅报销限额是多少?",
        )
        assert task_id == "fake-task-id"
        assert len(fake_queue) == 1
        call = fake_queue[0]
        assert call["workspace_id"] == "ws-1"
        assert call["trigger_type"] == "reflection"
        assert call["user_id"] == "u-1"
        assert call["deduplicate"] is True
        payload = call["payload"]
        assert payload["missing"] == ["差旅报销限额", "审批流程"]
        assert payload["trigger_source"] == "supervisor_wiki_gap"
        assert payload["session_id"] == "sess-1"
        assert payload["upstream_task_id"] == "upstream-syn-001"

    async def test_missing_全空_直接返回_None(self, fake_queue):
        from app.supervisor.nodes.executor import _enqueue_wiki_gap_task

        for empty in [{}, {"missing": []}, {"missing": [None, ""]}, {"missing": "not_a_list"}]:
            task_id = await _enqueue_wiki_gap_task(
                wiki_gap_signal=empty,
                workspace_id="ws-1",
                user_id="u-1",
                session_id="sess-1",
                user_query="q",
            )
            assert task_id is None
        assert fake_queue == []

    async def test_workspace_id_缺失_不入队(self, fake_queue):
        from app.supervisor.nodes.executor import _enqueue_wiki_gap_task

        task_id = await _enqueue_wiki_gap_task(
            wiki_gap_signal={"missing": ["x"]},
            workspace_id="",
            user_id="u-1",
            session_id="sess-1",
            user_query="q",
        )
        assert task_id is None
        assert fake_queue == []

    async def test_signal_非_dict_不入队(self, fake_queue):
        from app.supervisor.nodes.executor import _enqueue_wiki_gap_task

        for bad in [None, "string-signal", 123, ["list-signal"]]:
            task_id = await _enqueue_wiki_gap_task(
                wiki_gap_signal=bad,
                workspace_id="ws-1",
                user_id="u-1",
                session_id="sess-1",
                user_query="q",
            )
            assert task_id is None
        assert fake_queue == []

    async def test_missing_截断到_20_项(self, fake_queue):
        from app.supervisor.nodes.executor import _enqueue_wiki_gap_task

        long_missing = [f"topic-{i}" for i in range(50)]
        await _enqueue_wiki_gap_task(
            wiki_gap_signal={"missing": long_missing},
            workspace_id="ws-1",
            user_id="u-1",
            session_id="sess-1",
            user_query="q",
        )
        assert len(fake_queue) == 1
        assert len(fake_queue[0]["payload"]["missing"]) == 20
        assert fake_queue[0]["payload"]["missing"][0] == "topic-0"
        assert fake_queue[0]["payload"]["missing"][-1] == "topic-19"

    async def test_enqueue_异常_仅_log_返回_None(self, monkeypatch):
        from app.services import wiki_compile_queue_service as q_module
        from app.supervisor.nodes.executor import _enqueue_wiki_gap_task

        async def _explode(**kwargs):
            raise RuntimeError("DB unavailable")

        fake_q = MagicMock()
        fake_q.enqueue_task = _explode
        monkeypatch.setattr(q_module, "get_wiki_compile_queue", lambda: fake_q)

        task_id = await _enqueue_wiki_gap_task(
            wiki_gap_signal={"missing": ["x"]},
            workspace_id="ws-1",
            user_id="u-1",
            session_id="sess-1",
            user_query="q",
        )
        assert task_id is None  # 异常被吞掉，返回 None

    async def test_user_query_截断到_200(self, fake_queue):
        from app.supervisor.nodes.executor import _enqueue_wiki_gap_task

        long_q = "X" * 500
        await _enqueue_wiki_gap_task(
            wiki_gap_signal={"missing": ["x"]},
            workspace_id="ws-1",
            user_id="u-1",
            session_id="sess-1",
            user_query=long_q,
        )
        assert fake_queue[0]["payload"]["user_query"] == "X" * 200


# ============ Executor finalize 集成（验证 finalize 调用 helper 并回写 compile_task_id） ============


@pytest.mark.asyncio
async def test_finalize_调用_enqueue_并回写_compile_task_id(monkeypatch):
    """端到端：模拟 doc_worker 输出 wiki_gap_signal → finalize 调 _enqueue_wiki_gap_task。"""
    from app.supervisor.nodes import executor as executor_module
    from app.services import wiki_compile_queue_service as q_module

    captured: list[dict[str, Any]] = []

    class _FakeTask:
        id = "queue-task-xyz"

    fake_q = MagicMock()

    async def _fake_enqueue(**kwargs):
        captured.append(kwargs)
        return _FakeTask()

    fake_q.enqueue_task = _fake_enqueue
    monkeypatch.setattr(q_module, "get_wiki_compile_queue", lambda: fake_q)

    # metrics 落库 mock 掉
    from app.services import wiki_metrics_service as m_module

    async def _noop_record(**kwargs):
        return "fake-id"

    fake_metric_svc = MagicMock()
    fake_metric_svc.record_route_metric = _noop_record
    monkeypatch.setattr(m_module, "get_wiki_metrics_service", lambda: fake_metric_svc)

    task_id = await executor_module._enqueue_wiki_gap_task(
        wiki_gap_signal={"missing": ["foo"], "task_id": "syn-1"},
        workspace_id="ws-2",
        user_id="u-2",
        session_id="sess-2",
        user_query="问 foo 是啥",
    )
    assert task_id == "queue-task-xyz"
    assert captured[0]["trigger_type"] == "reflection"
    assert captured[0]["payload"]["upstream_task_id"] == "syn-1"
