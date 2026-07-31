"""
平台管理员独立鉴权模块

与租户用户鉴权完全隔离，使用独立的：
1. OAuth2PasswordBearer (tokenUrl)
2. Token 类型 (type=platform)
3. 用户表 (sys_platform_admins)
"""
import os
import logging
from typing import Optional
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from passlib.context import CryptContext

from app.api.deps import get_async_db
from app.config import get_settings
from app.models.platform.admin import PlatformAdminModel

logger = logging.getLogger(__name__)

# ========== 配置 ==========

settings = get_settings()

# [No Hardcoding] 从环境变量读取密钥
_default_secret = "deludata-platform-secret-key-change-in-production"
PLATFORM_SECRET_KEY = os.getenv(
    "PLATFORM_JWT_SECRET_KEY", 
    os.getenv("JWT_SECRET_KEY", _default_secret)
)

# [安全警告] 生产环境必须设置环境变量
if PLATFORM_SECRET_KEY == _default_secret:
    logger.warning(
        "[安全警告] 正在使用默认 JWT 密钥！"
        "生产环境请设置环境变量 PLATFORM_JWT_SECRET_KEY"
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 小时

# 密码哈希上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# [Swagger 隔离] 独立的 OAuth2 Scheme，指向平台登录接口
platform_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/platform/auth/login",
    scheme_name="PlatformAuth"  # Swagger UI 中显示区分
)


# ========== Pydantic 模型 ==========

class PlatformTokenData(BaseModel):
    """平台管理员 Token 载荷"""
    admin_id: str
    username: str
    type: str = "platform"  # 类型标识，用于隔离验证


class PlatformAdmin(BaseModel):
    """平台管理员 API 响应模型"""
    id: str
    username: str
    email: Optional[str] = None
    is_active: bool = True


class PlatformLoginRequest(BaseModel):
    """平台登录请求"""
    username: str
    password: str


class PlatformLoginResponse(BaseModel):
    """平台登录响应"""
    access_token: str
    token_type: str = "bearer"
    admin: PlatformAdmin


# ========== 密码工具 ==========

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return pwd_context.hash(password)


# ========== Token 工具 ==========

def create_platform_token(admin: PlatformAdminModel, expires_delta: Optional[timedelta] = None) -> str:
    """
    创建平台管理员访问 Token
    
    [深度防御] type=platform 用于区分租户 Token
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {
        "sub": admin.id,
        "username": admin.username,
        "type": "platform",  # [关键] Token 类型标识
        "exp": expire
    }
    
    encoded_jwt = jwt.encode(to_encode, PLATFORM_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_platform_token(token: str) -> Optional[PlatformTokenData]:
    """
    解码并验证平台管理员 Token
    
    [安全] 严格检查 type=platform
    """
    try:
        payload = jwt.decode(token, PLATFORM_SECRET_KEY, algorithms=[ALGORITHM])
        
        # [关键] 验证 Token 类型
        token_type = payload.get("type")
        if token_type != "platform":
            logger.warning(f"[安全] Token 类型不匹配: expected=platform, got={token_type}")
            return None
        
        admin_id: str = payload.get("sub")
        username: str = payload.get("username")
        
        if admin_id is None:
            return None
            
        return PlatformTokenData(
            admin_id=admin_id,
            username=username,
            type=token_type
        )
    except JWTError as e:
        logger.warning(f"[安全] Token 解码失败: {e}")
        return None


# ========== 数据库操作 ==========

async def get_platform_admin_by_username(db: AsyncSession, username: str) -> Optional[PlatformAdminModel]:
    """通过用户名查询平台管理员"""
    result = await db.execute(
        select(PlatformAdminModel).where(PlatformAdminModel.username == username)
    )
    return result.scalar_one_or_none()


async def get_platform_admin_by_id(db: AsyncSession, admin_id: str) -> Optional[PlatformAdminModel]:
    """通过 ID 查询平台管理员"""
    result = await db.execute(
        select(PlatformAdminModel).where(PlatformAdminModel.id == admin_id)
    )
    return result.scalar_one_or_none()


async def authenticate_platform_admin(
    db: AsyncSession, 
    username: str, 
    password: str,
    client_ip: Optional[str] = None
) -> Optional[PlatformAdminModel]:
    """
    认证平台管理员
    
    [安全] 包含防爆破检查和登录记录
    """
    admin = await get_platform_admin_by_username(db, username)
    
    if not admin:
        return None
    
    # 检查账号状态
    if not admin.is_active:
        logger.warning(f"[安全] 禁用账号尝试登录: {username}")
        return None
    
    # 检查爆破锁定 (5 次失败后锁定)
    if admin.login_attempts >= 5:
        logger.warning(f"[安全] 账号已锁定 (爆破保护): {username}")
        return None
    
    # 验证密码
    if not verify_password(password, admin.password_hash):
        # 增加失败计数
        admin.login_attempts += 1
        await db.commit()
        logger.warning(f"[安全] 密码错误 ({admin.login_attempts}/5): {username}")
        return None
    
    # 登录成功，重置计数并记录
    admin.login_attempts = 0
    admin.last_login = datetime.utcnow()
    if client_ip:
        admin.last_login_ip = client_ip
    await db.commit()
    
    logger.info(f"[平台] 管理员登录成功: {username} (IP: {client_ip})")
    return admin


# ========== FastAPI 依赖 ==========

async def get_current_platform_admin(
    token: str = Depends(platform_oauth2_scheme),
    db: AsyncSession = Depends(get_async_db)
) -> PlatformAdminModel:
    """
    获取当前平台管理员
    
    [独立鉴权] 使用独立的 Token 解码和用户表查询
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证平台管理员凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # 解码 Token
    token_data = decode_platform_token(token)
    if token_data is None:
        raise credentials_exception
    
    # 从 sys_platform_admins 查询（非 users 表）
    admin = await get_platform_admin_by_id(db, token_data.admin_id)
    if admin is None:
        raise credentials_exception
    
    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用"
        )
    
    return admin


def platform_admin_to_response(admin: PlatformAdminModel) -> PlatformAdmin:
    """将数据库模型转换为 API 响应"""
    return PlatformAdmin(
        id=admin.id,
        username=admin.username,
        email=admin.email,
        is_active=admin.is_active
    )
