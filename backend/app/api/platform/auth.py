"""
平台管理员认证 API

独立的登录/登出接口
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.api.platform.deps import (
    authenticate_platform_admin,
    create_platform_token,
    platform_admin_to_response,
    get_current_platform_admin,
    PlatformLoginRequest,
    PlatformLoginResponse,
    PlatformAdmin
)
from app.models.platform.admin import PlatformAdminModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["平台认证"])


def get_client_ip(request: Request) -> Optional[str]:
    """获取客户端 IP (支持反向代理)"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/login", response_model=PlatformLoginResponse)
async def platform_login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_async_db)
):
    """
    平台管理员登录
    
    [独立鉴权] 使用 sys_platform_admins 表验证
    
    Returns:
        access_token: 平台管理员专用 Token (type=platform)
    """
    client_ip = get_client_ip(request)
    
    admin = await authenticate_platform_admin(
        db, 
        form_data.username, 
        form_data.password,
        client_ip
    )
    
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 创建平台专用 Token
    access_token = create_platform_token(admin)
    
    return PlatformLoginResponse(
        access_token=access_token,
        token_type="bearer",
        admin=platform_admin_to_response(admin)
    )


@router.get("/me", response_model=PlatformAdmin)
async def get_current_admin_info(
    admin: PlatformAdminModel = Depends(get_current_platform_admin)
):
    """
    获取当前登录的平台管理员信息
    """
    return platform_admin_to_response(admin)


@router.post("/logout")
async def platform_logout(
    admin: PlatformAdminModel = Depends(get_current_platform_admin)
):
    """
    平台管理员登出
    
    [说明] JWT 无状态，前端清除 Token 即可
    """
    logger.info(f"[平台] 管理员登出: {admin.username}")
    return {"success": True, "message": "已登出"}
