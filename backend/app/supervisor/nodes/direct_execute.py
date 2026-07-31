"""
直连执行节点 - 跳过 Planner 直接调用 Worker

职责：
- 根据 execution_mode 直接调用对应 Worker
- 跳过 Planner、Executor、RouterAgent 完整循环
- 单次执行，无反思重试

设计原则：
- SSE 事件显式发送（规避历史 Auto-Confirm 问题）
- 不使用 aupdate_state，避免 as_node 参数陷阱
- 终结类任务前置数据源检查
"""
import asyncio
import logging
from typing import Dict, Any

from app.supervisor.state import SupervisorState
from app.agents.sql_worker import get_sql_worker
from app.agents.office_worker import get_office_worker
from app.tools.chart_tool import run_chart_task
from app.models.common.context import UserContext
from app.api.events import emit_step_update, emit_plan_complete
from app.config import get_settings
from app.supervisor.nodes.readiness_guard import (
    build_readiness_state_patch,
    resolve_runtime_readiness,
)
from app.services.workspace_readiness_service import WorkspaceReadinessService

logger = logging.getLogger(__name__)

# 模式 -> Worker 映射
MODE_WORKER_MAP = {
    "rag_only": "doc_worker",
    "sql_only": "sql_worker",
    "chart_only": "chart_worker",
    "office_only": "office_worker",
}

# 需要数据源的终结类模式
TERMINAL_MODES = {"chart_only", "office_only"}


def _extract_text_content(content: Any) -> str:
    """Extract text from message content that can be str or rich-item list."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
            elif isinstance(item, dict) and "text" in item:
                text_parts.append(str(item.get("text", "")))
        return " ".join([p for p in text_parts if p]).strip()
    return str(content or "").strip()


def _get_previous_user_query(messages: list, current_query: str) -> str | None:
    """Get the previous user query for short follow-up query completion."""
    if not messages:
        return None

    seen_current = False
    for msg in reversed(messages):
        msg_type = (msg.__class__.__name__ or "").lower()
        role = str(getattr(msg, "type", "")).lower()
        if "human" not in msg_type and role != "human":
            continue

        text = _extract_text_content(getattr(msg, "content", ""))
        if not text:
            continue

        if not seen_current and text == (current_query or "").strip():
            seen_current = True
            continue

        if text != (current_query or "").strip():
            return text[:300]
    return None


async def direct_execute_node(state: SupervisorState) -> dict:
    """
    直连执行节点：根据 execution_mode 直接调用对应 Worker
    
    设计要点：
    1. 跳过 Planner，单次执行，无反思循环
    2. SSE 事件显式发送（规避历史问题）
    3. 终结类任务前置数据源检查
    4. 新增 is_direct_execution 标记供 Synthesizer 感知
    """
    execution_mode = state.get("execution_mode", "auto")
    user_query = state.get("user_query", "")
    user_context = state.get("user_context", {})
    session_id = state.get("session_id", "")
    memory_dfs = state.get("memory_dfs", {})
    round_index = state.get("round_index", 0)
    messages = state.get("messages", [])  # 获取对话历史用于 RAG 查询改写
    
    worker_type = MODE_WORKER_MAP.get(execution_mode)
    if not worker_type:
        logger.error(f"[DirectExecute] 未知执行模式: {execution_mode}")
        return {"error": f"未知执行模式: {execution_mode}", "plan_status": "error"}
    
    step_id = "direct_1"
    logger.info(f"[DirectExecute] 模式={execution_mode}, Worker={worker_type}")

    readiness_service = WorkspaceReadinessService()
    runtime_readiness = await resolve_runtime_readiness(
        state,
        default_available=False,
    )
    readiness_state_patch = build_readiness_state_patch(runtime_readiness)

    availability = readiness_service.check_worker_availability(worker_type, runtime_readiness)
    if not availability.allowed:
        logger.warning(
            "[DirectExecute] readiness blocked worker=%s reason=%s",
            worker_type,
            availability.reason_code,
        )
        await emit_step_update(session_id, step_id, "error", availability.message)
        return {
            "error": availability.message,
            "plan_status": "error",
            "task_plan": [
                {
                    "step_id": step_id,
                    "worker": worker_type,
                    "description": user_query,
                    "status": "failed",
                    "result": availability.message,
                }
            ],
            "execution_results": [
                {
                    "step_id": step_id,
                    "worker": worker_type,
                    "error": availability.message,
                    "meta": {"stop_reason": availability.reason_code or "readiness_blocked"},
                }
            ],
            "is_direct_execution": True,
            **readiness_state_patch,
        }    
    # ========== 终结类前置检查 ==========
    if execution_mode in TERMINAL_MODES:
        file_context = user_context.get("file_context", {}) if isinstance(user_context, dict) else {}
        has_uploaded_file = bool(
            user_context.get("file_path")
            or (file_context.get("file_path") if isinstance(file_context, dict) else None)
        )
        has_data = bool(memory_dfs) or has_uploaded_file
        has_ready_source = bool(runtime_readiness.has_db or runtime_readiness.has_knowledge)
        has_usable_input = has_data or has_ready_source

        if not has_usable_input:
            error_msg = (
                f"模式 {execution_mode} 需要可用数据来源。请先上传文件，或先配置数据库/知识库任一数据源。"
            )
            logger.warning(f"[DirectExecute] 终结类无数据源: {error_msg}")
            await emit_step_update(session_id, step_id, "error", error_msg)
            return {
                "error": error_msg,
                "plan_status": "error",
                "task_plan": [{
                    "step_id": step_id,
                    "worker": worker_type,
                    "description": user_query,
                    "status": "failed"
                }],
                "is_direct_execution": True,
                **readiness_state_patch,
            }
    
    # ========== 发送开始事件 ==========
    await emit_step_update(session_id, step_id, "running", f"正在执行 {worker_type}...")
    
    # ========== 执行 Worker ==========
    try:
        result = await _execute_worker(
            worker_type=worker_type,
            query=user_query,
            user_context=user_context,
            session_id=session_id,
            parent_step_id=step_id,
            memory_dfs=memory_dfs,
            round_index=round_index,
            messages=messages,  # 传递对话历史
            summary=state.get("summary", ""),
            current_focus_result=state.get("current_focus_result", {}),
            execution_results=state.get("execution_results", []),
        )
        
        result_meta = result.get("meta", {}) or {}
        has_error = result_meta.get("success") is False or bool(result_meta.get("semantic_fallback_blocked"))
        step_status = "failed" if has_error else "completed"

        # 发送完成事件
        await emit_step_update(
            session_id,
            step_id,
            "error" if has_error else "completed",
            result_meta.get("error") or ("执行失败" if has_error else "执行完成"),
        )
        
        # 构建返回状态
        task_plan = [{
            "step_id": step_id,
            "worker": worker_type,
            "description": user_query,
            "status": step_status,
            "result": result.get("output", "")  # 冗余结果到 Plan
        }]
        
        # 发送计划完成事件
        await emit_plan_complete(session_id, has_error=has_error)
        
        return {
            "execution_results": [{
                "step_id": step_id,
                "result": result.get("output", ""),
                "worker": worker_type,
                "meta": result.get("meta", {}) or {},
            }],
            "plan_status": "error" if has_error else "completed",
            "task_plan": task_plan,
            "memory_dfs": result.get("memory_update", {}),
            "is_direct_execution": True,  # 关键标记：供 Synthesizer 感知
            **readiness_state_patch,
        }
        
    except Exception as e:
        logger.error(f"[DirectExecute] 执行失败: {e}", exc_info=True)
        await emit_step_update(session_id, step_id, "error", str(e))
        return {
            "error": str(e),
            "plan_status": "error",
            "task_plan": [{
                "step_id": step_id,
                "worker": worker_type,
                "description": user_query,
                "status": "failed"
            }],
            "is_direct_execution": True,
            **readiness_state_patch,
        }


async def _execute_worker(
    worker_type: str,
    query: str,
    user_context: dict,
    session_id: str,
    parent_step_id: str,
    memory_dfs: dict,
    round_index: int,
    messages: list = [],  # 对话历史，用于 RAG 查询改写
    summary: str = "",
    current_focus_result: dict | None = None,
    execution_results: list | None = None,
) -> Dict[str, Any]:
    """
    调用对应 Worker
    
    统一返回格式: {"output": str, "memory_update": dict}
    """
    settings = get_settings()
    terminal_timeout_sec = int(
        max(1, getattr(settings.supervisor, "terminal_worker_timeout_sec", 300) or 300)
    )
    ctx = UserContext(**user_context) if user_context else None
    
    if worker_type == "doc_worker":
        # [修复] 使用 run_doc_task 而非 skill.execute，确保 CITATION 标记被生成
        # 之前直接调用 skill.execute() 只返回 chunks，丢失了 [[CITATION:...]] 引用标记
        from app.tools.doc_tool import run_doc_task
        previous_user_query = _get_previous_user_query(messages, query)
        
        result = await run_doc_task(
            query=query,
            original_query=previous_user_query,
            user_context=user_context,  # 传递原始 dict，run_doc_task 内部会转换
            deep_search=bool(user_context.get("deep_search", False)),
            doc_scope=user_context.get("doc_scope"),
            session_id=session_id,
            parent_step_id=parent_step_id,
            round_index=round_index,
            messages=messages[-4:] if messages else []  # 只传最近 2 轮（4条消息），避免上下文过长
        )
        
        # run_doc_task 返回 WorkerResult，包含带 CITATION 标记的 output
        return {
            "output": result.output,
            "memory_update": result.artifacts or {},
            "meta": result.meta or {},
        }
        
    elif worker_type == "sql_worker":
        # SQL 查询
        worker = get_sql_worker()
        result = await worker.execute_task(
            task_description=query,
            user_id=user_context.get("user_id", ""),
            session_id=session_id,
            parent_step_id=parent_step_id,
            round_index=round_index,
            messages=messages,
            summary=summary,
            current_focus_result=current_focus_result or {},
            execution_results=execution_results or [],
        )
        output_text = worker.format_result_for_synthesizer(result)
        memory_update = result.get("artifacts", {})
        if not memory_update and result.get("data"):
            memory_update = {f"df_sql_r{round_index}": result.get("data", [])}
        meta = {
            "success": bool(result.get("success")),
            "error": result.get("error"),
            "error_type": result.get("error_type"),
            "semantic_fallback_blocked": bool(result.get("semantic_fallback_blocked")),
            "from_semantic": bool(result.get("from_semantic")),
        }
        if not result.get("success"):
            meta["stop_reason"] = result.get("error_type") or "sql_worker_failed"
        return {
            "output": output_text,
            "memory_update": memory_update,
            "meta": meta,
        }
        
    elif worker_type == "chart_worker":
        # 图表生成
        result = await asyncio.wait_for(
            run_chart_task(
                query=query,
                session_id=session_id,
                parent_step_id=parent_step_id,
                user_context=user_context,
                messages=messages,
                memory_dfs=memory_dfs,
                round_index=round_index
            ),
            timeout=terminal_timeout_sec,
        )
        return {"output": result.output, "memory_update": {}, "meta": {}}
        
    elif worker_type == "office_worker":
        # 办公文档生成
        worker = get_office_worker()
        result = await asyncio.wait_for(
            worker.execute_task(
                task_description=query,
                user_id=user_context.get("user_id", ""),
                sandbox_path=user_context.get("sandbox_path", ""),
                session_id=session_id,
                parent_step_id=parent_step_id,
                messages=messages,
                memory_dfs=memory_dfs,
                round_index=round_index,
                user_context=user_context,
            ),
            timeout=terminal_timeout_sec,
        )
        return {"output": result.get("output", "文档生成完成"), "memory_update": {}, "meta": {}}
    
    return {"output": "未知的 Worker 类型", "memory_update": {}, "meta": {}}

