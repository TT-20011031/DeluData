"""Platform APIs for workspace DB whitelist management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.api.platform.deps import get_current_platform_admin
from app.models.auth.workspace import WorkspaceModel
from app.models.platform.admin import PlatformAdminModel
from app.services.db_whitelist_service import DBWhitelistService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform", tags=["平台白名单"])
_whitelist_service = DBWhitelistService()


class DBWhitelistEndpointPayload(BaseModel):
    host: str = Field(..., min_length=1)
    port: int = Field(default=3306, ge=1, le=65535)


class WorkspaceDBWhitelistUpdateRequest(BaseModel):
    is_enabled: bool = False
    allowed_endpoints: list[DBWhitelistEndpointPayload] = Field(default_factory=list)
    note: str | None = None


class WorkspaceDBWhitelistResponse(BaseModel):
    workspace_id: str
    workspace_code: str | None = None
    workspace_name: str | None = None
    is_enabled: bool = False
    allowed_endpoints: list[DBWhitelistEndpointPayload] = Field(default_factory=list)
    note: str | None = None


async def _get_workspace_or_404(
    db: AsyncSession,
    workspace_id: str,
) -> WorkspaceModel:
    result = await db.execute(
        select(WorkspaceModel).where(WorkspaceModel.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    return workspace


@router.get("/workspaces/{workspace_id}/db-whitelist", response_model=WorkspaceDBWhitelistResponse)
async def get_workspace_db_whitelist(
    workspace_id: str,
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db),
):
    workspace = await _get_workspace_or_404(db, workspace_id)
    config = await _whitelist_service.get_workspace_config(workspace_id)

    if not config:
        return WorkspaceDBWhitelistResponse(
            workspace_id=workspace.id,
            workspace_code=workspace.code,
            workspace_name=workspace.name,
            is_enabled=False,
            allowed_endpoints=[],
            note=None,
        )

    return WorkspaceDBWhitelistResponse(
        workspace_id=workspace.id,
        workspace_code=workspace.code,
        workspace_name=workspace.name,
        is_enabled=bool(config.is_enabled),
        allowed_endpoints=[
            DBWhitelistEndpointPayload(host=item.host, port=item.port)
            for item in (config.allowed_endpoints or [])
        ],
        note=config.note,
    )


@router.put("/workspaces/{workspace_id}/db-whitelist", response_model=WorkspaceDBWhitelistResponse)
async def update_workspace_db_whitelist(
    workspace_id: str,
    request: WorkspaceDBWhitelistUpdateRequest,
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db),
):
    workspace = await _get_workspace_or_404(db, workspace_id)
    saved = await _whitelist_service.update_workspace_config(
        workspace_id,
        is_enabled=request.is_enabled,
        allowed_endpoints=[
            {"host": item.host, "port": item.port}
            for item in request.allowed_endpoints
        ],
        note=request.note,
        updated_by=f"platform:{admin.id}",
    )

    logger.info(
        "[PlatformAudit] admin=%s workspace=%s updated DB whitelist enabled=%s endpoints=%s",
        admin.username,
        workspace_id,
        saved.is_enabled,
        len(saved.allowed_endpoints),
    )

    return WorkspaceDBWhitelistResponse(
        workspace_id=workspace.id,
        workspace_code=workspace.code,
        workspace_name=workspace.name,
        is_enabled=bool(saved.is_enabled),
        allowed_endpoints=[
            DBWhitelistEndpointPayload(host=item.host, port=item.port)
            for item in (saved.allowed_endpoints or [])
        ],
        note=saved.note,
    )
