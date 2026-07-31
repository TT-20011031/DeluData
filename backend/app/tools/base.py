from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class BaseToolInput(BaseModel):
    """
    标准工具输入 Schema
    """
    query: str = Field(description="自然语言查询或任务描述")
    user_id: str = Field(default="default_user", description="与请求关联的用户 ID")
    session_id: str = Field(default="", description="用于上下文和 SSE 的会话 ID")
    parent_step_id: str = Field(default="", description="用于追踪的父步骤 ID")
    user_context: Optional[Dict[str, Any]] = Field(default=None, description="用户上下文 (权限, 允许的表)")
    # 用于上下文传递
    messages: Optional[List[Any]] = Field(default=None, description="对话历史")
    memory_dfs: Optional[Dict[str, Any]] = Field(default=None, description="DataFrame 的共享内存 (df_key -> df)")
    # [Session Round] 当前执行轮次，用于 SSE 事件关联
    round_index: int = Field(default=0, description="当前执行轮次，用于 SSE 事件与消息关联")


class WorkerResult(BaseModel):
    """
    标准 Worker 输出 Schema
    """
    output: str = Field(description="文本输出或总结")
    artifacts: Dict[str, Any] = Field(default_factory=dict, description="结构化数据产物 (如 DataFrames, 文件)")
    quality_signal: Dict[str, Any] = Field(
        default_factory=dict,
        description="质量信号（只描述执行结果，不直接携带路由动作）"
    )
    meta: Dict[str, Any] = Field(
        default_factory=dict,
        description="执行元信息（token/chunks/latency/stop_reason）"
    )

