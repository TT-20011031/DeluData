"""
DeluData 多租户系统 - 工作空间模型

定义租户/工作空间表，用于 SaaS 多企业客户隔离
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text
from sqlalchemy.orm import relationship

from app.core.db.database import Base


class WorkspaceModel(Base):
    """
    工作空间/租户表
    
    每个工作空间代表一个独立的企业客户
    
    Attributes:
        id: 工作空间唯一标识（UUID）
        name: 企业名称
        code: 唯一标识码（用于URL和识别）
        owner_id: 超管用户ID
        plan: 套餐类型 (free/pro/enterprise)
        max_users: 最大用户数限制
        is_active: 是否启用
        description: 描述信息
        created_at: 创建时间
        updated_at: 更新时间
    """
    __tablename__ = 'sys_workspaces'
    
    id = Column(String(36), primary_key=True, comment='工作空间ID')
    name = Column(String(128), nullable=False, comment='企业名称')
    code = Column(String(64), unique=True, nullable=False, index=True, comment='唯一标识码')
    
    # 管理
    owner_id = Column(String(36), nullable=True, comment='超管用户ID')
    plan = Column(String(32), default='free', comment='套餐: free/pro/enterprise')
    max_users = Column(Integer, default=10, comment='最大用户数')
    
    # 状态
    is_active = Column(Boolean, default=True, comment='是否启用')
    museum_enabled = Column(Boolean, default=False, nullable=False, comment='是否开通 Museum')
    kiosk_enabled = Column(Boolean, default=False, nullable=False, comment='是否开通 Kiosk')
    description = Column(Text, nullable=True, comment='描述信息')
    
    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment='更新时间')
    
    def __repr__(self):
        return f"<Workspace {self.code}: {self.name}>"
    
    @property
    def is_default(self) -> bool:
        """是否为默认工作空间"""
        return self.id == 'default' or self.code == 'default'


# ========== Pydantic Schemas ==========

from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    """创建工作空间请求"""
    name: str = Field(..., min_length=2, max_length=128, description="企业名称")
    code: str = Field(..., min_length=2, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$', description="唯一标识码")
    plan: str = Field(default='free', description="套餐类型")
    max_users: int = Field(default=10, ge=1, le=10000, description="最大用户数")
    description: Optional[str] = Field(default=None, description="描述")
    museum_enabled: bool = Field(default=False, description="是否开通 Museum")
    kiosk_enabled: bool = Field(default=False, description="是否开通 Kiosk")
    
    # 初始管理员信息
    admin_username: str = Field(..., min_length=3, max_length=64, description="管理员用户名")
    admin_password: str = Field(..., min_length=6, max_length=128, description="管理员密码")
    admin_email: Optional[str] = Field(default=None, description="管理员邮箱")


class WorkspaceUpdate(BaseModel):
    """更新工作空间请求"""
    name: Optional[str] = Field(default=None, min_length=2, max_length=128)
    plan: Optional[str] = Field(default=None)
    max_users: Optional[int] = Field(default=None, ge=1, le=10000)
    description: Optional[str] = Field(default=None)
    is_active: Optional[bool] = Field(default=None)
    museum_enabled: Optional[bool] = Field(default=None)
    kiosk_enabled: Optional[bool] = Field(default=None)


class WorkspaceAdminResponse(BaseModel):
    """租户管理员响应"""
    id: str
    username: str
    email: Optional[str] = None
    disabled: bool = False

    class Config:
        from_attributes = True


class WorkspaceAdminUpdate(BaseModel):
    """租户管理员更新请求"""
    username: Optional[str] = Field(default=None, min_length=3, max_length=64)
    email: Optional[str] = Field(default=None, max_length=128)
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)


class WorkspaceResponse(BaseModel):
    """工作空间响应"""
    id: str
    name: str
    code: str
    plan: str
    max_users: int
    is_active: bool
    museum_enabled: bool = False
    kiosk_enabled: bool = False
    description: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class WorkspaceWithAdmin(BaseModel):
    """创建工作空间响应（包含管理员信息）"""
    workspace: WorkspaceResponse
    admin_username: str
    admin_id: str
