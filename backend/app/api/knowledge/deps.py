"""
知识库 API - 依赖项

统一管理服务注入和用户上下文
"""
from typing import Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.database import get_async_db
from app.api.auth import get_current_user_optional, get_current_admin
from app.core.security.auth import User
from app.models.common.context import UserContext
from app.services.filesystem_service import FilesystemService
from app.services.ingestion_service import IngestionService
from app.services.graph_service import GraphService


def get_user_context(
    user: Optional[User] = Depends(get_current_user_optional)
) -> UserContext:
    """获取用户上下文"""
    if user:
        has_full_access = "*" in user.permissions
        return UserContext(
            user_id=user.id,
            workspace_id=user.workspace_id,
            allowed_tables=["*"] if has_full_access else [],
            role=user.role,
            capabilities=list(user.permissions),
            is_workspace_admin=user.is_workspace_admin,
            dept_id=user.department_id,
            data_scope=user.data_scope
        )
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="需要登录才能访问此功能",
        headers={"WWW-Authenticate": "Bearer"}
    )


async def get_filesystem_service(
    db: AsyncSession = Depends(get_async_db)
) -> FilesystemService:
    """获取文件系统服务"""
    return FilesystemService(db)


async def get_ingestion_service(
    db: AsyncSession = Depends(get_async_db)
) -> IngestionService:
    """获取文档解析服务"""
    return IngestionService(db)


async def get_graph_service(
    db: AsyncSession = Depends(get_async_db)
) -> GraphService:
    """获取图谱服务"""
    return GraphService(db)
