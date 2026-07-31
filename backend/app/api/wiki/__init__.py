"""Wiki API 模块（管理端，租户内可用）。

路由前缀 /api/wiki，挂载在 admin-backend 上。
所有接口都需要登录用户；编译/编辑接口需要管理员权限。
"""
from app.api.wiki.router import router

__all__ = ["router"]
