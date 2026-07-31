"""
平台管理员数据模型

独立于租户用户表 (users)，实现物理隔离
不继承 TenantMixin
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.database import Base


class PlatformAdminModel(Base):
    """
    平台管理员模型
    
    用于管理多租户平台的超级管理员账号
    与租户用户表 (users) 完全隔离
    """
    __tablename__ = "sys_platform_admins"
    
    # 主键
    id: Mapped[str] = mapped_column(
        String(36), 
        primary_key=True, 
        default=lambda: str(uuid.uuid4())
    )
    
    # 基础信息
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, comment="用户名")
    email: Mapped[Optional[str]] = mapped_column(String(100), comment="邮箱")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, comment="密码哈希")
    
    # 状态
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, comment="最后登录时间")
    
    # [防御性字段] 安全审计
    login_attempts: Mapped[int] = mapped_column(Integer, default=0, comment="登录尝试次数 (防爆破)")
    last_login_ip: Mapped[Optional[str]] = mapped_column(String(45), comment="最后登录 IP (支持 IPv6)")
    
    def __repr__(self):
        return f"<PlatformAdmin(id={self.id}, username={self.username})>"
