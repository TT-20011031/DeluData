"""
OfficeWorker 子模块包

策略模式:
- CreationHandler: Markdown → Word 快速生成
"""
from .handlers.base import TaskContext, TaskHandler

__all__ = ["TaskContext", "TaskHandler"]
