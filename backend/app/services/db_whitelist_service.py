"""Workspace DB whitelist business service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.models.config.db_whitelist import (
    DBWhitelistEndpoint,
    WorkspaceDBWhitelist,
    get_workspace_db_whitelist_async,
    save_workspace_db_whitelist_async,
)


@dataclass(frozen=True)
class WhitelistCheckResult:
    allowed: bool
    is_enabled: bool
    normalized_host: str
    normalized_port: int
    detail: str = ""


class DBWhitelistService:
    """Manage and evaluate DB endpoint whitelist rules."""

    BLOCKED_CODE = "WHITELIST_BLOCKED"
    BLOCKED_MESSAGE = "\u6b64\u6570\u636e\u5e93\u672a\u901a\u8fc7\u767d\u540d\u5355\uff0c\u8bf7\u8054\u7cfb\u7ba1\u7406\u5458\u8fdb\u884c\u51c6\u5165\u5ba1\u6279\u3002"
    DEFAULT_PORT = 3306

    @classmethod
    def normalize_host(cls, host: Optional[str]) -> str:
        return (host or "").strip().lower()

    @classmethod
    def normalize_port(cls, port: Optional[int]) -> int:
        if port in (None, ""):
            return cls.DEFAULT_PORT
        try:
            parsed = int(port)
        except (TypeError, ValueError):
            return cls.DEFAULT_PORT
        return parsed if parsed > 0 else cls.DEFAULT_PORT

    @classmethod
    def normalize_endpoint(cls, host: Optional[str], port: Optional[int]) -> tuple[str, int]:
        return cls.normalize_host(host), cls.normalize_port(port)

    @classmethod
    def endpoint_key(cls, host: Optional[str], port: Optional[int]) -> str:
        n_host, n_port = cls.normalize_endpoint(host, port)
        return f"{n_host}:{n_port}"

    @classmethod
    def normalize_endpoints(cls, endpoints: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        seen = set()
        normalized: list[dict[str, Any]] = []
        for raw in endpoints or []:
            host, port = cls.normalize_endpoint(raw.get("host"), raw.get("port"))
            if not host:
                continue
            key = (host, port)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({"host": host, "port": port})
        return normalized

    async def get_workspace_config(self, workspace_id: str) -> Optional[WorkspaceDBWhitelist]:
        if not workspace_id:
            return None
        return await get_workspace_db_whitelist_async(workspace_id)

    async def update_workspace_config(
        self,
        workspace_id: str,
        *,
        is_enabled: bool,
        allowed_endpoints: list[dict[str, Any]] | None,
        note: Optional[str],
        updated_by: Optional[str],
    ) -> WorkspaceDBWhitelist:
        normalized_endpoints = self.normalize_endpoints(allowed_endpoints or [])
        return await save_workspace_db_whitelist_async(
            workspace_id,
            is_enabled=bool(is_enabled),
            allowed_endpoints=normalized_endpoints,
            note=(note or "").strip() or None,
            updated_by=updated_by,
        )

    async def check_endpoint_allowed(
        self,
        workspace_id: str,
        *,
        host: Optional[str],
        port: Optional[int],
    ) -> WhitelistCheckResult:
        n_host, n_port = self.normalize_endpoint(host, port)

        if not workspace_id:
            return WhitelistCheckResult(True, False, n_host, n_port)

        config = await self.get_workspace_config(workspace_id)
        if not config or not config.is_enabled:
            return WhitelistCheckResult(True, False, n_host, n_port)

        if not n_host:
            return WhitelistCheckResult(False, True, n_host, n_port, "missing_host")

        allowed_set = {
            self.endpoint_key(item.host, item.port)
            for item in (config.allowed_endpoints or [])
        }
        current_key = self.endpoint_key(n_host, n_port)

        if current_key in allowed_set:
            return WhitelistCheckResult(True, True, n_host, n_port)

        detail = (
            f"endpoint={current_key} not in whitelist for workspace={workspace_id}; "
            f"allowed={sorted(allowed_set)}"
        )
        return WhitelistCheckResult(False, True, n_host, n_port, detail)

    async def build_block_detail(
        self,
        *,
        workspace_id: str,
        user_id: Optional[str],
        host: Optional[str],
        port: Optional[int],
        route: str,
    ) -> str:
        key = self.endpoint_key(host, port)
        return f"workspace_id={workspace_id}, user_id={user_id or ''}, route={route}, endpoint={key}"


class DBWhitelistGetResponse(WorkspaceDBWhitelist):
    """Response model used by admin GET API."""


class DBWhitelistUpdateRequest(WorkspaceDBWhitelist):
    """Request model used by admin PUT API."""

    workspace_id: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DBWhitelistUpdateRequest":
        return cls(**payload)

    def as_service_payload(self) -> dict[str, Any]:
        return {
            "is_enabled": self.is_enabled,
            "allowed_endpoints": [
                {"host": endpoint.host, "port": endpoint.port}
                for endpoint in self.allowed_endpoints
            ],
            "note": self.note,
        }


__all__ = [
    "DBWhitelistService",
    "DBWhitelistEndpoint",
    "DBWhitelistGetResponse",
    "DBWhitelistUpdateRequest",
    "WhitelistCheckResult",
]
