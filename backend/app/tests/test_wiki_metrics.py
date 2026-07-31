"""[M3.5] WikiMetricsService 与 Executor 埋点单元测试。

覆盖：
- record_route_metric: 正常写入、workspace_id 缺失软降级、path 校正
- get_route_summary: 空数据、聚合正确性
- Executor._record_wiki_route_metrics: 仅处理 doc_worker、多 worker 不污染
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


# ============ 共用 fakes ============


class _FakeSession:
    """记录 add() 调用与 execute() 返回固定 rows 的 fake AsyncSession。"""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.adds: list[Any] = []
        self._rows = rows or []

    def add(self, obj: Any) -> None:
        self.adds.append(obj)

    async def flush(self) -> None:  # noqa: D401
        pass

    async def execute(self, stmt: Any) -> Any:  # noqa: ANN401
        result = MagicMock()
        result.all.return_value = list(self._rows)
        return result


class _FakeScope:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False


def _install_fake_db(monkeypatch, session: _FakeSession) -> None:
    """把 wiki_metrics_service 内部用到的 get_async_db_manager 换成返回 fake 管理器。"""
    from app.services import wiki_metrics_service as svc_module

    fake_mgr = MagicMock()
    fake_mgr.session_scope = lambda: _FakeScope(session)
    monkeypatch.setattr(svc_module, "get_async_db_manager", lambda: fake_mgr)


# ============ record_route_metric ============


@pytest.mark.asyncio
class TestRecordRouteMetric:
    async def test_正常写入返回_id(self, monkeypatch):
        from app.services.wiki_metrics_service import WikiMetricsService

        session = _FakeSession()
        _install_fake_db(monkeypatch, session)

        svc = WikiMetricsService()
        rid = await svc.record_route_metric(
            workspace_id="ws-1",
            knowledge_path="wiki",
            knowledge_path_source="rule",
            knowledge_path_reason="rule hit",
            session_id="sess-1",
            user_id="u-1",
            chunks_used=5,
            wiki_chunks_count=3,
            wiki_chunks_used=2,
            has_wiki_gap=True,
            wiki_gap_task_id="task-001",
            latency_ms=420,
            stop_reason="rag_ok",
            user_query="差旅报销?",
        )
        assert isinstance(rid, str) and rid
        assert len(session.adds) == 1
        record = session.adds[0]
        assert record.workspace_id == "ws-1"
        assert record.knowledge_path == "wiki"
        assert record.knowledge_path_source == "rule"
        assert record.has_wiki_gap is True
        assert record.wiki_gap_task_id == "task-001"
        assert record.latency_ms == 420
        assert record.user_query == "差旅报销?"

    async def test_workspace_id_缺失返回_None(self, monkeypatch):
        from app.services.wiki_metrics_service import WikiMetricsService

        session = _FakeSession()
        _install_fake_db(monkeypatch, session)

        svc = WikiMetricsService()
        rid = await svc.record_route_metric(workspace_id="", knowledge_path="wiki")
        assert rid is None
        assert session.adds == []

    async def test_非法_path_自动校正为_rag(self, monkeypatch):
        from app.services.wiki_metrics_service import WikiMetricsService

        session = _FakeSession()
        _install_fake_db(monkeypatch, session)

        svc = WikiMetricsService()
        rid = await svc.record_route_metric(
            workspace_id="ws-1", knowledge_path="invalid_path_xyz"
        )
        assert rid is not None
        assert session.adds[0].knowledge_path == "rag"

    async def test_user_query_截断到_200(self, monkeypatch):
        from app.services.wiki_metrics_service import WikiMetricsService

        session = _FakeSession()
        _install_fake_db(monkeypatch, session)

        svc = WikiMetricsService()
        long_q = "X" * 500
        await svc.record_route_metric(
            workspace_id="ws-1", knowledge_path="rag", user_query=long_q
        )
        assert session.adds[0].user_query is not None
        assert len(session.adds[0].user_query) == 200


# ============ get_route_summary ============


@pytest.mark.asyncio
class TestGetRouteSummary:
    async def test_空数据_total_为_0(self, monkeypatch):
        from app.services.wiki_metrics_service import WikiMetricsService

        session = _FakeSession(rows=[])
        _install_fake_db(monkeypatch, session)

        svc = WikiMetricsService()
        summary = await svc.get_route_summary(workspace_id="ws-1", days=7)
        assert summary["total"] == 0
        assert summary["window_days"] == 7
        assert summary["wiki_hit_rate"] == 0.0
        assert summary["wiki_gap_rate"] == 0.0
        assert summary["avg_latency_ms"] == 0
        # 三条桶都存在，count 为 0
        assert set(summary["by_path"].keys()) == {"rag", "wiki", "both"}
        for bucket in summary["by_path"].values():
            assert bucket["count"] == 0

    async def test_聚合_命中率_与_gap_率(self, monkeypatch):
        """rag=80 wiki=30 both=10 → wiki_hit=40/120=0.333, wiki_gap=5/120=0.042。"""
        from app.services.wiki_metrics_service import WikiMetricsService

        # rows: (knowledge_path, count, avg_latency, wiki_gap_count)
        rows = [
            ("rag", 80, 450.0, 0),
            ("wiki", 30, 380.0, 3),
            ("both", 10, 600.0, 2),
        ]
        session = _FakeSession(rows=rows)
        _install_fake_db(monkeypatch, session)

        svc = WikiMetricsService()
        summary = await svc.get_route_summary(workspace_id="ws-1", days=7)
        assert summary["total"] == 120
        assert summary["by_path"]["rag"]["count"] == 80
        assert summary["by_path"]["rag"]["avg_latency_ms"] == 450
        assert summary["by_path"]["wiki"]["count"] == 30
        assert summary["by_path"]["wiki"]["wiki_gap_count"] == 3
        assert summary["by_path"]["both"]["count"] == 10

        wiki_hit_rate = summary["wiki_hit_rate"]
        assert abs(wiki_hit_rate - (40 / 120)) < 1e-6

        wiki_gap_rate = summary["wiki_gap_rate"]
        assert abs(wiki_gap_rate - (5 / 120)) < 1e-6

        # avg_latency_ms = (80*450 + 30*380 + 10*600) / 120 = 53400/120 = 445
        assert summary["avg_latency_ms"] == 445

    async def test_days_参数_自动夹取_1_到_90(self, monkeypatch):
        from app.services.wiki_metrics_service import WikiMetricsService

        session = _FakeSession(rows=[])
        _install_fake_db(monkeypatch, session)
        svc = WikiMetricsService()

        s0 = await svc.get_route_summary(workspace_id="ws-1", days=0)
        assert s0["window_days"] == 1

        s_big = await svc.get_route_summary(workspace_id="ws-1", days=999)
        assert s_big["window_days"] == 90


# ============ Executor._record_wiki_route_metrics ============


@pytest.mark.asyncio
class TestExecutorRecordMetrics:
    async def test_仅处理_doc_worker_落库(self, monkeypatch):
        """sql_worker / chart_worker 不写 metrics；多个 doc_worker 各写一行。"""
        from app.supervisor.nodes import executor as executor_module

        recorded: list[dict[str, Any]] = []

        async def fake_record(**kwargs):
            recorded.append(kwargs)
            return "fake-id"

        fake_svc = MagicMock()
        fake_svc.record_route_metric = fake_record
        from app.services import wiki_metrics_service as svc_module
        monkeypatch.setattr(svc_module, "get_wiki_metrics_service", lambda: fake_svc)

        results = [
            {
                "step_id": "s1",
                "worker": "sql_worker",
                "meta": {"chunks_used": 0, "latency_ms": 100},
            },
            {
                "step_id": "s2",
                "worker": "doc_worker",
                "meta": {
                    "chunks_used": 5,
                    "wiki_chunks_count": 2,
                    "wiki_chunks_used": 1,
                    "knowledge_path": "wiki",
                    "latency_ms": 350,
                    "stop_reason": "rag_ok",
                    "wiki_gap_signal": {"task_id": "t-1"},
                },
            },
            {
                "step_id": "s3",
                "worker": "doc_worker",
                "meta": {
                    "chunks_used": 3,
                    "knowledge_path": "rag",
                    "latency_ms": 220,
                },
            },
            {
                "step_id": "s4",
                "worker": "chart_worker",
                "meta": {},
            },
        ]
        state = {
            "user_query": "Q",
            "knowledge_path": "rag",
            "knowledge_path_source": "rule",
            "knowledge_path_reason": "测试",
        }

        await executor_module._record_wiki_route_metrics(
            results=results,
            state=state,
            workspace_id="ws-1",
            session_id="sess-1",
            user_id="u-1",
        )

        assert len(recorded) == 2
        # 第一条来自 s2 (wiki + gap)
        first = recorded[0]
        assert first["knowledge_path"] == "wiki"
        assert first["has_wiki_gap"] is True
        assert first["wiki_gap_task_id"] == "t-1"
        assert first["chunks_used"] == 5
        assert first["latency_ms"] == 350
        assert first["message_id"] == "s2"
        # 第二条来自 s3 (rag, 无 gap)
        second = recorded[1]
        assert second["knowledge_path"] == "rag"
        assert second["has_wiki_gap"] is False
        assert second["wiki_gap_task_id"] is None
        assert second["latency_ms"] == 220

    async def test_无_doc_worker_直接返回_不写库(self, monkeypatch):
        from app.supervisor.nodes import executor as executor_module

        recorded: list[dict[str, Any]] = []

        async def fake_record(**kwargs):
            recorded.append(kwargs)
            return "fake-id"

        fake_svc = MagicMock()
        fake_svc.record_route_metric = fake_record
        from app.services import wiki_metrics_service as svc_module
        monkeypatch.setattr(svc_module, "get_wiki_metrics_service", lambda: fake_svc)

        await executor_module._record_wiki_route_metrics(
            results=[{"step_id": "s1", "worker": "sql_worker", "meta": {}}],
            state={"user_query": "Q"},
            workspace_id="ws-1",
            session_id="sess-1",
            user_id="u-1",
        )
        assert recorded == []

    async def test_workspace_id_缺失_直接返回(self, monkeypatch):
        from app.supervisor.nodes import executor as executor_module

        recorded: list[dict[str, Any]] = []

        async def fake_record(**kwargs):
            recorded.append(kwargs)
            return "fake-id"

        fake_svc = MagicMock()
        fake_svc.record_route_metric = fake_record
        from app.services import wiki_metrics_service as svc_module
        monkeypatch.setattr(svc_module, "get_wiki_metrics_service", lambda: fake_svc)

        await executor_module._record_wiki_route_metrics(
            results=[{"step_id": "s1", "worker": "doc_worker", "meta": {}}],
            state={"user_query": "Q"},
            workspace_id="",
            session_id="sess-1",
            user_id="u-1",
        )
        assert recorded == []
