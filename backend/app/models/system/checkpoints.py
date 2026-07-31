"""
DeluData 智能问数系统 - 系统级数据模型

存储 LangGraph Checkpoint 等系统状态数据
"""
from datetime import datetime
from typing import Optional, Any, Dict
from sqlalchemy import String, LargeBinary, JSON, DateTime, Integer
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.database import Base
from app.core.db.tenant_mixin import TenantMixin


class LangGraphCheckpoints(Base, TenantMixin):
    """
    LangGraph Checkpoints 存储表
    
    用于持久化 Agent 运行状态
    采用复合主键 (thread_id, checkpoint_id)
    """
    __tablename__ = "langgraph_checkpoints"

    # 复合主键
    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True, comment="会话ID")
    checkpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True, comment="检查点ID")
    
    # [NEW] 用户隔离字段
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True, comment="用户ID（用于会话隔离）")
    # workspace_id 继承自 TenantMixin，不再手动定义
    
    # 关联字段
    parent_checkpoint_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="父检查点ID")
    
    # 内容字段
    checkpoint: Mapped[bytes] = mapped_column(LONGBLOB, comment="序列化后的 State (Pickle)")
    metadata_: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, default={}, comment="元数据")
    
    # 辅助字段
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="创建时间")
    
    def __repr__(self):
        return f"<Checkpoint(thread_id={self.thread_id}, checkpoint_id={self.checkpoint_id})>"


class LangGraphWrites(Base):
    """
    LangGraph Writes 存储表
    
    存储中间写入状态 (用于容错)
    """
    __tablename__ = "langgraph_checkpoint_writes"

    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    channel: Mapped[str] = mapped_column(String(255))
    type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True) # e.g. "json", "pickle"
    value: Mapped[bytes] = mapped_column(LONGBLOB) # Serialized value
    
    def __repr__(self):
        return f"<Write(thread={self.thread_id}, cp={self.checkpoint_id}, task={self.task_id})>"
