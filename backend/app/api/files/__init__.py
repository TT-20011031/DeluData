"""
Files API 模块

文件系统管理、图片服务
"""
from fastapi import APIRouter

from .filesystem import router as filesystem_router
from .images import router as images_router

router = APIRouter()

# 聚合子路由
router.include_router(filesystem_router, tags=["文件管理"])
router.include_router(images_router, tags=["图片服务"])

__all__ = ["router"]
