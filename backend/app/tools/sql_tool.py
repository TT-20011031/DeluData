"""
SQL 工具 - 将 SqlWorker 封装为 LangChain StructuredTool

职责：
- 接收自然语言查询
- 调用 SqlWorker 执行 NL2SQL + 本地查询
- 返回格式化结果供 Synthesizer 使用
"""
import logging
import time
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import Field

logger = logging.getLogger(__name__)


from app.tools.base import BaseToolInput

class SqlToolInput(BaseToolInput):
    """SQL 工具输入参数"""
    # BaseToolInput covers query, user_id, session_id, parent_step_id, memory_dfs, messages
    original_query: Optional[str] = Field(default=None, description="未经 Planner 改写的原始用户问题")
    summary: str = Field(default="", description="会话长期摘要")
    current_focus_result: Optional[dict] = Field(default=None, description="当前正在讨论的数据结果快照")
    execution_results: Optional[list] = Field(default=None, description="近期 Worker 执行结果")
    semantic_clarification: Optional[dict] = Field(default=None, description="Semantic clarification patch selected by the user")


async def run_sql_task(
    query: str,
    user_id: str,
    session_id: str = "",
    parent_step_id: str = "",
    memory_dfs: Optional[dict] = None,
    user_context: Optional[dict] = None,
    messages: Optional[list] = None,
    summary: str = "",
    current_focus_result: Optional[dict] = None,
    execution_results: Optional[list] = None,
    round_index: int = 0,  # [P1] 用于 df_key 语义化命名
    semantic_clarification: Optional[dict] = None,
    original_query: Optional[str] = None,
    **kwargs
) -> "WorkerResult":
    """
    执行 SQL 查询任务
    
    Args:
        query: 自然语言查询描述
        user_id: 用户 ID
        session_id: 会话 ID
        parent_step_id: 父步骤 ID
        round_index: 当前执行轮次（用于 df_key 命名）
        
    Returns:
        格式化的查询结果文本
    """
    from app.agents.sql_worker import get_sql_worker
    
    logger.info(f"sql_tool: 执行查询, user_id={user_id}, round={round_index}, query={query[:50]}...")
    started = time.perf_counter()
    
    worker = get_sql_worker()
    result = await worker.execute_task(
        task_description=query,
        original_query=original_query,
        user_id=user_id,
        session_id=session_id,
        parent_step_id=parent_step_id,
        round_index=round_index,  # [P1] 传递轮次用于 df_key 命名
        messages=messages or [],
        summary=summary,
        current_focus_result=current_focus_result or {},
        execution_results=execution_results or [],
        semantic_clarification=semantic_clarification,
    )
    
    from app.tools.base import WorkerResult

    # 检查结果是否成功（不抛异常，统一由 Router 消费质量信号）
    if not result.get("success"):
        error_msg = result.get("error", "SQL 执行失败")
        error_type = str(result.get("error_type") or "sql_execution_failed")
        if result.get("semantic_clarification") or error_type in {"permission_rewrite_required", "semantic_clarification_required", "metric_ambiguous"}:
            reason_code = "semantic_clarification_required"
            retryable = False
        elif error_type in {"permission_denied", "sql_permission_denied", "sensitive_object"}:
            reason_code = "sql_permission_denied"
            retryable = False
        elif error_type in {"semantic_model_incomplete", "join_path_ambiguous"}:
            reason_code = "semantic_model_incomplete"
            retryable = False
        else:
            reason_code = error_type
            retryable = bool(result.get("retryable", error_type in {
                "sql_execution_failed",
                "sql_syntax_error",
                "sql_table_not_found",
                "timeout",
                "semantic_unavailable",
            }))

        return WorkerResult(
            output=f"SQL 执行失败：{error_msg}",
            artifacts={},
            quality_signal={
                "verdict": "fail",
                "reason_code": reason_code,
                "confidence": 0.9,
                "retryable": retryable,
                **({"clarification": result.get("semantic_clarification")} if result.get("semantic_clarification") else {}),
            },
            meta={
                "worker_round": round_index,
                "token_used": 0,
                "chunks_used": 0,
                "stop_reason": reason_code,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "success": False,
                "error": error_msg,
                "error_type": error_type,
                "from_semantic": bool(result.get("from_semantic")),
                "semantic_fallback_blocked": bool(result.get("semantic_fallback_blocked")),
                "error_details": result.get("error_details") or {},
                **({"clarification": result.get("semantic_clarification")} if result.get("semantic_clarification") else {}),
            },
        )

    artifacts = result.get("artifacts", {}) or {}
    output_text = worker.format_result_for_synthesizer(result)
    row_count = int(result.get("row_count", 0) or 0)
    if row_count == 0:
        verdict = "partial"
        reason_code = "sql_zero_rows"
    else:
        verdict = "pass"
        reason_code = "sql_ok"

    return WorkerResult(
        output=output_text,
        artifacts=artifacts,
        quality_signal={
            "verdict": verdict,
            "reason_code": reason_code,
            "confidence": 0.9,
            "retryable": False,
        },
        meta={
            "worker_round": round_index,
            "token_used": 0,
            "chunks_used": row_count,
            "stop_reason": reason_code,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        },
    )


# 创建 StructuredTool 实例
sql_tool = StructuredTool.from_function(
    func=None,  # 不提供同步函数
    coroutine=run_sql_task,
    name="sql_worker",
    description="执行数据库查询任务。接收自然语言问题，生成并执行 SQL，返回查询结果。",
    args_schema=SqlToolInput,
)
