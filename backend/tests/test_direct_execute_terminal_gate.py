import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.supervisor.nodes.direct_execute as direct_execute_module
from app.services.workspace_readiness_service import DBReadiness, KnowledgeReadiness, WorkspaceReadiness


def _ready(has_db: bool = True, has_knowledge: bool = True) -> WorkspaceReadiness:
    return WorkspaceReadiness(
        has_db=has_db,
        has_knowledge=has_knowledge,
        db=DBReadiness(connected=has_db),
        knowledge=KnowledgeReadiness(total_count=1 if has_knowledge else 0),
        reasons=[],
    )


@pytest.mark.asyncio
async def test_chart_only_blocks_when_only_sandbox_path_present(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    async def _fake_resolve_runtime_readiness(_state, *, default_available=False):
        return _ready(has_db=False, has_knowledge=False)

    async def _should_not_execute_worker(**_kwargs):
        raise AssertionError("terminal worker should be blocked before execution")

    monkeypatch.setattr(direct_execute_module, "emit_step_update", _noop)
    monkeypatch.setattr(direct_execute_module, "emit_plan_complete", _noop)
    monkeypatch.setattr(direct_execute_module, "resolve_runtime_readiness", _fake_resolve_runtime_readiness)
    monkeypatch.setattr(direct_execute_module, "_execute_worker", _should_not_execute_worker)

    output = await direct_execute_module.direct_execute_node(
        {
            "execution_mode": "chart_only",
            "user_query": "画图",
            "user_context": {"sandbox_path": "D:/tmp/session_1"},
            "session_id": "s1",
            "memory_dfs": {},
            "round_index": 0,
            "messages": [],
        }
    )

    assert output["plan_status"] == "error"
    assert "至少需要一种数据源" in output["error"]


@pytest.mark.asyncio
async def test_chart_only_allows_uploaded_file_without_memory(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    async def _fake_resolve_runtime_readiness(_state, *, default_available=False):
        return _ready()

    async def _fake_execute_worker(**_kwargs):
        return {"output": "ok", "memory_update": {}, "meta": {}}

    monkeypatch.setattr(direct_execute_module, "emit_step_update", _noop)
    monkeypatch.setattr(direct_execute_module, "emit_plan_complete", _noop)
    monkeypatch.setattr(direct_execute_module, "resolve_runtime_readiness", _fake_resolve_runtime_readiness)
    monkeypatch.setattr(direct_execute_module, "_execute_worker", _fake_execute_worker)

    output = await direct_execute_module.direct_execute_node(
        {
            "execution_mode": "chart_only",
            "user_query": "画图",
            "user_context": {
                "sandbox_path": "D:/tmp/session_1",
                "file_path": "D:/tmp/session_1/input.csv",
            },
            "session_id": "s1",
            "memory_dfs": {},
            "round_index": 0,
            "messages": [],
        }
    )

    assert output["plan_status"] == "completed"
    assert output["task_plan"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_chart_only_allows_when_only_knowledge_source_available(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    async def _fake_resolve_runtime_readiness(_state, *, default_available=False):
        return _ready(has_db=False, has_knowledge=True)

    async def _fake_execute_worker(**_kwargs):
        return {"output": "ok", "memory_update": {}, "meta": {}}

    monkeypatch.setattr(direct_execute_module, "emit_step_update", _noop)
    monkeypatch.setattr(direct_execute_module, "emit_plan_complete", _noop)
    monkeypatch.setattr(direct_execute_module, "resolve_runtime_readiness", _fake_resolve_runtime_readiness)
    monkeypatch.setattr(direct_execute_module, "_execute_worker", _fake_execute_worker)

    output = await direct_execute_module.direct_execute_node(
        {
            "execution_mode": "chart_only",
            "user_query": "画图",
            "user_context": {"sandbox_path": "D:/tmp/session_1"},
            "session_id": "s1",
            "memory_dfs": {},
            "round_index": 0,
            "messages": [],
        }
    )

    assert output["plan_status"] == "completed"
    assert output["task_plan"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_direct_sql_execution_passes_current_question_as_original_query(monkeypatch):
    observed = {}

    class FakeWorker:
        async def execute_task(self, **kwargs):
            observed.update(kwargs)
            return {"success": True, "row_count": 1, "data": [{"ok": 1}], "columns": ["ok"], "artifacts": {}}

        @staticmethod
        def format_result_for_synthesizer(result):
            return "ok"

    monkeypatch.setattr(direct_execute_module, "get_sql_worker", lambda: FakeWorker())

    await direct_execute_module._execute_worker(
        worker_type="sql_worker",
        query="按月统计销售金额趋势",
        user_context={"user_id": "u1", "workspace_id": "w1"},
        session_id="s1",
        parent_step_id="step-1",
        memory_dfs={},
        round_index=0,
    )

    assert observed["task_description"] == "按月统计销售金额趋势"
    assert observed["original_query"] == "按月统计销售金额趋势"
