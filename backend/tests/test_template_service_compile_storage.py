from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.config.template import Template
from app.services.template.service import TemplateService


class _FakeRenderer:
    def __init__(self, compiled_local_path: Path) -> None:
        self.compiled_local_path = compiled_local_path
        self.calls = []

    async def compile(self, template, bindings, output_name=None):
        self.calls.append(
            {
                "template_path": template.file_path,
                "bindings": bindings,
                "output_name": output_name,
            }
        )
        self.compiled_local_path.write_text("compiled", encoding="utf-8")
        return str(self.compiled_local_path), "compiled_56.docx"


class _FakeStorageService:
    def __init__(self) -> None:
        self.calls = []

    async def replace_from_local_file(self, storage_path: str, local_path: str, *, content_type=None):
        self.calls.append(
            {
                "storage_path": storage_path,
                "local_path": local_path,
                "content_type": content_type,
            }
        )
        return storage_path


class _TestTemplateService(TemplateService):
    def __init__(self, local_source_path: Path):
        super().__init__()
        self.local_source_path = local_source_path

    @asynccontextmanager
    async def _materialized_template(self, template: Template):
        yield template.model_copy(update={"file_path": str(self.local_source_path)})


@pytest.mark.asyncio
async def test_compile_template_persists_compiled_docx_to_storage(tmp_path: Path):
    local_source_path = tmp_path / "source.docx"
    local_source_path.write_text("source", encoding="utf-8")
    local_compiled_path = tmp_path / "compiled_56.docx"

    service = _TestTemplateService(local_source_path)
    service._renderer = _FakeRenderer(local_compiled_path)
    service._storage_service = _FakeStorageService()

    template = Template(
        id=56,
        name="单一来源采购公示模板",
        file_path="oss://delu-agent/workspaces/default/templates/56/source.docx",
        file_type="docx",
        workspace_id="default",
        created_by="user-1",
    )

    compiled_path, compiled_name = await service.compile_template(
        template,
        [{"key": "f_0001", "label": "采购人", "type": "text"}],
    )

    assert compiled_name == "compiled_56.docx"
    assert compiled_path == "oss://delu-agent/workspaces/default/templates/56/compiled_56.docx"
    assert service._storage_service.calls == [
        {
            "storage_path": "oss://delu-agent/workspaces/default/templates/56/compiled_56.docx",
            "local_path": str(local_compiled_path),
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    ]
    assert local_compiled_path.exists() is False
