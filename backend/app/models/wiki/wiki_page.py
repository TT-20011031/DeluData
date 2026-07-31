"""Wiki 实体页 ORM 模型。

每一行代表一个实体（产品、制度条款、客户、决策、术语等）的 Markdown 详情页。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Index,
    Integer,
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


class WikiPage(Base, TenantMixin):
    """Wiki 实体页（Markdown 文本 + 关系数据库元数据双轨存放）。"""

    __tablename__ = "wiki_pages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_wiki_pages_ws_slug"),
        Index("ix_wiki_pages_ws_domain", "workspace_id", "domain"),
        Index("ix_wiki_pages_ws_status", "workspace_id", "status"),
        Index("ix_wiki_pages_ws_compiled", "workspace_id", "last_compiled_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # ====== 标识 ======
    slug: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="工作区内唯一的 URL 标识（小写连字符）",
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="实体显示名")
    aliases: Mapped[Optional[list[str]]] = mapped_column(
        JSON, nullable=True, comment="同名/别名 JSON 数组，用于实体对齐"
    )
    domain: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="general",
        comment="知识域分类（policy/product/customer/term/decision/general）",
    )

    # ====== 内容 ======
    summary: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="一句话摘要（用于 INDEX 装载）"
    )
    markdown_body: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="完整 Markdown 正文"
    )

    # ====== 状态与版本 ======
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="draft",
        comment="candidate / draft / published / verified / deprecated / archived",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="自增版本号（用于乐观锁）"
    )
    char_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    token_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # ====== 编译元数据 ======
    last_compiled_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP, nullable=True, comment="最后一次被自动编译的时间"
    )
    last_compiled_by: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="agent / user:<id> / cli"
    )
    compile_meta: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True, comment="模型/温度/编译耗时/token 用量等"
    )

    # ====== 通用字段 ======
    owner_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now(), onupdate=func.now()
    )
