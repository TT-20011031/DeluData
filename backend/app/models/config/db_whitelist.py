"""
Workspace database whitelist model and CRUD helpers.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text, select

from app.core.db.database import Base, get_async_db_manager

logger = logging.getLogger(__name__)


class WorkspaceDBWhitelistModel(Base):
    """Per-workspace DB endpoint whitelist."""

    __tablename__ = "workspace_db_whitelists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, unique=True, index=True)
    is_enabled = Column(Boolean, nullable=False, default=False)
    allowed_endpoints = Column(JSON, nullable=False, default=list)
    note = Column(Text, nullable=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class DBWhitelistEndpoint(BaseModel):
    host: str
    port: int = 3306

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("host is required")
        return text


class WorkspaceDBWhitelist(BaseModel):
    workspace_id: str
    is_enabled: bool = False
    allowed_endpoints: list[DBWhitelistEndpoint] = Field(default_factory=list)
    note: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_orm(cls, orm_obj: WorkspaceDBWhitelistModel) -> "WorkspaceDBWhitelist":
        endpoints = orm_obj.allowed_endpoints or []
        return cls(
            workspace_id=orm_obj.workspace_id,
            is_enabled=bool(orm_obj.is_enabled),
            allowed_endpoints=[DBWhitelistEndpoint(**item) for item in endpoints],
            note=orm_obj.note,
            updated_by=orm_obj.updated_by,
            created_at=orm_obj.created_at,
            updated_at=orm_obj.updated_at,
        )


async def get_workspace_db_whitelist_async(workspace_id: str) -> Optional[WorkspaceDBWhitelist]:
    """Fetch whitelist config for a workspace."""

    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(WorkspaceDBWhitelistModel).where(
                WorkspaceDBWhitelistModel.workspace_id == workspace_id
            )
        )
        model = result.scalar_one_or_none()
        return WorkspaceDBWhitelist.from_orm(model) if model else None


async def save_workspace_db_whitelist_async(
    workspace_id: str,
    *,
    is_enabled: bool,
    allowed_endpoints: list[dict[str, Any]],
    note: Optional[str] = None,
    updated_by: Optional[str] = None,
) -> WorkspaceDBWhitelist:
    """Create or update whitelist config."""

    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(WorkspaceDBWhitelistModel).where(
                WorkspaceDBWhitelistModel.workspace_id == workspace_id
            )
        )
        model = result.scalar_one_or_none()

        if model:
            model.is_enabled = bool(is_enabled)
            model.allowed_endpoints = allowed_endpoints
            model.note = note
            model.updated_by = updated_by
            model.updated_at = datetime.now()
        else:
            model = WorkspaceDBWhitelistModel(
                workspace_id=workspace_id,
                is_enabled=bool(is_enabled),
                allowed_endpoints=allowed_endpoints,
                note=note,
                updated_by=updated_by,
            )
            session.add(model)

        await session.flush()
        await session.refresh(model)
        logger.info(
            "[DBWhitelist] workspace=%s updated_by=%s is_enabled=%s endpoint_count=%s",
            workspace_id,
            updated_by,
            bool(is_enabled),
            len(allowed_endpoints or []),
        )
        return WorkspaceDBWhitelist.from_orm(model)
