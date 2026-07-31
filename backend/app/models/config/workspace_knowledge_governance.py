"""
Workspace knowledge governance model and schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text

from app.core.db.database import Base


class WorkspaceKnowledgeGovernanceModel(Base):
    """Per-workspace knowledge governance configuration."""

    __tablename__ = "workspace_knowledge_governance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=False, unique=True, index=True)
    storage_quota_bytes = Column(BigInteger, nullable=True)
    max_upload_file_size_bytes = Column(BigInteger, nullable=True)
    upload_enabled = Column(Boolean, nullable=False, default=True)
    delete_enabled = Column(Boolean, nullable=False, default=True)
    rename_enabled = Column(Boolean, nullable=False, default=True)
    move_enabled = Column(Boolean, nullable=False, default=True)
    create_folder_enabled = Column(Boolean, nullable=False, default=True)
    note = Column(Text, nullable=True)
    updated_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class WorkspaceKnowledgeGovernance(BaseModel):
    workspace_id: str
    storage_quota_bytes: Optional[int] = None
    max_upload_file_size_bytes: Optional[int] = None
    upload_enabled: bool = True
    delete_enabled: bool = True
    rename_enabled: bool = True
    move_enabled: bool = True
    create_folder_enabled: bool = True
    note: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @classmethod
    def from_orm(
        cls,
        orm_obj: WorkspaceKnowledgeGovernanceModel,
    ) -> "WorkspaceKnowledgeGovernance":
        return cls(
            workspace_id=orm_obj.workspace_id,
            storage_quota_bytes=orm_obj.storage_quota_bytes,
            max_upload_file_size_bytes=orm_obj.max_upload_file_size_bytes,
            upload_enabled=bool(orm_obj.upload_enabled),
            delete_enabled=bool(orm_obj.delete_enabled),
            rename_enabled=bool(orm_obj.rename_enabled),
            move_enabled=bool(orm_obj.move_enabled),
            create_folder_enabled=bool(orm_obj.create_folder_enabled),
            note=orm_obj.note,
            updated_by=orm_obj.updated_by,
            created_at=orm_obj.created_at,
            updated_at=orm_obj.updated_at,
        )
