"""Wiki 实体页之间的双向链接。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    TIMESTAMP,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.database import Base
from app.core.db.tenant_mixin import TenantMixin


class WikiLink(Base, TenantMixin):
    """两个实体页之间的关系。

    link_type:
        - mentions    : A 提及 B（默认）
        - related     : 强相关（双向呈现）
        - supersedes  : A 取代了 B
        - contradicts : A 与 B 存在事实冲突，等待人工裁决
    """

    __tablename__ = "wiki_links"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_page_id",
            "target_page_id",
            "link_type",
            name="uq_wiki_links_triplet",
        ),
        Index("ix_wiki_links_ws_source", "workspace_id", "source_page_id"),
        Index("ix_wiki_links_ws_target", "workspace_id", "target_page_id"),
        Index("ix_wiki_links_ws_type", "workspace_id", "link_type"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    source_page_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wiki_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_page_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wiki_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    link_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="mentions"
    )

    # 证据：链接对应的原文切片，便于"溯源到原文"
    evidence_chunk_ids: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        comment="active / pending_review / resolved / dismissed",
    )

    extra_meta: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now(), onupdate=func.now()
    )
