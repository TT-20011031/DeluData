"""
DeluData - 通用模型模块

包含:
- 上下文模型 (UserContext, SessionContext, TaskStep)
- 执行结果模型 (ExecutionResult, QueryResult, DocumentChunk)
- 通用枚举 (DocumentStatus, Visibility)
"""

# 上下文模型
from app.models.common.context import (
    UserContext,
    SessionContext,
    TaskStep,
)

# 执行结果模型
from app.models.common.execution import (
    ExecutionResult,
    QueryResult,
    DocumentChunk,
)

# 通用枚举
from app.models.common.enums import (
    DocumentStatus,
    Visibility,
)

__all__ = [
    # 上下文
    "UserContext",
    "SessionContext",
    "TaskStep",
    # 执行结果
    "ExecutionResult",
    "QueryResult",
    "DocumentChunk",
    # 枚举
    "DocumentStatus",
    "Visibility",
]
