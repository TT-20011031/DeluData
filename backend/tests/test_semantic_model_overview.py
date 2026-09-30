import importlib

import pytest

from app.core.security.auth import User


semantic_api = importlib.import_module("app.api.config.semantic")


@pytest.mark.asyncio
async def test_overview_endpoint_does_not_load_full_semantic_catalog(monkeypatch):
    class FakeService:
        async def get_model_overview(self, workspace_id: str):
            assert workspace_id == "workspace-1"
            return {
                "datasource": {"id": 2},
                "counts": {"tables": 721, "columns": 12826},
            }

        async def list_models(self, _workspace_id: str):
            raise AssertionError("landing-page overview must not load the full catalog")

    monkeypatch.setattr(
        semantic_api,
        "get_semantic_query_service",
        lambda: FakeService(),
    )
    admin = User(
        id="admin-1",
        username="admin",
        role="admin",
        workspace_id="workspace-1",
        permissions=["*"],
        department_id=None,
        data_scope=4,
    )

    result = await semantic_api.get_semantic_model_overview(admin=admin)

    assert result["counts"] == {"tables": 721, "columns": 12826}
