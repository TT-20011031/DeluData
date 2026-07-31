from types import SimpleNamespace

import pytest

from app.agents import sql_worker as sql_worker_module
from app.agents.sql_worker import SqlWorker
from app.services.semantic_query_service import SemanticQueryError


class _FakeSemanticService:
    def __init__(self, mode: str, *, error: SemanticQueryError | None = None):
        self.datasource = SimpleNamespace(
            runtime_mode=mode,
            semantic_sql_enabled=mode != "disabled",
            semantic_sql_fallback_enabled=mode == "shadow",
        )
        self.error = error
        self.records = []

    async def get_active_datasource(self, _workspace_id):
        return self.datasource

    async def execute_semantic_query(self, **_kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(
            sql="SELECT 1 LIMIT 1",
            row_count=1,
            execution_time_ms=8,
            columns=["value"],
            data=[[1]],
        )

    async def record_query_run(self, **payload):
        self.records.append(payload)
        return len(self.records)


@pytest.mark.asyncio
async def test_shadow_mode_returns_legacy_and_records_comparison(monkeypatch):
    service = _FakeSemanticService("shadow")
    monkeypatch.setattr(sql_worker_module, "get_semantic_query_service", lambda: service)
    monkeypatch.setattr(sql_worker_module, "_resolve_workspace_id_for_user", _async_value("w1"))
    monkeypatch.setattr(sql_worker_module, "_requires_semantic_guard", _async_value(False))
    worker = SqlWorker()
    original = worker.execute_task

    async def dispatch(*args, **kwargs):
        if kwargs.get("run_mode") == "legacy_only":
            return {"success": True, "sql": "SELECT 2 LIMIT 1", "row_count": 1, "referenced_tables": []}
        return await original(*args, **kwargs)

    worker.execute_task = dispatch
    result = await worker.execute_task("查订单", "u1")

    assert result["sql"] == "SELECT 2 LIMIT 1"
    comparison = result["semantic_shadow"]["comparison"]
    assert comparison["semantic"]["success"] is True
    assert comparison["legacy"]["success"] is True
    assert service.records[-1]["returned_chain"] == "legacy"
    assert service.records[-1]["runtime_mode"] == "shadow"


@pytest.mark.asyncio
async def test_trusted_mode_blocks_clarification_fallback(monkeypatch):
    service = _FakeSemanticService(
        "trusted",
        error=SemanticQueryError("clarification_required", "需要确认指标", safe_to_fallback=True),
    )
    monkeypatch.setattr(sql_worker_module, "get_semantic_query_service", lambda: service)
    monkeypatch.setattr(sql_worker_module, "_resolve_workspace_id_for_user", _async_value("w1"))
    monkeypatch.setattr(sql_worker_module, "_requires_semantic_guard", _async_value(False))

    result = await SqlWorker().execute_task("查销售", "u1")

    assert result["success"] is False
    assert result["error_type"] == "clarification_required"
    assert result["semantic_fallback_blocked"] is True


@pytest.mark.asyncio
async def test_member_cannot_fallback_to_legacy_sql(monkeypatch):
    service = _FakeSemanticService(
        "shadow",
        error=SemanticQueryError("clarification_required", "需要确认指标", safe_to_fallback=True),
    )
    monkeypatch.setattr(sql_worker_module, "get_semantic_query_service", lambda: service)
    monkeypatch.setattr(sql_worker_module, "_resolve_workspace_id_for_user", _async_value("w1"))
    monkeypatch.setattr(sql_worker_module, "_requires_semantic_guard", _async_value(True))

    result = await SqlWorker().execute_task("查销售", "u1")

    assert result["success"] is False
    assert result["error_type"] == "semantic_unavailable"
    assert result["semantic_fallback_blocked"] is True


def _async_value(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner
