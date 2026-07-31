"""
Ingestion task ORM model.

用于知识库上传后处理的持久化任务队列。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text, TIMESTAMP, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.database import Base
from app.core.db.tenant_mixin import TenantMixin


class IngestionTask(Base, TenantMixin):
    __tablename__ = "ingestion_tasks"
    __table_args__ = (
        Index("idx_tasks_status_lease", "status", "lease_expires_at"),
        Index("idx_tasks_ws_status_created", "workspace_id", "status", "created_at"),
        Index("idx_tasks_file", "file_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    run_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)

    payload_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)
