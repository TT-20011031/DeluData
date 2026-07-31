"""
平台管理员 Pydantic 模型 schemas

对应 sys_platform_admins 表的 API 交互模型
"""
from typing import Optional
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ========== 基础模型 (Shared) ==========

class PlatformAdminBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: Optional[EmailStr] = Field(None, description="邮箱")
    is_active: Optional[bool] = Field(True, description="是否启用")


# ========== 创建模型 (Create) ==========

class PlatformAdminCreate(PlatformAdminBase):
    password: str = Field(..., min_length=6, description="密码")


# ========== 更新模型 (Update) ==========

class PlatformAdminUpdate(BaseModel):
    """
    管理员更新模型
    所有字段均为可选，支持 partial update
    """
    password: Optional[str] = Field(None, min_length=6, description="新密码 (留空则不修改)")
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None


# ========== 响应模型 (Response) ==========

class PlatformAdminResponse(PlatformAdminBase):
    id: str
    created_at: datetime
    last_login: Optional[datetime] = None
    last_login_ip: Optional[str] = None
    
    class Config:
        from_attributes = True
