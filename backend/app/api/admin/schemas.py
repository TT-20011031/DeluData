"""
管理员 API - Pydantic 模型

用户管理、权限配置等请求/响应模型
"""
from typing import Optional, List
from pydantic import BaseModel


class RoleInfo(BaseModel):
    """角色信息"""
    id: int
    name: str
    description: Optional[str] = None


class DepartmentInfo(BaseModel):
    """部门信息"""
    id: int
    name: str


class UserInfo(BaseModel):
    """用户信息"""
    id: str
    username: str
    email: Optional[str]
    role: str  # 兼容旧版本
    roles: List[RoleInfo] = []  # 完整角色列表
    workspace_id: str
    disabled: bool
    department_id: Optional[int] = None
    department: Optional[DepartmentInfo] = None  # 部门详情


class UserListResponse(BaseModel):
    """用户列表响应"""
    users: List[UserInfo]


class CreateUserRequest(BaseModel):
    """
    创建用户请求 (原子性事务)
    
    支持两种角色指定方式：
    1. role_ids: 直接指定角色 ID 列表（推荐）
    2. role: 兼容旧版本的角色名称 ("admin"/"user")
    """
    username: str
    password: str
    email: Optional[str] = None
    department_id: Optional[int] = None      # 归属部门
    role_ids: Optional[List[int]] = None     # 角色 ID 列表（优先使用）
    role: str = "user"                        # 兼容旧版本


class UpdateUserRequest(BaseModel):
    """更新用户请求 (原子性事务)"""
    email: Optional[str] = None
    role: Optional[str] = None  # 兼容旧版本
    role_ids: Optional[List[int]] = None  # 角色 ID 列表（优先使用）
    disabled: Optional[bool] = None
    department_id: Optional[int] = None


class TablePermissionsRequest(BaseModel):
    """表级权限请求"""
    table_permissions: dict  # {table_name: bool}


class TablePermissionsResponse(BaseModel):
    """表级权限响应"""
    user_id: str
    allowed_tables: List[str]


class ResetPasswordRequest(BaseModel):
    """管理员重置密码请求"""
    new_password: str  # 新密码（至少6位）
