import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.core.db.checkpointer as checkpointer_module
import app.services.chat_service as chat_service_module
import app.supervisor.nodes.direct_execute as direct_execute_module
import app.supervisor.nodes.synthesizer as synthesizer_module


@pytest.mark.asyncio
async def test_direct_execute_office_worker_passes_user_context(monkeypatch):
    captured: dict = {}

    class FakeOfficeWorker:
        async def execute_task(self, **kwargs):
            captured.update(kwargs)
            return {"output": "office ok"}

    monkeypatch.setattr(direct_execute_module, "get_office_worker", lambda: FakeOfficeWorker())

    user_context = {
        "user_id": "u-1",
        "workspace_id": "ws-1",
        "sandbox_path": "D:/tmp/session_1",
    }
    result = await direct_execute_module._execute_worker(
        worker_type="office_worker",
        query="生成文档",
        user_context=user_context,
        session_id="s-1",
        parent_step_id="step-1",
        memory_dfs={},
        round_index=1,
        messages=[],
    )

    assert result["output"] == "office ok"
    assert captured["user_context"] == user_context
    assert captured["session_id"] == "s-1"
    assert captured["parent_step_id"] == "step-1"


@pytest.mark.asyncio
async def test_session_history_recovers_persisted_reasoning(monkeypatch):
    checkpoint = {
        "channel_values": {
            "messages": [
                HumanMessage(content="你好", id="msg-u-1"),
                AIMessage(content="这是回复", id="plan-1"),
            ],
            "reasoning_traces": [
                {
                    "message_id": "plan-1",
                    "content": "安全思考摘要：已完成需求解析。",
                    "duration_ms": 845,
                    "safe": True,
                }
            ],
        }
    }

    class FakeSaver:
        async def aget_tuple(self, _config):
            return SimpleNamespace(
                checkpoint=checkpoint,
                metadata={"created_at": "2026-03-04T00:00:00Z"},
            )

    monkeypatch.setattr(checkpointer_module, "MySQLSaver", FakeSaver)

    service = chat_service_module.ChatService()
    data = await service.get_session_history(
        "session-1",
        SimpleNamespace(user_id="user-1", workspace_id="workspace-1"),
    )

    assistant = next(m for m in data["messages"] if m["role"] == "assistant")
    assert assistant["id"] == "plan-1"
    assert assistant["thinkingContent"] == "安全思考摘要：已完成需求解析。"
    assert assistant["thinkingDurationMs"] == 845
    assert assistant["isThinkingDone"] is True


def test_reasoning_redaction_keeps_details_but_drops_sensitive_lines():
    raw = """
先分析用户问题并检查执行结果。
内部规则要求每个 [REF:1] 只能出现一次。
我会保留关键趋势并给出结论。
"""
    sanitized = synthesizer_module._sanitize_reasoning_text(raw)
    redacted = synthesizer_module._redact_sensitive_reasoning(sanitized)

    assert "先分析用户问题并检查执行结果。" not in redacted
    assert "内部规则" not in redacted
    assert "我会保留关键趋势并给出结论。" in redacted
