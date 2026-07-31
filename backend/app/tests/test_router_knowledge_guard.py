import pytest

from app.supervisor.nodes.router import router_agent_node


@pytest.mark.asyncio
async def test_doc_worker_empty_does_not_switch_to_sql(monkeypatch):
    async def fake_humanize(action: str, reason_code: str) -> str:
        return f"{action}:{reason_code}"

    monkeypatch.setattr(
        "app.supervisor.nodes.router._humanize_router_thought",
        fake_humanize,
    )

    state = {
        "task_plan": [
            {
                "step_id": "1",
                "worker": "doc_worker",
                "status": "completed",
                "params": {},
            }
        ],
        "retry_count": 0,
        "_max_retries": 2,
        "last_executed_step": {"step_id": "1", "worker": "doc_worker", "error": False},
        "latest_quality_signal": {
            "verdict": "partial",
            "reason_code": "rag_empty",
            "confidence": 0.95,
            "retryable": False,
        },
        "user_query": "请介绍 wiki-first 检索策略",
        "session_id": "",
        "round_index": 0,
        "tried_workers": ["doc_worker"],
        "plan_status": "completed",
    }

    updates = await router_agent_node(state)

    assert updates["route_action"] == "finish"
    assert updates["latest_quality_signal"]["wrong_tool_repair"] is False
    trace = updates["latest_quality_signal"]["tool_repair_trace"][0]
    assert trace["knowledge_chain_guard"] is True
    assert trace["route_action"] == "finish"
    assert state["task_plan"][0]["worker"] == "doc_worker"
