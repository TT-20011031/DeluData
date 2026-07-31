"""Shared readiness resolution helpers for supervisor nodes."""

from __future__ import annotations

from typing import Any

from app.services.workspace_readiness_service import WorkspaceReadiness, WorkspaceReadinessService


async def resolve_runtime_readiness(
    state: dict[str, Any],
    *,
    default_available: bool = False,
) -> WorkspaceReadiness:
    """Resolve readiness from runtime identity, fallback to state snapshot."""

    service = WorkspaceReadinessService()
    user_context = state.get("user_context", {}) or {}
    user_id = user_context.get("user_id")
    workspace_id = user_context.get("workspace_id")

    if user_id and workspace_id:
        return await service.get_workspace_readiness(
            user_id=str(user_id),
            workspace_id=workspace_id,
        )

    return service.readiness_from_state(state, default_available=default_available)


def build_readiness_state_patch(readiness: WorkspaceReadiness) -> dict[str, Any]:
    """Build state patch fields used by planner/executor/direct-execute."""

    return {
        "has_db_connection": readiness.has_db,
        "has_knowledge_base": readiness.has_knowledge,
        "readiness_reasons": readiness.reasons,
    }
