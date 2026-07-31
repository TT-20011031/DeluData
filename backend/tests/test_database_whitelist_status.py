import sys
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.security.auth import User

db_router = importlib.import_module("app.api.database.router")


class _FakeDBConfig:
    is_active = True
    host = "db.example.com"
    port = 3306
    database = "prod"

    @staticmethod
    def get_connection_url() -> str:
        raise AssertionError("status endpoint should not connect when whitelist blocks")


@pytest.mark.asyncio
async def test_status_returns_disconnected_when_whitelist_blocked(monkeypatch):
    async def _fake_get_user_db_config_async(_user_id: str):
        return _FakeDBConfig()

    async def _fake_ensure_whitelist_allowed(**_kwargs):
        return object()  # truthy means blocked

    monkeypatch.setattr(db_router, "get_user_db_config_async", _fake_get_user_db_config_async)
    monkeypatch.setattr(db_router, "_ensure_whitelist_allowed", _fake_ensure_whitelist_allowed)

    current_user = User(
        id="u1",
        username="tester",
        role="user",
        workspace_id="ws1",
        permissions=[],
        department_id=None,
        data_scope=4,
    )
    result = await db_router.get_connection_status(current_user=current_user)

    assert result.is_connected is False
