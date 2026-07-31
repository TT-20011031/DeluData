"""
知识图谱与文档管理系统 - SQLAlchemy 模型定义
"""
from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import (
    Column,
    String,
    Text,
    ForeignKey,
    TIMESTAMP,
    Enum,
    Integer,
    BigInteger,
    Boolean,
    DateTime,
    func,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.ext.declarative import declarative_base

from app.core.db.database import Base
from app.core.db.tenant_mixin import TenantMixin

class Folder(Base, TenantMixin):
    __tablename__ = 'folders'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    parent_id: Mapped[Optional[str]] = mapped_column(ForeignKey('folders.id', ondelete='CASCADE'), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    user_id: Mapped[Optional[str]] = mapped_column(String(36))
    
    # [新增] 可见性与部门归属 (实现文件夹隔离)
    visibility: Mapped[str] = mapped_column(String(20), default='public')  # public | dept | private
    dept_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 关联部门ID
    owner_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # 创建者ID
    # workspace_id 继承自 TenantMixin，不再手动定义
    
    # 图谱位置
    x: Mapped[Optional[float]] = mapped_column(Integer, default=0)
    y: Mapped[Optional[float]] = mapped_column(Integer, default=0)

    # 关系
    parent = relationship("Folder", remote_side=[id], backref="children")
    files = relationship("File", back_populates="folder")


class File(Base, TenantMixin):
    __tablename__ = 'files'
    __table_args__ = (
        Index('ix_files_workspace_deleted_status', 'workspace_id', 'is_deleted', 'status'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID, 与 Chroma 一致
    folder_id: Mapped[Optional[str]] = mapped_column(ForeignKey('folders.id', ondelete='SET NULL'), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    storage_path: Mapped[Optional[str]] = mapped_column(String(512))
    file_type: Mapped[Optional[str]] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(Enum('processing', 'indexed', 'error', name='file_status_enum'), default='processing')
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    processed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)
    user_id: Mapped[Optional[str]] = mapped_column(String(36))
    # workspace_id 继承自 TenantMixin，不再手动定义
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)
    delete_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    delete_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delete_op_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # PageIndex 生命周期
    pageindex_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pageindex_error: Mapped[Optional[str]] = mapped_column(Text)
    
    # [新增] 可见性与部门归属
    visibility: Mapped[str] = mapped_column(String(20), default='dept')  # public | dept | private
    dept_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 关联部门ID
    owner_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # 上传者ID

    # 企业知识元数据（用于 Wiki 分区、治理、诊断与后续智能路由）
    document_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    business_domain: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    confidentiality_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    effective_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # 图谱位置
    x: Mapped[Optional[float]] = mapped_column(Integer, default=0)
    y: Mapped[Optional[float]] = mapped_column(Integer, default=0)

    # 关系
    folder = relationship("Folder", back_populates="files")
    
    # 显式关系 (Source)
    relationships_source = relationship("FileRelationship", 
                                      foreign_keys="[FileRelationship.source_file_id]",
                                      back_populates="source_file",
                                      cascade="all, delete-orphan")
    
    # 显式关系 (Target)
    relationships_target = relationship("FileRelationship", 
                                      foreign_keys="[FileRelationship.target_file_id]",
                                      back_populates="target_file",
                                      cascade="all, delete-orphan")
    images = relationship(
        "DocumentImage",
        back_populates="file",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FileRelationship(Base):
    __tablename__ = 'file_relationships'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_file_id: Mapped[str] = mapped_column(ForeignKey('files.id', ondelete='CASCADE'), nullable=False)
    target_file_id: Mapped[str] = mapped_column(ForeignKey('files.id', ondelete='CASCADE'), nullable=False)
    relation_type: Mapped[Optional[str]] = mapped_column(String(50))  # '引用', '补充', '冲突'
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())

    # 关系
    source_file = relationship("File", foreign_keys=[source_file_id], back_populates="relationships_source")
    target_file = relationship("File", foreign_keys=[target_file_id], back_populates="relationships_target")


class DocumentImage(Base):
    """
    文档图片表 (v2.1 多模态RAG)
    
    存储从 PDF/Word 提取的图片元数据
    """
    __tablename__ = 'document_images'
    
    __table_args__ = (
        UniqueConstraint("file_id", "image_id", name="uq_document_images_file_image"),
        Index("ix_document_images_file_id", "file_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    file_id: Mapped[str] = mapped_column(ForeignKey('files.id', ondelete='CASCADE'), nullable=False)
    image_id: Mapped[str] = mapped_column(String(64), nullable=False)  # 业务图片ID（001 / page_0001 等）
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)  # 本地存储路径
    page_number: Mapped[int] = mapped_column(Integer, default=1)  # 所在页码
    bbox: Mapped[Optional[str]] = mapped_column(String(100))  # JSON: {"x1":0, "y1":0, "x2":100, "y2":100}
    alt_text: Mapped[Optional[str]] = mapped_column(Text)  # VLM 生成的描述
    phash: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # 用于去重
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now())
    
    # 关系
    file = relationship("File", back_populates="images", passive_deletes=True)

