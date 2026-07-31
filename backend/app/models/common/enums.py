"""
知识库枚举与常量

放在 models 层避免循环导入
"""
from enum import Enum


class DocumentStatus(str, Enum):
    """
    文档处理状态
    
    与 sql_graph.py 中的 file_status_enum 保持一致
    """
    PROCESSING = "processing"
    INDEXED = "indexed"
    ERROR = "error"


class DeleteStatus(str, Enum):
    """文件删除状态机"""

    ACTIVE = "active"
    PENDING_VECTOR_DELETE = "pending_delete"
    VECTOR_DELETED = "vector_deleted"
    DELETE_FAILED = "delete_failed"


class Visibility(str, Enum):
    """可见范围"""
    PUBLIC = "public"
    DEPT = "dept"
    PRIVATE = "private"
