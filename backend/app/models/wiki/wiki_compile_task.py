"""Wiki 编译任务（与 IngestionTask 同框架，独立队列）。

trigger_type:
    - doc_upload  : 文档入库成功后自动触发
    - doc_delete  : 文档被删除时清理对应实体页/链接
    - manual      : 管理员手动触发（API/CLI）
    - cron        : 定时全量 / 增量 lint
    - reflection  : 对话中智能体输出 wiki_gap 信号触发的回写
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Index,
    Integer,
    JSON,
    String,
    Text,
    TIMESTAMP,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.database import Base
from app.core.db.tenant_mixin import TenantMixin


class WikiCompileTask(Base, TenantMixin):
    __tablename__ = "wiki_compile_tasks"
    __table_args__ = (
        Index("ix_wiki_compile_tasks_status_lease", "status", "lease_expires_at"),
        Index(
            "ix_wiki_compile_tasks_ws_status_created",
            "workspace_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_wiki_compile_tasks_ws_trigger",
            "workspace_id",
            "trigger_type",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # 任务负载：file_ids[] / page_ids[] / scope='workspace' / extra
    payload_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # 进度
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 结果摘要
    result_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
        comment="编译结果摘要：created/updated/conflicted 各页数等",
    )

    # 失败重试（与 IngestionTask 一致语义）
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    run_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)
