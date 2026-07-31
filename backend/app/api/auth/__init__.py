"""
Auth API 模块

认证相关的 API 端点
"""
from .router import router

# 为保持向后兼容，从 deps.py 重新导出依赖函数
# 新代码应直接使用 from app.api.deps import ...
from app.api.deps import (
    get_current_user,
    get_current_user_optional,
    get_current_admin,
    get_user_context,
    get_user_context_optional,
)

__all__ = [
    "router",
    "get_current_user",
    "get_current_user_optional", 
    "get_current_admin",
    "get_user_context",
    "get_user_context_optional",
]
