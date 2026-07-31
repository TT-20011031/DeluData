"""
认证 API - Pydantic 模型

请求/响应的数据验证
"""
from typing import Optional
from pydantic import BaseModel, Field

from app.core.security.auth import ACCESS_TOKEN_EXPIRE_MINUTES


class LoginRequest(BaseModel):
    """登录请求"""
    username: str
    password: str


class RegisterRequest(BaseModel):
    """注册请求"""
    username: str
    password: str
    email: Optional[str] = None


class TokenResponse(BaseModel):
    """Token 响应"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_EXPIRE_MINUTES * 60
    user: dict


class UserResponse(BaseModel):
    """用户信息响应"""
    id: str
    username: str
    email: Optional[str]
    role: str
    workspace_id: str
    permissions: list[str] = Field(default_factory=list)
    is_workspace_admin: bool = False
    workspace_features: dict = Field(default_factory=dict)
