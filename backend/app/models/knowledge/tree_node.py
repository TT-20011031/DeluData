"""
PageIndex 树索引模型
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Integer, TIMESTAMP, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.database import Base
from app.core.db.tenant_mixin import TenantMixin


class TreeNode(Base, TenantMixin):
    __tablename__ = "tree_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parent_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[Optional[str]] = mapped_column(Text)

    start_page: Mapped[Optional[int]] = mapped_column(Integer)
    end_page: Mapped[Optional[int]] = mapped_column(Integer)

    node_level: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    token_count: Mapped[int] = mapped_column(Integer, default=0)

    visibility: Mapped[str] = mapped_column(String(20), default="dept")
    owner_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    dept_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    meta_info: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

