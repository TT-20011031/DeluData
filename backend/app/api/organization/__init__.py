"""
Organization API 模块

组织管理（部门、RBAC 权限）
"""
from fastapi import APIRouter

from .department import router as department_router
from .rbac import router as rbac_router
from .authorization import router as authorization_router
from .semantic_profiles import router as semantic_profiles_router

router = APIRouter()

# 聚合子路由
router.include_router(department_router, tags=["部门管理"])
router.include_router(rbac_router, tags=["RBAC权限"])

router.include_router(authorization_router, tags=["组织权限中心"])
router.include_router(semantic_profiles_router, tags=["组织语义画像"])

__all__ = ["router"]
