from __future__ import annotations

import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.office.handlers.base import TaskContext
from app.agents.office.handlers.template import TemplateHandler
from app.models.config.template import Template


async def _noop_async(**kwargs):
    return None


class _FakeRenderer:
    def __init__(self) -> None:
        self.calls = []

    async def render_to_path(self, template, context, output_path: str) -> str:
        self.calls.append(
            {
                "template_path": template.file_path,
                "context": context,
                "output_path": output_path,
            }
        )
        Path(output_path).write_text("ok", encoding="utf-8")
        return output_path


class _FakeStorageService:
    def __init__(self, local_template_path: str) -> None:
        self.local_template_path = local_template_path
        self.exists_calls = []
        self.materialize_calls = []

    async def exists(self, storage_path: str) -> bool:
        self.exists_calls.append(storage_path)
        return False

    @asynccontextmanager
    async def materialize(self, storage_path: str, *, suffix: str = "", filename: str | None = None):
        self.materialize_calls.append(
            {
                "storage_path": storage_path,
                "suffix": suffix,
                "filename": filename,
            }
        )
        yield self.local_template_path


@pytest.mark.asyncio
async def test_template_handler_materializes_oss_template_before_render(monkeypatch, tmp_path: Path):
    handler = TemplateHandler()
    renderer = _FakeRenderer()
    storage = _FakeStorageService(str(tmp_path / "template-local.docx"))
    template = Template(
        id=56,
        name="单一来源采购公示模板",
        file_path="oss://delu-agent/workspaces/default/templates/56/9be6b49a_0单一来源采购公示模板.docx",
        file_type="docx",
        workspace_id="default",
        created_by="user-1",
    )

    async def fake_get_template_async(template_id: int):
        assert template_id == 56
        return template

    async def fake_relevance(**kwargs):
        return {"is_relevant": True, "confidence": 0.99}

    fake_temp_artifact_module = types.SimpleNamespace(
        get_temp_artifact_service=lambda: types.SimpleNamespace(save_doc=_noop_async)
    )

    monkeypatch.setattr(
        "app.agents.office.handlers.template.get_template_async",
        fake_get_template_async,
    )
    monkeypatch.setattr(
        "app.agents.office.handlers.template.get_storage_service",
        lambda: storage,
    )
    monkeypatch.setattr(
        "app.agents.office.handlers.template.get_template_renderer",
        lambda: renderer,
    )
    monkeypatch.setattr(
        "app.agents.office.handlers.template.evaluate_generation_data_relevance",
        fake_relevance,
    )
    monkeypatch.setitem(sys.modules, "app.services.temp_artifact_service", fake_temp_artifact_module)

    ctx = TaskContext(
        task_description="生成公示文档",
        sandbox_path=str(tmp_path / "sandbox"),
        session_id="",
        user_id="user-1",
        template_id=56,
        memory_dfs={"template_context": {"project_name": "测试项目"}},
        user_context={"workspace_id": "default", "user_id": "user-1"},
        output_filename="result.docx",
    )

    result = await handler.handle(ctx)

    assert result["success"] is True
    assert storage.exists_calls == [
        "oss://delu-agent/workspaces/default/templates/56/compiled_56.docx"
    ]
    assert storage.materialize_calls == [
        {
            "storage_path": template.file_path,
            "suffix": ".docx",
            "filename": "9be6b49a_0单一来源采购公示模板.docx",
        }
    ]
    assert renderer.calls[0]["template_path"] == str(tmp_path / "template-local.docx")


def test_template_handler_builds_compiled_oss_path_with_forward_slashes():
    handler = TemplateHandler()

    compiled_path = handler._build_compiled_template_path(
        "oss://delu-agent/workspaces/default/templates/56/source.docx",
        56,
    )

    assert compiled_path == "oss://delu-agent/workspaces/default/templates/56/compiled_56.docx"


def test_template_handler_normalizes_alias_context_to_binding_keys():
    handler = TemplateHandler()
    template = Template(
        id=56,
        name="单一来源采购公示模板",
        file_path="oss://delu-agent/workspaces/default/templates/56/source.docx",
        file_type="docx",
        workspace_id="default",
        created_by="user-1",
        variables_schema={
            "f_0001": {"desc": "采购人", "type": "text"},
            "采购人": {"desc": "采购人：", "type": "text"},
        },
        bindings=[
            {"key": "f_0001", "label": "采购人", "type": "text"},
        ],
    )

    normalized = handler._normalize_template_context(
        template,
        {"采购人": "滨海市公安局"},
    )

    assert normalized == {"f_0001": "滨海市公安局"}
    assert handler._has_meaningful_render_values(template, normalized) is True


@pytest.mark.asyncio
async def test_template_handler_blocks_blank_document_when_render_values_are_empty(monkeypatch, tmp_path: Path):
    handler = TemplateHandler()
    template = Template(
        id=56,
        name="单一来源采购公示模板",
        file_path="oss://delu-agent/workspaces/default/templates/56/source.docx",
        file_type="docx",
        workspace_id="default",
        created_by="user-1",
        variables_schema={"f_0001": {"desc": "采购人", "type": "text"}},
        bindings=[{"key": "f_0001", "label": "采购人", "type": "text"}],
    )

    async def fake_get_template_async(template_id: int):
        assert template_id == 56
        return template

    async def fake_relevance(**kwargs):
        return {"is_relevant": True, "confidence": 0.99}

    monkeypatch.setattr(
        "app.agents.office.handlers.template.get_template_async",
        fake_get_template_async,
    )
    monkeypatch.setattr(
        "app.agents.office.handlers.template.evaluate_generation_data_relevance",
        fake_relevance,
    )

    ctx = TaskContext(
        task_description="生成公示文档",
        sandbox_path=str(tmp_path / "sandbox"),
        session_id="",
        user_id="user-1",
        template_id=56,
        memory_dfs={"template_context": {"采购人": ""}},
        user_context={"workspace_id": "default", "user_id": "user-1"},
        output_filename="result.docx",
    )

    result = await handler.handle(ctx)

    assert result["success"] is False
    assert result["error"] == "模板字段未提取到有效值，已阻止生成空白文档"
