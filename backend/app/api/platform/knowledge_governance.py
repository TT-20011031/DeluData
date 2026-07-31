"""Platform APIs for workspace knowledge governance."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.api.platform.deps import get_current_platform_admin
from app.models.auth.workspace import WorkspaceModel
from app.models.platform.admin import PlatformAdminModel
from app.services.workspace_knowledge_governance_service import (
    WorkspaceKnowledgeGovernanceService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform", tags=["平台知识库治理"])


class WorkspaceKnowledgeGovernanceUpdateRequest(BaseModel):
    storage_quota_bytes: Optional[int] = Field(
        default=None,
        ge=0,
        description="知识库存储总配额（字节）；为空或 0 表示不限额",
    )
    max_upload_file_size_bytes: Optional[int] = Field(
        default=None,
        ge=0,
        description="单文件上传大小上限（字节）；为空或 0 表示不限制",
    )
    upload_enabled: bool = True
    delete_enabled: bool = True
    rename_enabled: bool = True
    move_enabled: bool = True
    create_folder_enabled: bool = True
    note: Optional[str] = None


class WorkspaceKnowledgeGovernanceResponse(BaseModel):
    workspace_id: str
    workspace_code: Optional[str] = None
    workspace_name: Optional[str] = None
    storage_quota_bytes: Optional[int] = None
    max_upload_file_size_bytes: Optional[int] = None
    storage_used_bytes: int = 0
    storage_remaining_bytes: Optional[int] = None
    storage_usage_ratio: Optional[float] = None
    file_count: int = 0
    last_upload_at: Optional[datetime] = None
    upload_enabled: bool = True
    delete_enabled: bool = True
    rename_enabled: bool = True
    move_enabled: bool = True
    create_folder_enabled: bool = True
    note: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None


class WorkspaceKnowledgeGovernanceSummaryResponse(BaseModel):
    workspace_id: str
    workspace_code: Optional[str] = None
    workspace_name: Optional[str] = None
    storage_quota_bytes: Optional[int] = None
    max_upload_file_size_bytes: Optional[int] = None
    storage_used_bytes: int = 0
    storage_remaining_bytes: Optional[int] = None
    storage_usage_ratio: Optional[float] = None
    file_count: int = 0
    last_upload_at: Optional[datetime] = None
    upload_enabled: bool = True
    delete_enabled: bool = True
    rename_enabled: bool = True
    move_enabled: bool = True
    create_folder_enabled: bool = True


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


@router.get(
    "/knowledge-governance/workspaces",
    response_model=list[WorkspaceKnowledgeGovernanceSummaryResponse],
)
async def list_workspace_knowledge_governance(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db),
):
    del admin
    service = WorkspaceKnowledgeGovernanceService(db)
    return await service.list_workspace_summaries(skip=skip, limit=limit)


@router.get(
    "/workspaces/{workspace_id}/knowledge-governance",
    response_model=WorkspaceKnowledgeGovernanceResponse,
)
async def get_workspace_knowledge_governance(
    workspace_id: str,
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db),
):
    del admin
    workspace = await _get_workspace_or_404(db, workspace_id)
    service = WorkspaceKnowledgeGovernanceService(db)
    config = await service.get_workspace_config(workspace_id)
    usage = await service.get_workspace_usage(workspace_id)
    usage_payload = service.build_usage_payload(config, usage)

    return WorkspaceKnowledgeGovernanceResponse(
        workspace_id=workspace.id,
        workspace_code=workspace.code,
        workspace_name=workspace.name,
        max_upload_file_size_bytes=config.max_upload_file_size_bytes,
        upload_enabled=config.upload_enabled,
        delete_enabled=config.delete_enabled,
        rename_enabled=config.rename_enabled,
        move_enabled=config.move_enabled,
        create_folder_enabled=config.create_folder_enabled,
        note=config.note,
        updated_by=config.updated_by,
        updated_at=config.updated_at,
        **usage_payload,
    )


@router.put(
    "/workspaces/{workspace_id}/knowledge-governance",
    response_model=WorkspaceKnowledgeGovernanceResponse,
)
async def update_workspace_knowledge_governance(
    workspace_id: str,
    request: WorkspaceKnowledgeGovernanceUpdateRequest,
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db),
):
    workspace = await _get_workspace_or_404(db, workspace_id)
    service = WorkspaceKnowledgeGovernanceService(db)
    config = await service.update_workspace_config(
        workspace_id,
        storage_quota_bytes=request.storage_quota_bytes,
        max_upload_file_size_bytes=request.max_upload_file_size_bytes,
        upload_enabled=request.upload_enabled,
        delete_enabled=request.delete_enabled,
        rename_enabled=request.rename_enabled,
        move_enabled=request.move_enabled,
        create_folder_enabled=request.create_folder_enabled,
        note=request.note,
        updated_by=f"platform:{admin.id}",
    )
    usage = await service.get_workspace_usage(workspace_id)
    usage_payload = service.build_usage_payload(config, usage)

    logger.info(
        "[PlatformAudit] admin=%s workspace=%s updated knowledge governance quota=%s max_upload=%s upload=%s delete=%s rename=%s move=%s create_folder=%s",
        admin.username,
        workspace_id,
        config.storage_quota_bytes,
        config.max_upload_file_size_bytes,
        config.upload_enabled,
        config.delete_enabled,
        config.rename_enabled,
        config.move_enabled,
        config.create_folder_enabled,
    )

    return WorkspaceKnowledgeGovernanceResponse(
        workspace_id=workspace.id,
        workspace_code=workspace.code,
        workspace_name=workspace.name,
        max_upload_file_size_bytes=config.max_upload_file_size_bytes,
        upload_enabled=config.upload_enabled,
        delete_enabled=config.delete_enabled,
        rename_enabled=config.rename_enabled,
        move_enabled=config.move_enabled,
        create_folder_enabled=config.create_folder_enabled,
        note=config.note,
        updated_by=config.updated_by,
        updated_at=config.updated_at,
        **usage_payload,
    )
