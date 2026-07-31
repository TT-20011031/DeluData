"""平台管理 API 模块"""
from app.api.platform.router import router
from app.api.platform.auth import router as auth_router

__all__ = ['router', 'auth_router']
