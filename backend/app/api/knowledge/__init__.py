"""
知识库 API Package

路由聚合入口
"""
from fastapi import APIRouter

from .documents import router as doc_router
from .folders import router as folder_router
from .relationships import router as rel_router
from .stats import router as stats_router
from .images import router as images_router
from .tasks import router as tasks_router

# 创建主路由
router = APIRouter(prefix="/knowledge", tags=["Knowledge"])

# 挂载子路由
router.include_router(doc_router)
router.include_router(folder_router)
router.include_router(rel_router)
router.include_router(stats_router)
router.include_router(images_router)
router.include_router(tasks_router)

__all__ = ["router"]
