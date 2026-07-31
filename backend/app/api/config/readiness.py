"""Workspace readiness API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.security.auth import User
from app.services.workspace_readiness_service import WorkspaceReadinessService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/workspace-readiness")
async def get_workspace_readiness(current_user: User = Depends(get_current_user)):
    service = WorkspaceReadinessService()
    try:
        readiness = await service.get_workspace_readiness(
            user_id=str(current_user.id),
            workspace_id=current_user.workspace_id,
        )
        return readiness.to_dict()
    except Exception as exc:
        logger.error(
            "Get workspace readiness failed: user_id=%s workspace_id=%s error=%s",
            current_user.id,
            current_user.workspace_id,
            exc,
        )
        raise HTTPException(status_code=500, detail="获取工作区可用性失败")
