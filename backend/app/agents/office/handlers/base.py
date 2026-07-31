"""
TaskHandler 抽象基类和 TaskContext 数据类

策略模式基础设施
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List


@dataclass(frozen=True)
class TaskContext:
    """
    任务上下文（不可变）
    
    frozen=True 防止 Handler 意外修改原始数据
    """
    task_description: str
    sandbox_path: str
    session_id: str
    user_id: str = ""
    parent_step_id: str = ""
    user_context: Optional[Dict[str, Any]] = None
    template_id: Optional[int] = None
    template_version: Optional[str] = None
    template_mode: Optional[str] = None
    output_filename: Optional[str] = None
    file_context: Optional[Dict[str, str]] = None
    messages: Optional[List[Any]] = None
    memory_dfs: Optional[Dict[str, Any]] = None
    # 会话轮次 当前执行轮次，用于 SSE 事件关联
    round_index: int = 0
    # [P1] 任务执行结果，用于数据源匹配
    execution_results: Optional[List[Dict]] = None
    
    def with_updates(self, **kwargs) -> "TaskContext":
        """创建带更新的新实例"""
        return TaskContext(
            task_description=kwargs.get("task_description", self.task_description),
            sandbox_path=kwargs.get("sandbox_path", self.sandbox_path),
            session_id=kwargs.get("session_id", self.session_id),
            user_id=kwargs.get("user_id", self.user_id),
            parent_step_id=kwargs.get("parent_step_id", self.parent_step_id),
            user_context=kwargs.get("user_context", self.user_context),
            template_id=kwargs.get("template_id", self.template_id),
            template_version=kwargs.get("template_version", self.template_version),
            template_mode=kwargs.get("template_mode", self.template_mode),
            output_filename=kwargs.get("output_filename", self.output_filename),
            file_context=kwargs.get("file_context", self.file_context),
            messages=kwargs.get("messages", self.messages),
            memory_dfs=kwargs.get("memory_dfs", self.memory_dfs),
            round_index=kwargs.get("round_index", self.round_index),
            execution_results=kwargs.get("execution_results", self.execution_results),
        )



class TaskHandler(ABC):
    """
    任务处理器抽象基类
    
    所有具体 Handler 必须实现 handle 方法
    """
    
    @abstractmethod
    async def handle(self, ctx: TaskContext) -> Dict[str, Any]:
        """
        处理任务
        
        参数:
            ctx: 任务上下文（不可变）
            
        参数:
            {
                "success": bool,
                "output": str,
                "output_files": [...],
                "error": str (可选),
                ...
            }
        """
        ...
    
    async def on_step(self, ctx: TaskContext, name: str, status: str, detail: str):
        """发送 SSE 步骤更新"""
        if ctx.session_id:
            from app.api.events import emit_step_update
            try:
                await emit_step_update(
                    session_id=ctx.session_id,
                    step_id=f"office-{name}",
                    status=status,
                    label=f"{name}: {detail[:50]}..." if len(detail) > 50 else f"{name}: {detail}",
                    parent_step_id=ctx.parent_step_id or None,
                    round_index=ctx.round_index  # 会话轮次 透传轮次
                )
            except Exception:
                pass  # SSE 失败不影响主流程

