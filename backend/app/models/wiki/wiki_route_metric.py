"""[M3.5] Wiki 检索路由评估指标表。

每次 doc_worker 执行结束后写入一行，用于度量：
- 路径分布：rag / wiki / both 各自占比与平均时延
- Wiki 命中率：wiki+both / total
- wiki_gap 触发率：has_wiki_gap=true 占比
- KnowledgeRouter 判定来源分布：rule / llm / fallback / disabled

设计原则：
- 单表追加写入，不与其他表 join；分析时按 workspace_id+created_at 聚合
- 字段保持精炼以避免膨胀；明细信息留给 worker meta（不持久化）
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
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


class WikiRouteMetric(Base, TenantMixin):
    """Wiki 检索路由埋点指标。"""

    __tablename__ = "wiki_route_metrics"
    __table_args__ = (
        Index("ix_wiki_route_metrics_ws_created", "workspace_id", "created_at"),
        Index("ix_wiki_route_metrics_ws_path_created", "workspace_id", "knowledge_path", "created_at"),
        Index("ix_wiki_route_metrics_ws_session", "workspace_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # ===== 上下文标识 =====
    session_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="对话会话 id"
    )
    message_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="本轮消息 id（可选）"
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, comment="发起用户 id"
    )

    # ===== 路由判定 =====
    knowledge_path: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="rag",
        comment="rag / wiki / both",
    )
    knowledge_path_source: Mapped[Optional[str]] = mapped_column(
        String(16),
        nullable=True,
        comment="rule / llm / fallback / disabled / default",
    )
    knowledge_path_reason: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="判定原因摘要（来自 KnowledgeRouter）"
    )

    # ===== 检索结果 =====
    chunks_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="送入 Synthesizer 的总 chunk 数"
    )
    wiki_chunks_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="navigator 装载的 wiki_page 数"
    )
    wiki_chunks_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="实际被 Synthesizer 使用的 wiki_page 数"
    )

    # ===== Wiki 缺口信号 =====
    has_wiki_gap: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否触发 wiki_gap 入队"
    )
    wiki_gap_task_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True, comment="对应的 wiki_compile_tasks.id"
    )

    # ===== 性能与终态 =====
    latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="doc_worker 端到端时延（毫秒）"
    )
    stop_reason: Mapped[Optional[str]] = mapped_column(
        String(32),
        nullable=True,
        comment="rag_ok / rag_empty / rag_retrieval_error 等",
    )

    # ===== 输入摘要（便于排查；非分析维度）=====
    user_query: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="用户查询前 200 字（隐私脱敏后）"
    )
    extra_meta: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True, comment="保留扩展字段（模型/温度/feature flag 等）"
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now(), nullable=False
    )
