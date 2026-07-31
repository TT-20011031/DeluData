import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.models.config.template as template_model_module
import app.supervisor.nodes.executor as executor_module


@pytest.mark.asyncio
async def test_sanitize_office_step_params_keeps_valid_template_without_skill(monkeypatch):
    async def fake_get_template_async(template_id: int):
        assert template_id == 56
        return SimpleNamespace(workspace_id="ws-1")

    monkeypatch.setattr(template_model_module, "get_template_async", fake_get_template_async)

    step = {"params": {"template_id": 56, "template_mode": "render"}}
    result = await executor_module._sanitize_office_step_params(
        step=step,
        state={"selected_skill_id": None, "skill_mode": False},
        user_context={"workspace_id": "ws-1"},
        template_cache={},
    )

    assert result["template_id"] == 56
    assert result["template_mode"] == "render"
    assert step["params"]["template_id"] == 56


@pytest.mark.asyncio
async def test_sanitize_office_step_params_drops_cross_workspace_template(monkeypatch):
    async def fake_get_template_async(template_id: int):
        assert template_id == 56
        return SimpleNamespace(workspace_id="other-ws")

    monkeypatch.setattr(template_model_module, "get_template_async", fake_get_template_async)

    step = {"params": {"template_id": 56, "template_mode": "render"}}
    result = await executor_module._sanitize_office_step_params(
        step=step,
        state={"selected_skill_id": None, "skill_mode": False},
        user_context={"workspace_id": "ws-1"},
        template_cache={},
    )

    assert "template_id" not in result
    assert "template_mode" not in result
    assert "template_id" not in step["params"]
