import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from app.models.config.db_whitelist import DBWhitelistEndpoint, WorkspaceDBWhitelist
from app.services.db_whitelist_service import DBWhitelistService


def test_normalize_endpoint_and_key():
    service = DBWhitelistService()

    host, port = service.normalize_endpoint("  DB.EXAMPLE.COM  ", None)
    assert host == "db.example.com"
    assert port == 3306
    assert service.endpoint_key(host, port) == "db.example.com:3306"


def test_normalize_endpoints_deduplicates_and_skips_invalid():
    service = DBWhitelistService()

    normalized = service.normalize_endpoints(
        [
            {"host": " DB.EXAMPLE.COM ", "port": 3306},
            {"host": "db.example.com", "port": 3306},
            {"host": "", "port": 3306},
            {"host": "db.internal", "port": None},
        ]
    )

    assert normalized == [
        {"host": "db.example.com", "port": 3306},
        {"host": "db.internal", "port": 3306},
    ]


@pytest.mark.asyncio
async def test_check_endpoint_allowed_when_whitelist_disabled(monkeypatch):
    service = DBWhitelistService()

    async def _fake_config(_workspace_id: str):
        return WorkspaceDBWhitelist(
            workspace_id="ws-1",
            is_enabled=False,
            allowed_endpoints=[DBWhitelistEndpoint(host="db.example.com", port=3306)],
        )

    monkeypatch.setattr(service, "get_workspace_config", _fake_config)

    result = await service.check_endpoint_allowed(
        "ws-1",
        host="db.other.com",
        port=3306,
    )

    assert result.allowed is True
    assert result.is_enabled is False


@pytest.mark.asyncio
async def test_check_endpoint_allowed_when_not_in_whitelist(monkeypatch):
    service = DBWhitelistService()

    async def _fake_config(_workspace_id: str):
        return WorkspaceDBWhitelist(
            workspace_id="ws-1",
            is_enabled=True,
            allowed_endpoints=[DBWhitelistEndpoint(host="db.example.com", port=3306)],
        )

    monkeypatch.setattr(service, "get_workspace_config", _fake_config)

    result = await service.check_endpoint_allowed(
        "ws-1",
        host="db.other.com",
        port=3306,
    )

    assert result.allowed is False
    assert result.is_enabled is True
    assert "not in whitelist" in result.detail


@pytest.mark.asyncio
async def test_check_endpoint_allowed_when_match(monkeypatch):
    service = DBWhitelistService()

    async def _fake_config(_workspace_id: str):
        return WorkspaceDBWhitelist(
            workspace_id="ws-1",
            is_enabled=True,
            allowed_endpoints=[DBWhitelistEndpoint(host="db.example.com", port=3306)],
        )

    monkeypatch.setattr(service, "get_workspace_config", _fake_config)

    result = await service.check_endpoint_allowed(
        "ws-1",
        host="DB.EXAMPLE.COM",
        port=3306,
    )

    assert result.allowed is True
    assert result.is_enabled is True
