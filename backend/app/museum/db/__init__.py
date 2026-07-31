"""
博物馆模块 - 数据库 ORM 模型

遵循设计原则：严格模式校验 (Schema Validation)
"""
import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import Column, String, Text, DECIMAL, Enum, JSON, Integer, TIMESTAMP, func, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.database import Base


class MuseumProduct(Base):
    """博物馆商品表"""
    
    __tablename__ = "museum_products"
    
    id: Mapped[str] = mapped_column(
        String(36), 
        primary_key=True, 
        default=lambda: str(uuid.uuid4())
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        default="default",
        comment="租户ID",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="商品名称")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="商品描述")
    price: Mapped[float] = mapped_column(DECIMAL(10, 2), nullable=False, comment="商品价格")
    category: Mapped[str] = mapped_column(
        Enum("文创", "纪念品", "仿制品", "书籍", "其他", name="product_category"),
        default="其他",
        comment="商品分类"
    )
    related_exhibit_ids: Mapped[Optional[List]] = mapped_column(
        JSON, 
        nullable=True, 
        comment="关联展品ID列表"
    )
    image_urls: Mapped[Optional[List]] = mapped_column(
        JSON, 
        nullable=True, 
        comment="商品图片URL列表"
    )
    stock: Mapped[int] = mapped_column(Integer, default=0, comment="库存数量")
    status: Mapped[str] = mapped_column(
        Enum("active", "inactive", name="product_status"),
        default="active",
        comment="商品状态"
    )
    is_global: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为全局推荐")
    target_crowd: Mapped[Optional[List]] = mapped_column(
        JSON, 
        nullable=True, 
        comment="适用人群标签 (对应 PersonType)"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, 
        server_default=func.current_timestamp(),
        comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        comment="更新时间"
    )


class MuseumArtifact(Base):
    """
    文物索引表 (The Bridge)
    
    用于连接 RAG 检索结果与实际业务商品
    """
    __tablename__ = "museum_artifacts"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        default="default",
        comment="租户ID",
    )
    name: Mapped[str] = mapped_column(
        String(100), 
        unique=True, 
        nullable=False, 
        index=True,
        comment="文物名称 (需与 RAG 文档标题一致)"
    )
    location: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="展馆位置")
    is_highlight: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为镇馆之宝")
    click_count: Mapped[int] = mapped_column(Integer, default=0, comment="热度点击量")
    
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, 
        server_default=func.current_timestamp(),
        comment="创建时间"
    )


class MuseumGuideSession(Base):
    """博物馆导览会话表"""
    
    __tablename__ = "museum_guide_sessions"
    
    id: Mapped[str] = mapped_column(
        String(36), 
        primary_key=True, 
        default=lambda: str(uuid.uuid4())
    )
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        default="default",
        comment="租户ID",
    )
    visitor_uuid: Mapped[str] = mapped_column(
        String(36), 
        nullable=False, 
        index=True,
        comment="访客 UUID"
    )
    person_type: Mapped[Optional[str]] = mapped_column(
        String(50), 
        nullable=True,
        comment="识别出的人物类型"
    )
    person_features: Mapped[Optional[List]] = mapped_column(
        JSON, 
        nullable=True,
        comment="人物特征列表"
    )
    style_config: Mapped[Optional[dict]] = mapped_column(
        JSON, 
        nullable=True,
        comment="缓存的风格配置"
    )
    visitor_image_path: Mapped[Optional[str]] = mapped_column(
        String(500), 
        nullable=True,
        comment="访客图片路径"
    )
    conversation_history: Mapped[Optional[List]] = mapped_column(
        JSON, 
        nullable=True,
        comment="对话历史"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, 
        server_default=func.current_timestamp(),
        index=True,
        comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        comment="更新时间"
    )
