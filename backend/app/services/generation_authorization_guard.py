"""Prevent chart and Office workers from reusing stale protected SQL rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.db.database import get_async_db_context
from app.models.auth.rbac import UserModel
from app.services.authorization_service import build_effective_access_context


LINEAGE_ATTR = "deludata_authorization"


def stamp_semantic_dataframe(
    dataframe: Any,
    *,
    user_id: str,
    workspace_id: str,
    authorization_revision: int,
    authorization_valid_until: str | None = None,
) -> Any:
    """Attach non-column lineage, so it never appears in generated artifacts."""
    attrs = getattr(dataframe, "attrs", None)
    if isinstance(attrs, dict):
        attrs[LINEAGE_ATTR] = {
            "kind": "semantic_sql",
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "authorization_revision": int(authorization_revision or 0),
            "authorization_valid_until": authorization_valid_until,
        }
    return dataframe


def lineage_rejection_reason(
    lineage: dict[str, Any], *, user_id: str, authorization_revision: int,
    now: datetime | None = None,
) -> str | None:
    if lineage.get("kind") != "semantic_sql":
        return None
    if str(lineage.get("user_id") or "") != str(user_id):
        return "protected_source_user_mismatch"
    if int(lineage.get("authorization_revision") or 0) != int(authorization_revision):
        return "protected_source_authorization_changed"
    valid_until = lineage.get("authorization_valid_until")
    if valid_until:
        try:
            expires_at = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
            current = now or datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if current >= expires_at:
                return "protected_source_authorization_expired"
        except ValueError:
            return "protected_source_authorization_expired"
    return None


async def filter_authorized_generation_sources(
    memory_dfs: dict[str, Any] | None,
    user_id: str,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Remove only protected sources that no longer match current authorization."""
    source = dict(memory_dfs or {})
    protected = {
        key: getattr(value, "attrs", {}).get(LINEAGE_ATTR)
        for key, value in source.items()
        if isinstance(getattr(value, "attrs", None), dict)
        and getattr(value, "attrs", {}).get(LINEAGE_ATTR)
    }
    if not protected:
        return source, []

    async with get_async_db_context() as session:
        user = await session.get(UserModel, str(user_id))
        if not user:
            current_revision = -1
        else:
            context = await build_effective_access_context(
                session, user.workspace_id, str(user_id), allow_legacy_fallback=False,
            )
            current_revision = context.revision

    rejected: list[dict[str, str]] = []
    for key, lineage in protected.items():
        reason = lineage_rejection_reason(
            lineage, user_id=str(user_id), authorization_revision=current_revision,
        )
        if reason:
            source.pop(key, None)
            rejected.append({"key": key, "reason": reason})
    return source, rejected
