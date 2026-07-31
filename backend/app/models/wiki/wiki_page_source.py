"""Wiki 实体页 ↔ 原始文档/切片的多对多溯源表。

每一行表示「实体页 P 的某些事实来自文件 F 的若干 chunk」。
让 Wiki 内容能始终回溯到原文，避免 LLM 幻觉沉淀。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.database import Base
from app.core.db.tenant_mixin import TenantMixin


class WikiPageSource(Base, TenantMixin):
    __tablename__ = "wiki_page_sources"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "page_id",
            "file_id",
            name="uq_wiki_page_sources_ws_page_file",
        ),
        Index("ix_wiki_page_sources_ws_page", "workspace_id", "page_id"),
        Index("ix_wiki_page_sources_ws_file", "workspace_id", "file_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    page_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wiki_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ChromaDB 中的 chunk_id 列表（不在 MySQL 加 FK，保持松耦合）
    chunk_ids: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)

    excerpt: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="原文摘录（前 N 字），用于快速预览"
    )
    relevance: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="该文件对此页的相关度（0-1）"
    )

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now(), onupdate=func.now()
    )
