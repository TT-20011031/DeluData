"""Wiki 实体页修订历史。

每次编辑（自动编译 / 人工修改 / Lint 修复）都写一条，可一键回滚。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    ForeignKey,
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


class WikiRevision(Base, TenantMixin):
    __tablename__ = "wiki_revisions"
    __table_args__ = (
        Index("ix_wiki_revisions_ws_page_ver", "workspace_id", "page_id", "version"),
        Index("ix_wiki_revisions_ws_committed", "workspace_id", "committed_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    page_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wiki_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # 提交者：agent / user:<id> / cli / lint
    committed_by: Mapped[str] = mapped_column(String(64), nullable=False, default="agent")
    commit_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # 快照（保留全文，便于回滚）
    snapshot_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # diff：相对前一版本的统一 diff（unified diff string）
    diff_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    extra_meta: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    committed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now()
    )
