"""
Skill 操作手册模型

用于存储用户自定义的操作指南，供 Planner 检索参考

设计原则遵循：
- Design Rigor: 模块化设计，与其他模型解耦
- Schema Validation: 严格定义字段类型
- Zero Tech Debt: 无冗余字段
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, Text, DateTime, JSON, Enum, ForeignKey, Integer
from sqlalchemy.orm import relationship
import enum

from app.core.db.database import Base


class SkillVisibility(str, enum.Enum):
    """Skill 可见性枚举"""
    GLOBAL = "global"       # 全局可见（系统预设）
    WORKSPACE = "workspace" # 工作区可见


class Skill(Base):
    """
    Skill 操作手册表
    
    存储用户定义的操作指南，包含标题、描述、步骤列表。
    向量嵌入存储在独立的 ChromaDB Collection 中。
    """
    __tablename__ = "skills"
    
    # 主键
    id = Column(String(36), primary_key=True, comment="UUID 主键")
    
    # 所属工作区（租户隔离）
    workspace_id = Column(
        String(36), 
        nullable=True,
        index=True,
        default='default',
        comment="所属工作区ID"
    )

    # 核心内容
    title = Column(
        String(255), 
        nullable=False, 
        index=True, 
        comment="技能标题（用于展示和检索）"
    )
    description = Column(
        Text, 
        nullable=False, 
        comment="技能描述（用于语义检索）"
    )
    steps = Column(
        JSON, 
        nullable=False, 
        comment="步骤列表 [{step, action, tool, template, keywords}]"
    )
    
    # 检索优化
    tags = Column(
        JSON, 
        default=list, 
        comment="标签列表，用于分类和检索"
    )
    example_queries = Column(
        JSON, 
        default=list, 
        comment="触发此 Skill 的典型用户提问，用于提高检索召回率"
    )
    
    # 可见性控制
    visibility = Column(
        Enum(SkillVisibility), 
        default=SkillVisibility.WORKSPACE,
        index=True,
        comment="可见性: global/workspace"
    )
    
    # 使用统计
    usage_count = Column(
        Integer, 
        default=0, 
        comment="使用次数，用于热门排序"
    )
    
    # 审计字段
    created_by = Column(
        String(36), 
        ForeignKey("sys_users.id", ondelete="CASCADE"), 
        nullable=False,
        comment="创建者用户ID"
    )
    created_at = Column(
        DateTime, 
        default=datetime.utcnow, 
        comment="创建时间"
    )
    updated_at = Column(
        DateTime, 
        default=datetime.utcnow, 
        onupdate=datetime.utcnow, 
        comment="更新时间"
    )
    
    def __repr__(self):
        return f"<Skill(id={self.id}, title={self.title})>"
