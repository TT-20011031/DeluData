"""
认证模块

JWT Token 生成、验证和用户认证
已迁移到 SQL 后端，使用 UserModel
"""
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional
from passlib.context import CryptContext
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.security.capabilities import CAPABILITIES
from app.models.auth.rbac import UserModel, RoleModel

# 密码哈希上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ========== 配置 ==========

settings = get_settings()
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "deludata-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 小时


# ========== 模型定义 ==========

class TokenData(BaseModel):
    """Token 载荷数据"""
    user_id: str
    username: str
    role: str
    workspace_id: str


class User(BaseModel):
    """用户模型 (API 响应用)"""
    id: str
    username: str
    email: Optional[str] = None
    role: str = "user"
    workspace_id: str = "default"
    disabled: bool = False
    permissions: list[str] = Field(default_factory=list)  # 聚合权限列表
    department_id: Optional[int] = None
    data_scope: int = 4  # 数据范围 (1=全部, 2=本部门及以下, 3=本部门, 4=仅本人)
    capability_scopes: dict[str, list[int] | str] = Field(default_factory=dict)
    denied_capability_scopes: dict[str, list[int] | str] = Field(default_factory=dict)
    authorization_revision: int = 0
    is_workspace_admin: bool = False


# ========== 密码工具 ==========

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)


# ========== Token 工具 ==========

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    创建访问 Token
    
    Args:
        data: Token 载荷数据
        expires_delta: 过期时间增量
        
    Returns:
        编码后的 JWT Token
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[TokenData]:
    """
    解码并验证 Token
    
    Args:
        token: JWT Token 字符串
        
    Returns:
        Token 数据，验证失败返回 None
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        username: str = payload.get("username")
        role: str = payload.get("role", "user")
        workspace_id: str = payload.get("workspace_id", "default")
        
        if user_id is None:
            return None
            
        return TokenData(
            user_id=user_id,
            username=username,
            role=role,
            workspace_id=workspace_id
        )
    except JWTError:
        return None


# ========== 异步数据库操作 (SQL 后端) ==========

async def get_user_by_username(db: AsyncSession, username: str) -> Optional[UserModel]:
    """
    通过用户名查询用户 (预加载角色和权限)
    
    使用 selectinload 避免 N+1 查询和 Lazy Loading 错误
    """
    result = await db.execute(
        select(UserModel)
        .where(UserModel.username == username)
        .options(
            selectinload(UserModel.roles).selectinload(RoleModel.permissions)
        )
    )
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[UserModel]:
    """
    通过用户ID查询用户 (预加载角色和权限)
    """
    result = await db.execute(
        select(UserModel)
        .where(UserModel.id == user_id)
        .options(
            selectinload(UserModel.roles).selectinload(RoleModel.permissions)
        )
    )
    return result.scalar_one_or_none()


async def authenticate_user_async(db: AsyncSession, username: str, password: str) -> Optional[UserModel]:
    """
    异步认证用户
    
    Args:
        db: 异步数据库会话
        username: 用户名
        password: 明文密码
        
    Returns:
        认证成功返回用户，失败返回 None
    """
    user = await get_user_by_username(db, username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if user.disabled:
        return None
    return user


async def create_user_async(
    db: AsyncSession, 
    username: str, 
    password: str, 
    email: Optional[str] = None,
    workspace_id: str = "default",
    department_id: Optional[int] = None
) -> UserModel:
    """
    异步创建新用户 (纯 RBAC)
    
    Args:
        db: 异步数据库会话
        username: 用户名
        password: 明文密码
        email: 邮箱（可选）
        workspace_id: 租户ID
        department_id: 部门ID（可选）
        
    Returns:
        新创建的用户
        
    Raises:
        ValueError: 用户名已存在
    """
    # 检查用户名是否已存在
    existing = await get_user_by_username(db, username)
    if existing:
        raise ValueError(f"用户名 '{username}' 已存在")
    
    # 创建用户 (纯 RBAC：不设 is_superuser)
    user = UserModel(
        id=str(uuid.uuid4()),
        username=username,
        email=email,
        hashed_password=get_password_hash(password),
        workspace_id=workspace_id,
        disabled=False,
        department_id=department_id
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return user


def user_model_to_user(user_db: UserModel) -> User:
    """
    将数据库 UserModel 转换为 API User 对象
    
    纯 RBAC：权限完全来自角色，无上帝账号
    """
    # 聚合所有角色的权限
    all_permissions = set()
    max_data_scope = 4  # 默认仅本人
    
    for role in user_db.roles:
        for perm in role.permissions:
            all_permissions.add(perm.code)
        # 取最大数据范围 (数字越小权限越大)
        if role.data_scope and role.data_scope < max_data_scope:
            max_data_scope = role.data_scope
    
    # 如果拥有 * 权限，data_scope 设为全部
    if "*" in all_permissions:
        max_data_scope = 1
    
    # [架构优化] 基于权限判断显示角色，而非角色名
    if "*" in all_permissions:
        display_role = "admin"
    elif user_db.roles:
        display_role = user_db.roles[0].name.lower()
    else:
        display_role = "user"
    
    return User(
        id=user_db.id,
        username=user_db.username,
        email=user_db.email,
        role=display_role,
        workspace_id=user_db.workspace_id,
        disabled=user_db.disabled,
        permissions=list(all_permissions),
        department_id=user_db.department_id,
        data_scope=max_data_scope
    )


def apply_workspace_admin_access(user: User, workspace_owner_id: str | None) -> User:
    """Apply the workspace-owner bypass without introducing a global wildcard."""
    if workspace_owner_id and str(workspace_owner_id) == str(user.id):
        user.role = "admin"
        user.permissions = sorted(CAPABILITIES)
        user.data_scope = 1
        user.capability_scopes = {code: "*" for code in sorted(CAPABILITIES)}
        user.denied_capability_scopes = {}
        user.is_workspace_admin = True
    return user

