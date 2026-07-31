"""
后台任务管理器

处理文档解析、审计日志等异步任务
"""
import asyncio
import logging
from typing import Callable, Any, Optional
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BackgroundTask:
    """后台任务"""
    def __init__(
        self,
        task_id: str,
        task_type: str,
        func: Callable,
        *args,
        **kwargs
    ):
        self.task_id = task_id
        self.task_type = task_type
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.status = TaskStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None


class TaskManager:
    """
    后台任务管理器
    
    管理异步任务的创建、执行和状态跟踪
    """
    
    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}
        self._event_callbacks: dict[str, list[Callable]] = {}
    
    def create_task(
        self,
        task_id: str,
        task_type: str,
        func: Callable,
        *args,
        **kwargs
    ) -> BackgroundTask:
        """
        创建后台任务
        """
        task = BackgroundTask(task_id, task_type, func, *args, **kwargs)
        self._tasks[task_id] = task
        return task
    
    async def run_task(self, task_id: str) -> Any:
        """
        执行后台任务
        """
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()
        
        try:
            # 判断是否是协程函数
            if asyncio.iscoroutinefunction(task.func):
                result = await task.func(*task.args, **task.kwargs)
            else:
                # 同步函数放到线程池执行
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, 
                    lambda: task.func(*task.args, **task.kwargs)
                )
            
            task.result = result
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            
            # 触发完成回调
            await self._emit_event(task_id, "completed", result)
            
            return result
            
        except Exception as e:
            task.error = str(e)
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now()
            
            logger.error(f"后台任务失败 [{task_id}]: {e}")
            
            # 触发失败回调
            await self._emit_event(task_id, "failed", str(e))
            
            raise
    
    def schedule_task(
        self,
        task_id: str,
        task_type: str,
        func: Callable,
        *args,
        **kwargs
    ) -> str:
        """
        调度任务（创建并异步执行）
        
        返回任务 ID，任务在后台执行
        """
        task = self.create_task(task_id, task_type, func, *args, **kwargs)
        
        # 创建任务但不等待
        asyncio.create_task(self.run_task(task_id))
        
        return task_id
    
    def get_task_status(self, task_id: str) -> Optional[dict]:
        """获取任务状态"""
        task = self._tasks.get(task_id)
        if not task:
            return None
        
        return {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "status": task.status,
            "error": task.error,
            "created_at": task.created_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        }
    
    def on_event(self, task_id: str, callback: Callable):
        """注册任务事件回调"""
        if task_id not in self._event_callbacks:
            self._event_callbacks[task_id] = []
        self._event_callbacks[task_id].append(callback)
    
    async def _emit_event(self, task_id: str, event_type: str, data: Any):
        """触发任务事件"""
        callbacks = self._event_callbacks.get(task_id, [])
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event_type, data)
                else:
                    callback(event_type, data)
            except Exception as e:
                logger.error(f"事件回调执行失败: {e}")


# 全局任务管理器
_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """获取任务管理器单例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
