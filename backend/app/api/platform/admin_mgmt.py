"""
平台管理员管理 API

提供对 sys_platform_admins 表的增删改查
仅限平台管理员访问
"""
import uuid
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.context import CryptContext

from app.api.deps import get_async_db
from app.api.platform.deps import get_current_platform_admin
from app.api.platform.schemas import (
    PlatformAdminResponse, 
    PlatformAdminCreate, 
    PlatformAdminUpdate
)
from app.models.platform.admin import PlatformAdminModel

logger = logging.getLogger(__name__)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

router = APIRouter(prefix="/admins", tags=["管理员管理"])

# ============ 工具函数 ============

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ============ CRUD 接口 ============

@router.get("", response_model=List[PlatformAdminResponse])
async def list_admins(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取平台管理员列表
    """
    stmt = select(PlatformAdminModel).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=PlatformAdminResponse, status_code=status.HTTP_201_CREATED)
async def create_admin(
    admin_in: PlatformAdminCreate,
    current_admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    创建新的平台管理员
    """
    # 检查用户名是否已存在
    stmt = select(PlatformAdminModel).where(PlatformAdminModel.username == admin_in.username)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在"
        )

    new_admin = PlatformAdminModel(
        id=str(uuid.uuid4()),
        username=admin_in.username,
        email=admin_in.email,
        password_hash=get_password_hash(admin_in.password),
        is_active=admin_in.is_active
    )
    
    db.add(new_admin)
    await db.commit()
    await db.refresh(new_admin)
    
    logger.info(f"[平台] 管理员 {current_admin.username} 创建了新管理员 {new_admin.username}")
    return new_admin


@router.put("/{admin_id}", response_model=PlatformAdminResponse)
async def update_admin(
    admin_id: str,
    admin_in: PlatformAdminUpdate,
    current_admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    更新管理员信息 (密码/状态)
    """
    stmt = select(PlatformAdminModel).where(PlatformAdminModel.id == admin_id)
    result = await db.execute(stmt)
    admin_obj = result.scalar_one_or_none()
    
    if not admin_obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "管理员不存在")

    # 更新字段
    if admin_in.email is not None:
        admin_obj.email = admin_in.email
    
    if admin_in.is_active is not None:
        # 防止禁用自己
        if admin_id == current_admin.id and admin_in.is_active is False:
             raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能禁用当前登录账号")
        admin_obj.is_active = admin_in.is_active
        
    if admin_in.password:
        admin_obj.password_hash = get_password_hash(admin_in.password)

    await db.commit()
    await db.refresh(admin_obj)
    
    logger.info(f"[平台] 管理员 {current_admin.username} 更新了管理员 {admin_obj.username}")
    return admin_obj


@router.delete("/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin(
    admin_id: str,
    current_admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    删除管理员
    """
    if admin_id == current_admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能删除当前登录账号")

    stmt = select(PlatformAdminModel).where(PlatformAdminModel.id == admin_id)
    result = await db.execute(stmt)
    admin_obj = result.scalar_one_or_none()
    
    if not admin_obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "管理员不存在")

    await db.delete(admin_obj)
    await db.commit()
    
    logger.info(f"[平台] 管理员 {current_admin.username} 删除了管理员 {admin_obj.username}")
