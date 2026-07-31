"""
DeluData 多租户隔离核心 - TenantMixin

提供 ORM 层面的自动租户隔离：
1. 查询自动过滤 (Read)
2. 写入自动注入 (Write)

使用方式：
    class MyModel(Base, TenantMixin):
        ...
"""
import logging
from contextvars import ContextVar
from typing import Optional

from sqlalchemy import Column, String, event
from sqlalchemy.orm import Query, Mapped, mapped_column

logger = logging.getLogger(__name__)


# ========== 租户上下文（线程/协程安全）==========

_current_workspace_id: ContextVar[Optional[str]] = ContextVar('workspace_id', default=None)


def set_current_workspace(workspace_id: str) -> None:
    """
    设置当前请求的租户 ID
    
    在 deps.py 的 get_user_context 中调用
    
    Args:
        workspace_id: 工作空间 ID
    """
    _current_workspace_id.set(workspace_id)
    logger.debug(f"[Tenant] 设置当前租户: {workspace_id}")


def get_current_workspace() -> Optional[str]:
    """
    获取当前请求的租户 ID
    
    Returns:
        当前租户 ID，未设置时返回 None
    """
    return _current_workspace_id.get()


def clear_current_workspace() -> None:
    """清除当前租户上下文（请求结束时调用）"""
    _current_workspace_id.set(None)


# ========== 租户感知 Mixin ==========

class TenantMixin:
    """
    租户感知 Mixin (SQLAlchemy 2.0 语法)
    
    所有需要租户隔离的业务表都应继承此 Mixin
    
    Features:
        - 自动添加 workspace_id 列
        - 查询时自动过滤（通过 before_compile 事件）
        - 写入时自动注入（通过 before_insert 事件）
    
    Example:
        class SkillModel(Base, TenantMixin):
            __tablename__ = 'skills'
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            name: Mapped[str] = mapped_column(String(64))
            # workspace_id 自动继承自 TenantMixin
    """
    # [SQLAlchemy 2.0] 使用 Mapped 类型注解语法
    workspace_id: Mapped[str] = mapped_column(
        String(36), 
        nullable=False, 
        index=True, 
        default='default',
        comment='租户ID（工作空间）'
    )


# ========== SQLAlchemy 事件监听器 ==========

# 标记：是否绕过租户过滤（用于平台超管查询）
_bypass_tenant_filter: ContextVar[bool] = ContextVar('bypass_tenant', default=False)


def bypass_tenant_filter():
    """
    临时绕过租户过滤（用于平台超管操作）
    
    Usage:
        from app.core.db.tenant_mixin import bypass_tenant_filter
        
        with bypass_tenant_filter():
            # 这里的查询不会自动过滤
            all_users = await session.execute(select(User))
    """
    from contextlib import contextmanager
    
    @contextmanager
    def _bypass():
        _bypass_tenant_filter.set(True)
        try:
            yield
        finally:
            _bypass_tenant_filter.set(False)
    
    return _bypass()


def _is_tenant_aware_model(model) -> bool:
    """
    检查模型是否为租户感知模型
    
    判断条件：
    1. 模型继承了 TenantMixin（推荐方式）
    2. 或者模型有 workspace_id 属性（兼容已有模型）
    """
    if model is None:
        return False
    # [兼容] 只要模型有 workspace_id 字段就启用自动过滤
    return hasattr(model, 'workspace_id')


# 查询拦截器：自动添加 WHERE workspace_id = ...
@event.listens_for(Query, "before_compile", retval=True)
def _add_tenant_filter(query):
    """
    自动为所有查询添加租户过滤
    
    触发条件：
    - 模型继承了 TenantMixin
    - 当前上下文有 workspace_id
    - 未设置 bypass 标志
    """
    # 检查是否绕过
    if _bypass_tenant_filter.get():
        return query
    
    workspace_id = get_current_workspace()
    if not workspace_id:
        return query  # 未设置租户上下文时不过滤
    
    for entity in query.column_descriptions:
        model = entity.get('entity')
        if _is_tenant_aware_model(model):
            # 添加租户过滤条件
            query = query.filter(model.workspace_id == workspace_id)
            logger.debug(f"[Tenant] 自动过滤 {model.__tablename__} WHERE workspace_id='{workspace_id}'")
    
    return query


# 写入拦截器：自动填充 workspace_id
@event.listens_for(TenantMixin, 'before_insert', propagate=True)
def _inject_tenant_id(mapper, connection, target):
    """
    写入数据时，自动填充当前租户 ID
    
    如果 workspace_id 未设置或为默认值，则使用当前上下文的租户 ID
    """
    current_ws = get_current_workspace()
    
    if current_ws:
        # 如果目标对象的 workspace_id 是默认值或未设置，则注入当前租户
        if not target.workspace_id or target.workspace_id == 'default':
            target.workspace_id = current_ws
            logger.debug(f"[Tenant] 自动注入 workspace_id='{current_ws}' 到 {type(target).__name__}")


# ========== 导出 ==========

__all__ = [
    'TenantMixin',
    'set_current_workspace',
    'get_current_workspace',
    'clear_current_workspace',
    'bypass_tenant_filter',
]
