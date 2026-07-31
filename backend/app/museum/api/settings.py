"""
博物馆模块 - 设置 API

提供部门列表等配置信息（租户内鉴权）
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_async_db, get_user_context
from app.models.auth.organization import DepartmentModel
from app.models.common.context import UserContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["博物馆设置"])


class DepartmentOption(BaseModel):
    """部门选项"""
    id: int
    name: str
    code: Optional[str] = None


@router.get("/departments", response_model=List[DepartmentOption], summary="获取部门列表")
async def list_departments(
    db: AsyncSession = Depends(get_async_db),
    user_context: UserContext = Depends(get_user_context),
):
    """
    获取可用部门列表（需登录并按租户隔离）
    
    用于博物馆前端选择知识库隔离范围
    """
    result = await db.execute(
        select(DepartmentModel)
        .where(
            DepartmentModel.workspace_id == user_context.workspace_id,
            DepartmentModel.status == True,
        )
        .order_by(DepartmentModel.order_num)
    )
    departments = result.scalars().all()
    
    return [
        DepartmentOption(
            id=d.id,
            name=d.name,
            code=d.code
        )
        for d in departments
    ]
