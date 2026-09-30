"""
RouterAgent 节点

职责：
- 读取 Worker 执行后的质量信号
- 根据固定路由矩阵输出下一步动作
- 在需要时仅修补当前失败步骤（重试/切换）并回投 Executor
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, Optional

from app.api.events import emit_ai_thought
from app.config import get_settings
from app.core.llm.async_llm import get_async_llm
from app.supervisor.state import SupervisorState

logger = logging.getLogger(__name__)


RETRY_REASON_CODES = {"syntax_error", "sql_syntax_error", "timeout", "transient_error"}
SWITCH_REASON_CODES = {"permission_denied", "sql_permission_denied", "sql_source_unavailable"}
AUTO_SWITCH_ON_EMPTY_CODES = {"sql_zero_rows", "rag_empty", "rag_low_relevance"}
KNOWLEDGE_EMPTY_REASON_CODES = {"rag_empty", "wiki_missing", "no_final_context"}
KNOWLEDGE_QUERY_HINTS = (
    "介绍", "是什么", "什么是", "流程", "策略", "原理", "文档", "依据", "知识", "说明", "解释",
)
REPAIR_ACTIONS = {"retry_self", "switch_worker"}

ACTION_CN = {
    "continue": "继续执行下一步",
    "retry_self": "修复后重试当前步骤",
    "switch_worker": "切换工具修复当前步骤",
    "finish": "准备输出最终答案",
}

REASON_CN = {
    "ok": "结果正常",
    "sql_ok": "SQL 返回有效结果",
    "rag_ok": "检索到有效证据",
    "rag_repaired": "修复检索后找到内容",
    "rag_repaired_trimmed": "修复检索后证据较多，已裁剪",
    "rag_budget_trimmed": "证据较多，已按预算精简",
    "rag_empty": "没有检索到相关证据",
    "rag_low_relevance": "检索结果相关度偏低",
    "wiki_missing": "正式 Wiki 未命中",
    "no_final_context": "没有可用于回答的最终上下文",
    "sql_zero_rows": "SQL 返回 0 行",
    "sql_permission_denied": "SQL 权限不足",
    "permission_denied": "权限不足",
    "sql_syntax_error": "SQL 语法错误",
    "syntax_error": "语法错误",
    "timeout": "执行超时",
    "retry_limit_reached": "已达到最大重试次数",
    "retry_target_missing": "当前失败步骤丢失，无法继续修复",
}


def _default_quality_signal(last_step: Dict) -> Dict:
    if (last_step or {}).get("error"):
        return {
            "verdict": "fail",
            "reason_code": "worker_exception",
            "confidence": 0.5,
            "retryable": True,
        }
    return {
        "verdict": "pass",
        "reason_code": "ok",
        "confidence": 0.7,
        "retryable": False,
    }


def _extract_latest_quality_signal(state: SupervisorState) -> Dict:
    signal = state.get("latest_quality_signal") or {}
    if signal:
        return signal

    execution_results = state.get("execution_results", [])
    if execution_results:
        latest = execution_results[-1]
        embedded = latest.get("quality_signal")
        if isinstance(embedded, dict) and embedded:
            return embedded

    return _default_quality_signal(state.get("last_executed_step") or {})


def _has_pending_steps(task_plan: list) -> bool:
    # [修复] 只有 "pending" 才算未完成，"waiting" 是终结类后台任务由 Synthesizer 处理
    return any(step.get("status") == "pending" for step in task_plan)


def _find_step(task_plan: list, step_id: str) -> Optional[Dict]:
    for step in task_plan:
        if step.get("step_id") == step_id:
            return step
    return None


def _choose_switch_target(current_worker: str, reason_code: str) -> str:
    if current_worker == "doc_worker" and reason_code in KNOWLEDGE_EMPTY_REASON_CODES:
        return ""
    if reason_code in SWITCH_REASON_CODES:
        return "doc_worker"
    if current_worker == "sql_worker":
        return "doc_worker"
    if current_worker == "doc_worker":
        return "sql_worker"
    return ""


def _action_to_thought_verdict(action: str) -> str:
    """
    将路由动作映射到前端 ThoughtNode 支持的 verdict 枚举。
    """
    if action in {"continue", "finish"}:
        return "pass"
    if action in {"retry_self", "switch_worker"}:
        return "partial"
    return "fail"


def _reason_to_cn(reason_code: str) -> str:
    if reason_code.startswith("switch_unavailable:"):
        fallback_reason = reason_code.split(":", 1)[1]
        return f"无法切换工具（{_reason_to_cn(fallback_reason)}）"
    return REASON_CN.get(reason_code, reason_code.replace("_", " "))


def _strip_router_repair_prefix(description: str) -> str:
    cleaned = str(description or "").strip()
    for prefix in ("[Router修复]", "[Router修复-切换工具]"):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix):].lstrip("：: ").strip()
    return cleaned


def _build_repair_description(
    *,
    original_description: str,
    reason_code: str,
    action: str,
    target_worker: str = "",
) -> str:
    base = _strip_router_repair_prefix(original_description) or "继续完成当前步骤"
    reason_cn = _reason_to_cn(reason_code)
    if action == "switch_worker" and target_worker:
        return f"[Router修复-切换工具] {reason_cn}，改用 {target_worker}：{base}"
    return f"[Router修复] {reason_cn}，修复后重试：{base}"


def _mark_step_for_retry(
    *,
    step: Dict,
    reason_code: str,
    action: str,
    target_worker: str = "",
) -> None:
    step["description"] = _build_repair_description(
        original_description=step.get("description", ""),
        reason_code=reason_code,
        action=action,
        target_worker=target_worker,
    )
    if target_worker:
        step["worker"] = target_worker
    step["status"] = "pending"
    step["result"] = None
    step["verified"] = False

    params = dict(step.get("params") or {})
    params["_router_repair_reason"] = reason_code
    params["_router_repair_action"] = action
    try:
        current = max(0, int(params.get("_router_repair_count", 0)))
    except (TypeError, ValueError):
        current = 0
    params["_router_repair_count"] = current + 1
    if target_worker:
        params["_router_switch_target"] = target_worker
    step["params"] = params


def _build_router_thought_template(action: str, reason_code: str) -> str:
    action_cn = ACTION_CN.get(action, action)
    reason_cn = _reason_to_cn(reason_code)

    if action == "finish":
        if reason_code == "retry_limit_reached":
            return "已达到重试上限，我基于当前结果先给你可用答案。"
        return f"这一步信息已经够用，我会基于当前证据直接给你答案。"
    if action == "continue":
        return f"当前结果正常，我继续执行后续步骤。"
    if action == "retry_self":
        return f"这一步还能修，我先修复后重试一次。"
    if action == "switch_worker":
        return f"当前路径不稳（{reason_cn}），我切换工具修复这一步。"
    return f"我调整了一下执行路径：{action_cn}（原因：{reason_cn}）。"


def _normalize_llm_sentence(text: str, max_chars: int = 64) -> str:
    if not text:
        return ""
    clean = text.strip().replace("\r", " ").replace("\n", " ")
    clean = clean.strip("\"'“”‘’")
    if len(clean) > max_chars:
        clean = clean[: max_chars - 1].rstrip() + "。"
    return clean


async def _humanize_router_thought(action: str, reason_code: str) -> str:
    settings = get_settings()

    fallback = _build_router_thought_template(action, reason_code)
    if not settings.supervisor.router_thought_humanize_enabled:
        return fallback

    try:
        llm = get_async_llm()
        prompt = (
            "你是企业应用里的 AI 执行助手。\n"
            "请把下面路由决策改写成一句自然口语中文。\n"
            "要求：\n"
            "1) 第一人称“我”；\n"
            "2) 18~32 字；\n"
            "3) 不要英文动作码、不加括号；\n"
            "4) 只输出一句话。\n\n"
            f"动作: {action}\n"
            f"原因: {reason_code}\n"
            f"参考: {fallback}\n"
        )
        content = await asyncio.wait_for(
            llm.chat(
                [{"role": "user", "content": prompt}],
                model=settings.llm.router_thought_model,
                temperature=0.2,
                max_tokens=settings.supervisor.router_thought_humanize_max_tokens,
            ),
            timeout=settings.supervisor.router_thought_humanize_timeout_sec,
        )
        normalized = _normalize_llm_sentence(str(content or ""))
        return normalized or fallback
    except Exception as err:
        logger.debug("router thought humanize fallback: %s", err)
        return fallback


def _decide_action(
    *,
    verdict: str,
    reason_code: str,
    retryable: bool,
    has_pending: bool,
) -> str:
    if verdict == "pass":
        return "continue" if has_pending else "finish"
    if verdict == "stop":
        return "finish"
    if reason_code in SWITCH_REASON_CODES or reason_code in AUTO_SWITCH_ON_EMPTY_CODES:
        return "switch_worker"
    if reason_code in RETRY_REASON_CODES or retryable:
        return "retry_self"
    if verdict in {"fail", "partial"}:
        return "finish"
    return "finish"


def _looks_like_knowledge_query(query: str) -> bool:
    text = str(query or "").strip().lower()
    if not text:
        return False
    return any(hint in text for hint in KNOWLEDGE_QUERY_HINTS)


def _should_keep_repair_in_knowledge_chain(
    *,
    current_worker: str,
    reason_code: str,
    user_query: str,
) -> bool:
    if current_worker != "doc_worker":
        return False
    if reason_code in KNOWLEDGE_EMPTY_REASON_CODES:
        return True
    if reason_code == "rag_low_relevance" and _looks_like_knowledge_query(user_query):
        return True
    return False


def _should_keep_confirmed_sql_route(
    *,
    current_worker: str,
    confirmed_sql_query: bool,
) -> bool:
    """A user-confirmed SQL task must not silently change its data source to RAG."""
    return current_worker == "sql_worker" and bool(confirmed_sql_query)


def _normalize_retry_budget(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


async def _emit_router_thought(
    *,
    session_id: str,
    round_index: int,
    action: str,
    reason_code: str,
    target_step_id: str,
) -> Dict:
    thought_id = str(uuid.uuid4())
    thought_verdict = _action_to_thought_verdict(action)
    message = await _humanize_router_thought(action, reason_code)
    if session_id:
        await emit_ai_thought(
            session_id=session_id,
            thought=message,
            thought_id=thought_id,
            verdict=thought_verdict,
            round_index=round_index,
            target_step_id=target_step_id or None,
            source="router_agent",
            phase="routing",
            stop_reason=reason_code,
        )
    return {
        "id": thought_id,
        "thought": message,
        "verdict": thought_verdict,
        "round_index": round_index,
        "target_step_id": target_step_id,
        "source": "router_agent",
        "phase": "routing",
        "stop_reason": reason_code,
        "timestamp": datetime.utcnow().isoformat(),
    }


async def router_agent_node(state: SupervisorState) -> SupervisorState:
    """
    RouterAgent 主逻辑

    Worker 只负责报告“发生了什么”，Router 决定“下一步做什么”。
    """
    task_plan = list(state.get("task_plan", []))
    retry_count = max(0, int(state.get("retry_count", 0) or 0))
    max_retries = _normalize_retry_budget(state.get("_max_retries", 2))
    last_step = state.get("last_executed_step") or {}
    step_id = str(last_step.get("step_id") or "")
    current_worker = str(last_step.get("worker") or "")
    session_id = state.get("session_id", "")
    round_index = state.get("round_index", 0)
    user_query = state.get("user_query", "")

    latest_signal = dict(_extract_latest_quality_signal(state))
    verdict = str(latest_signal.get("verdict") or "pass").lower()
    reason_code = str(latest_signal.get("reason_code") or "ok").lower()
    effective_reason_code = reason_code
    retryable = bool(latest_signal.get("retryable", False))
    has_pending = _has_pending_steps(task_plan)
    settings = get_settings()

    if reason_code == "semantic_clarification_required":
        clarification = latest_signal.get("clarification") if isinstance(latest_signal.get("clarification"), dict) else {}
        raw_options = clarification.get("options") if isinstance(clarification, dict) else []
        labels: list[str] = []
        option_map: dict[str, dict] = {}
        if isinstance(raw_options, list):
            for item in raw_options:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or "").strip()
                if not label:
                    continue
                labels.append(label)
                option_map[label] = item.get("selection_patch") or {}
        latest_signal["route_action"] = "ask_clarify"
        return {
            "route_action": "ask_clarify",
            "latest_quality_signal": latest_signal,
            "interrupt_signal": {
                "message": str(clarification.get("message") or "请补充语义查询口径后继续。"),
                "options": labels,
                "target_step_id": step_id,
                "source": "router",
                "type": "semantic_clarification",
                "signal_type": "semantic_clarification",
                "payload": {
                    "clarification": clarification,
                    "option_map": option_map,
                },
            },
            "plan_status": "suspended",
        }

    if reason_code == "semantic_model_incomplete":
        latest_signal["route_action"] = "finish"
        return {
            "route_action": "finish",
            "latest_quality_signal": latest_signal,
            "plan_status": "error",
        }

    # Router 开关关闭时，启用最小兜底质检，不裸奔直出。
    if not settings.supervisor.router_agent_enabled:
        if (
            verdict in {"fail", "partial"}
            and (retryable or reason_code in RETRY_REASON_CODES)
            and retry_count < max_retries
        ):
            action = "retry_self"
        else:
            action = "finish"
    else:
        action = _decide_action(
            verdict=verdict,
            reason_code=reason_code,
            retryable=retryable,
            has_pending=has_pending,
        )

    if action == "switch_worker" and _should_keep_repair_in_knowledge_chain(
        current_worker=current_worker,
        reason_code=reason_code,
        user_query=user_query,
    ):
        # Knowledge retrieval misses should not bounce into SQL. If the signal is
        # explicitly retryable, keep retrying doc_worker; otherwise synthesize the
        # empty/partial knowledge result into a clear user-facing answer.
        action = "retry_self" if retryable else "finish"
        effective_reason_code = reason_code

    confirmed_sql_guard = _should_keep_confirmed_sql_route(
        current_worker=current_worker,
        confirmed_sql_query=bool(state.get("confirmed_sql_query", False)),
    )
    if action == "switch_worker" and confirmed_sql_guard:
        action = "finish"
        effective_reason_code = reason_code

    # 将用户/租户配置 max_retries 作为 Router 修复预算：
    # 预算耗尽后直接 finish，进入输出节点，不再继续修复。
    if action in REPAIR_ACTIONS and retry_count >= max_retries:
        action = "finish"
        effective_reason_code = "retry_limit_reached"

    repair_trace = {
        "current_worker": current_worker,
        "reason_code": reason_code,
        "route_action": action,
        "repair_reason": effective_reason_code,
        "knowledge_chain_guard": _should_keep_repair_in_knowledge_chain(
            current_worker=current_worker,
            reason_code=reason_code,
            user_query=user_query,
        ),
        "confirmed_sql_guard": confirmed_sql_guard,
        "wrong_tool_repair": bool(
            current_worker == "doc_worker"
            and reason_code in KNOWLEDGE_EMPTY_REASON_CODES
            and action == "switch_worker"
        ),
    }
    latest_signal["route_action"] = action
    latest_signal["repair_reason"] = effective_reason_code
    latest_signal["tool_repair_trace"] = [repair_trace]
    latest_signal["wrong_tool_repair"] = repair_trace["wrong_tool_repair"]

    updates: Dict = {
        "route_action": action,
        "latest_quality_signal": latest_signal,
    }

    if action in REPAIR_ACTIONS:
        step = _find_step(task_plan, step_id) if step_id else None
        if step is None:
            logger.warning("router_agent: 修复失败，未找到 step_id=%s", step_id)
            updates["route_action"] = "finish"
            effective_reason_code = "retry_target_missing"
        elif action == "switch_worker":
            target_worker = _choose_switch_target(current_worker, reason_code)
            tried_workers = set(state.get("tried_workers", []))
            if not target_worker or target_worker in tried_workers:
                updates["route_action"] = "retry_self"
                effective_reason_code = f"switch_unavailable:{reason_code}"
                _mark_step_for_retry(
                    step=step,
                    reason_code=effective_reason_code,
                    action="retry_self",
                )
            else:
                _mark_step_for_retry(
                    step=step,
                    reason_code=reason_code,
                    action="switch_worker",
                    target_worker=target_worker,
                )
                updates["tried_workers"] = list(tried_workers | {target_worker})
            updates["task_plan"] = task_plan
        else:
            _mark_step_for_retry(
                step=step,
                reason_code=reason_code,
                action="retry_self",
            )
            updates["task_plan"] = task_plan

    final_action = updates.get("route_action", action)
    repair_trace["route_action"] = final_action
    repair_trace["repair_reason"] = effective_reason_code
    repair_trace["wrong_tool_repair"] = bool(
        current_worker == "doc_worker"
        and reason_code in KNOWLEDGE_EMPTY_REASON_CODES
        and final_action == "switch_worker"
    )
    latest_signal["route_action"] = final_action
    latest_signal["repair_reason"] = effective_reason_code
    latest_signal["tool_repair_trace"] = [repair_trace]
    latest_signal["wrong_tool_repair"] = repair_trace["wrong_tool_repair"]
    updates["latest_quality_signal"] = latest_signal
    if final_action in REPAIR_ACTIONS:
        updates["retry_count"] = retry_count + 1

    if updates.get("route_action") == "finish":
        updates["plan_status"] = state.get("plan_status", "completed")

    thought_node = await _emit_router_thought(
        session_id=session_id,
        round_index=round_index,
        action=updates.get("route_action", action),
        reason_code=effective_reason_code,
        target_step_id=step_id,
    )
    updates["thought_nodes"] = [thought_node]
    return updates


# 兼容旧导出名称，避免外部引用崩溃
dynamic_router_node = router_agent_node
