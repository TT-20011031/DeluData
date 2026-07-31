"""
Supervisor 路由函数 (Edges)

包含所有条件路由函数，决定图的流转方向
"""
import logging

from app.supervisor.state import SupervisorState

logger = logging.getLogger(__name__)


def route_after_intent(state: SupervisorState) -> str:
    """
    意图分类后的路由

    路由优先级：
    1. 直连模式（execution_mode != auto）-> direct_execute
    2. chitchat/direct_answer（skip_planner=True）-> synthesizer
    3. tool_use（默认）-> knowledge_router（M3.1 起插入；其内部决定走 wiki/rag/both）
    """
    execution_mode = state.get("execution_mode", "auto")
    if execution_mode in ["rag_only", "sql_only", "chart_only", "office_only"]:
        logger.info("⚡ [DirectMode] 直连模式 -> direct_execute (%s)", execution_mode)
        return "direct_execute"

    if state.get("skip_planner"):
        logger.info("⚡ [FastPath] 跳过 Planner -> Synthesizer")
        return "synthesizer"

    return "knowledge_router"


def should_continue_after_plan(state: SupervisorState) -> str:
    """
    Planner 后的路由

    注意：need_confirm=False 的自动确认逻辑在 Service 层处理。
    """
    task_plan = state.get("task_plan", [])
    logger.info(
        "[Route] should_continue_after_plan: plan_status=%s, error=%s, interrupt=%s, steps=%s",
        state.get("plan_status"),
        state.get("error"),
        bool(state.get("interrupt_signal")),
        len(task_plan),
    )

    if state.get("error"):
        return "end"

    if state.get("interrupt_signal"):
        return "suspend"

    if not task_plan:
        return "synthesizer"

    if state.get("plan_status") == "confirmed":
        return "executor"

    return "human_review"


def should_continue_after_review(state: SupervisorState) -> str:
    """Human Review 后的路由"""
    plan_status = state.get("plan_status", "draft")
    if plan_status == "confirmed":
        return "executor"
    return "end"


def route_after_executor(state: SupervisorState) -> str:
    """
    Executor 后的路由

    新架构固定进入 RouterAgent 统一决策，避免 Worker 直控全局流转。
    """
    if state.get("interrupt_signal"):
        return "suspend"

    if state.get("last_executed_step"):
        return "router_agent"

    task_plan = state.get("task_plan", [])
    # [修复] 只有 "pending" 状态才需要 Executor 继续执行
    # "waiting" 状态的终结类任务由 Synthesizer 后台执行，不应回到 Executor
    has_pending = any(s.get("status") == "pending" for s in task_plan)
    if has_pending:
        return "executor"
    return "synthesizer"


def route_after_router_agent(state: SupervisorState) -> str:
    """
    RouterAgent 后的路由

    Router 只输出 route_action，边函数负责节点跳转。
    """
    if state.get("interrupt_signal"):
        return "suspend"

    action = state.get("route_action", "finish")
    if action in ("continue", "retry_self", "switch_worker"):
        return "executor"
    return "synthesizer"


def route_after_resume(state: SupervisorState) -> str:
    """
    Resume 后的路由函数

    plan_status=confirmed 代表热修补继续执行，否则回 Planner。
    """
    if state.get("plan_status") == "suspended":
        return "end"
    if state.get("plan_status") == "confirmed":
        return "executor"
    return "planner"
