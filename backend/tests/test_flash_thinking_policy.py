from types import SimpleNamespace

import pytest

import app.core.llm.async_llm as async_llm_module
import app.core.llm.llm as llm_module
import app.tools.chart_tool as chart_tool_module
from app.core.llm.async_llm import AsyncLLMClient
from app.core.llm.llm import LLMClient
from app.services.chat_service import _resolve_reply_model_selection


def _fake_chat_response(content: str = "ok"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ]
    )


@pytest.mark.asyncio
async def test_async_llm_disables_thinking_for_flash(monkeypatch):
    captured: dict = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _fake_chat_response()

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(async_llm_module, "AsyncOpenAI", FakeAsyncOpenAI)

    client = AsyncLLMClient()
    await client.chat([{"role": "user", "content": "hi"}], model="qwen3.5-flash")

    assert captured["extra_body"]["enable_thinking"] is False


@pytest.mark.asyncio
async def test_async_llm_keeps_non_flash_default(monkeypatch):
    captured: dict = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _fake_chat_response()

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(async_llm_module, "AsyncOpenAI", FakeAsyncOpenAI)

    client = AsyncLLMClient()
    await client.chat([{"role": "user", "content": "hi"}], model="qwen3.5-plus")

    assert "extra_body" not in captured


@pytest.mark.asyncio
async def test_async_llm_merges_extra_body_for_flash(monkeypatch):
    captured: dict = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _fake_chat_response()

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(async_llm_module, "AsyncOpenAI", FakeAsyncOpenAI)

    client = AsyncLLMClient()
    await client.chat(
        [{"role": "user", "content": "hi"}],
        model="qwen3.5-flash",
        extra_body={"foo": "bar"},
    )

    assert captured["extra_body"] == {"foo": "bar", "enable_thinking": False}


@pytest.mark.asyncio
async def test_legacy_llm_disables_thinking_for_flash(monkeypatch):
    captured: dict = {}

    def fake_generation_call(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            output=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
            ),
        )

    monkeypatch.setattr(llm_module.Generation, "call", fake_generation_call)

    client = LLMClient(model_name="qwen3.5-flash")
    await client.chat([{"role": "user", "content": "hi"}])

    assert captured["enable_thinking"] is False


def test_reply_model_selection_uses_controlled_keys():
    settings = SimpleNamespace(
        llm=SimpleNamespace(
            final_reply_model_default_key="flash",
            final_reply_model_flash="qwen3.5-flash",
            final_reply_model_plus="qwen3.5-plus",
            final_reply_model_max="qwen3.7-max",
            final_reply_model="qwen3.5-flash",
        )
    )

    assert _resolve_reply_model_selection(settings, "plus") == ("plus", "qwen3.5-plus")
    assert _resolve_reply_model_selection(settings, "flash") == ("flash", "qwen3.5-flash")
    assert _resolve_reply_model_selection(settings, "max") == ("max", "qwen3.7-max")
    assert _resolve_reply_model_selection(settings, "anything") == ("flash", "qwen3.5-flash")


@pytest.mark.asyncio
async def test_chart_worker_uses_dedicated_model_without_thinking(monkeypatch, tmp_path):
    captured: dict = {}

    class FakeLLM:
        async def chat(self, messages, **kwargs):
            captured["messages"] = messages
            captured.update(kwargs)
            return "<!DOCTYPE html><html><body>ok</body></html>"

    async def fake_relevance(**kwargs):
        return {"is_relevant": True, "confidence": 0.95}

    monkeypatch.setattr(chart_tool_module, "get_async_llm", lambda: FakeLLM())
    monkeypatch.setattr(chart_tool_module, "evaluate_generation_data_relevance", fake_relevance)
    monkeypatch.setattr(
        chart_tool_module,
        "select_primary_generation_source",
        lambda *args, **kwargs: SimpleNamespace(
            key="df_demo",
            preview_text="name,value\nA,1",
            selected_items=1,
            total_items=1,
            source_type="dataframe",
            reason="matched",
        ),
    )
    monkeypatch.setattr(
        chart_tool_module,
        "get_settings",
        lambda: SimpleNamespace(
            llm=SimpleNamespace(
                chart_worker_model="qwen3.5-plus",
                chart_worker_enable_thinking=False,
                synthesizer_model="qwen3.5-flash",
            ),
            sandbox=SimpleNamespace(base_dir=str(tmp_path)),
        ),
    )

    result = await chart_tool_module.run_chart_task(
        query="生成一个图表",
        user_context={
            "sandbox_path": str(tmp_path),
            "workspace_id": "workspace-test",
            "user_id": "user-test",
        },
        memory_dfs={"df_demo": [{"name": "A", "value": 1}]},
    )

    assert captured["model"] == "qwen3.5-plus"
    assert captured["extra_body"] == {"enable_thinking": False}
    assert result.output.startswith("已生成数据可视化图表")
