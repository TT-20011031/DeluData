"""
公共节点函数

包含 human_review_node、suspend_node、summarizer_node
"""
import logging
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, RemoveMessage

from app.supervisor.state import SupervisorState
from app.supervisor.focus_result import focus_result_memory_keys, load_focus_result
from app.core.llm.async_llm import get_async_llm
from app.core.llm.prompt_manager import get_prompt
import json

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


async def human_review_node(state: SupervisorState) -> SupervisorState:
    """
    Human Review 节点：中断点，等待用户确认
    
    短路逻辑：如果 plan_status 已经是 confirmed（自动确认场景），直接返回
    """
    plan_status = state.get("plan_status", "draft")
    logger.info(f"Human Review: plan_status={plan_status}")
    
    # [Auto-Confirm] 短路逻辑：已确认则直接透传
    if plan_status == "confirmed":
        logger.info("Human Review: 状态已确认，跳过等待")
        return {"plan_status": "confirmed"}
    
    # 需要用户确认的情况，保持原状态
    return state


def build_interrupt_resume_updates(state: SupervisorState, user_input) -> dict:
    """Apply a user resume payload to the checkpointed interrupt state."""
    signal = state.get("interrupt_signal") or {}
    task_plan = state.get("task_plan", [])
    target_step_id = signal.get("target_step_id", "")
    source = signal.get("source", "unknown")
    payload = signal.get("payload") or {}
    signal_type = signal.get("type") or signal.get("signal_type") or ""

    if source == "executor" and signal_type == "template_preview" and target_step_id:
        patched_context = None
        if isinstance(user_input, dict):
            if "context" in user_input and isinstance(user_input.get("context"), dict):
                patched_context = user_input.get("context")
            else:
                patched_context = user_input
        elif isinstance(user_input, str):
            try:
                parsed = json.loads(user_input)
                if isinstance(parsed, dict):
                    patched_context = parsed.get("context") if isinstance(parsed.get("context"), dict) else parsed
            except Exception:
                patched_context = None

        new_memory = state.get("memory_dfs", {}).copy()
        if patched_context is not None:
            new_memory["template_context"] = patched_context

        for step in task_plan:
            if step.get("step_id") == target_step_id:
                step_params = step.get("params") or {}
                step_params["template_mode"] = "render"
                step["params"] = step_params
                step["status"] = "pending"
                step["result"] = None
                logger.info("interrupt resume: template preview confirmed for step %s", target_step_id)
                break

        return {
            "task_plan": task_plan,
            "memory_dfs": new_memory,
            "interrupt_signal": None,
            "interrupt_emitted": False,
            "plan_status": "confirmed",
            "user_query": state.get("user_query"),
            "latest_quality_signal": {},
        }

    if source == "router" and signal_type == "semantic_clarification" and target_step_id:
        selected_label = ""
        selected_patch = {}
        free_text = ""
        if isinstance(user_input, dict):
            selected_label = str(user_input.get("selected_option_id") or user_input.get("input") or "").strip()
            if isinstance(user_input.get("selection_patch"), dict):
                selected_patch = user_input.get("selection_patch") or {}
            free_text = str(user_input.get("free_text") or "").strip()
        else:
            selected_label = str(user_input or "").strip()
        option_map = payload.get("option_map") if isinstance(payload.get("option_map"), dict) else {}
        if not selected_patch and selected_label in option_map and isinstance(option_map.get(selected_label), dict):
            selected_patch = option_map.get(selected_label) or {}
        clarification_payload = payload.get("clarification") if isinstance(payload.get("clarification"), dict) else {}
        for step in task_plan:
            if step.get("step_id") == target_step_id:
                step_params = step.get("params") or {}
                step_params["semantic_clarification"] = {
                    "kind": clarification_payload.get("kind") or "semantic_clarification",
                    "selected_option_id": selected_label,
                    "free_text": free_text or ("" if selected_patch else selected_label),
                    "selection_patch": selected_patch,
                }
                step["params"] = step_params
                step["status"] = "pending"
                step["result"] = None
                logger.info("interrupt resume: semantic clarification patched step %s", target_step_id)
                break
        return {
            "task_plan": task_plan,
            "interrupt_signal": None,
            "interrupt_emitted": False,
            "plan_status": "confirmed",
            "user_query": state.get("user_query"),
            "route_action": "continue",
            "latest_quality_signal": {},
        }

    if source in {"executor", "router"} and target_step_id:
        for step in task_plan:
            if step.get("step_id") == target_step_id:
                step["description"] = f"{step['description']} (用户补充: {user_input})"
                step["status"] = "pending"
                step["result"] = None
                logger.info("interrupt resume: patched step %s", target_step_id)
                break

    if source == "planner":
        original_query = state.get("user_query", "")
        new_query = f"{original_query} (补充信息: {user_input})"
        logger.info("interrupt resume: planner clarification received")
        return {
            "task_plan": [],
            "execution_results": [],
            "retry_count": 0,
            "interrupt_signal": None,
            "interrupt_emitted": False,
            "plan_status": "draft",
            "user_query": new_query,
            "route_action": "continue",
            "latest_quality_signal": {},
            "error": None,
        }

    return {
        "task_plan": task_plan,
        "interrupt_signal": None,
        "interrupt_emitted": False,
        "plan_status": "confirmed",
        "user_query": state.get("user_query"),
        "latest_quality_signal": {},
    }


async def suspend_node(state: SupervisorState) -> SupervisorState:
    """
    Suspend 节点：挂起等待用户输入
    
    核心逻辑：
    1. 推送 SSE 事件通知前端
    2. 调用 interrupt() 挂起执行
    3. 恢复后清理状态并继续
    """
    from app.api.events import emit_interrupt
    
    signal = state.get("interrupt_signal")
    if not signal:
        logger.warning("suspend_node: 没有 interrupt_signal，跳过挂起")
        return {}
    
    session_id = state.get("session_id", "")
    message = signal.get("message", "请提供更多信息")
    options = signal.get("options", [])
    target_step_id = signal.get("target_step_id", "")
    source = signal.get("source", "unknown")
    payload = signal.get("payload") or {}
    signal_type = signal.get("type") or signal.get("signal_type") or ""
    
    logger.info(f"suspend_node: 挂起执行，等待用户输入 (source={source})")
    
    # [关键修复] 检查是否已经发送过 INTERRUPT
    # LangGraph interrupt() 恢复时会重新执行整个函数，需要避免重复发送
    interrupt_emitted = state.get("interrupt_emitted", False)
    
    if not interrupt_emitted:
        # [关键1] 首次进入：发送 SSE 事件，前端渲染输入框
        await emit_interrupt(
            session_id=session_id,
            message=message,
            options=options,
            target_step_id=target_step_id,
            source=source,
            payload=payload,
            signal_type=signal_type
        )
    else:
        logger.info("suspend_node: 跳过 emit_interrupt (已恢复模式)")

    return {
        "interrupt_signal": signal,
        "interrupt_emitted": True,
        "plan_status": "suspended",
        "route_action": "ask_clarify",
        "user_query": state.get("user_query"),
    }


async def summarizer_node(state: SupervisorState) -> SupervisorState:
    """
    Summarizer 节点：上下文压缩与 GC
    """
    messages = state.get("messages", [])
    current_summary = state.get("summary", "")
    
    # 阈值检查 (保留最近 N 条)
    from app.config import get_settings
    settings = get_settings()
    KEEP_COUNT = settings.app.keep_count
    if len(messages) <= KEEP_COUNT:
        return {}
        
    # 切分：旧消息 vs 新消息
    # 比如 15 条: old=9, new=6. 
    # 我们希望压缩 old，保留 new。
    old_msgs = messages[:-KEEP_COUNT]
    new_msgs = messages[-KEEP_COUNT:]
    
    logger.info(f"Summarizer: 此轮压缩 {len(old_msgs)} 条旧消息")
    
    # 调用 LLM 生成新摘要
    llm = get_async_llm()
    
    # 把 old_msgs 转为文本
    old_text = ""
    for m in old_msgs:
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        old_text += f"{role}: {m.content}\n"
    
    summarize_prompt = get_prompt("supervisor.summarizer.system", current_summary=current_summary, new_dialogue=old_text)
    
    # llm.chat 需要 messages 格式
    llm_messages = [{"role": "user", "content": summarize_prompt}]
    new_summary = await llm.chat(llm_messages)
    
    # [GC 策略] : 清理 memory_dfs
    # 简单策略：仅保留 new_msgs 和 new_summary 中提到的变量
    # 假设变量格式为 [df_xxxxx]
    memory_dfs = state.get("memory_dfs", {})
    new_memory_dfs = memory_dfs.copy()
    
    import re
    # 提取仍在使用的变量
    active_text = new_summary + "\n"
    for m in new_msgs:
        active_text += str(m.content)
    
    active_vars = set(re.findall(r'\[df_[a-zA-Z0-9_]+\]', active_text))
    # 去除括号
    active_keys = {v.strip("[]") for v in active_vars}
    active_keys.update(focus_result_memory_keys(state.get("current_focus_result", {})))
    
    # 执行清理
    keys_to_remove = []
    for key in new_memory_dfs:
        if key.startswith("df_") and key not in active_keys:
            # 也可以加时间策略，防止误删刚生成的。这里暂且只按引用清除。
            keys_to_remove.append(key)
            
    for k in keys_to_remove:
        del new_memory_dfs[k]
        logger.info(f"GC: 清除僵尸变量 {k}")
    
    # ========== [多模态 GC] 清理过期图片资产 ==========
    active_assets = state.get("active_assets", [])
    current_round = state.get("round_index", 0)
    
    # 策略：round_index - last_mentioned > 10 的资产移除
    MAX_IDLE_ROUNDS = 10
    kept_assets = []
    for asset in active_assets:
        idle_rounds = current_round - asset.get("last_mentioned", 0)
        if idle_rounds <= MAX_IDLE_ROUNDS:
            kept_assets.append(asset)
        else:
            logger.info(f"GC: 清除过期图片资产 {asset.get('id')} (idle {idle_rounds} rounds)")

    # [物理删除] 构造 RemoveMessage
    remove_msgs = []
    for m in old_msgs:
        if m.id:
            remove_msgs.append(RemoveMessage(id=m.id))
    
    # [关键] 使用 replace 模式清理过期资产
    return {
        "summary": new_summary,
        "messages": remove_msgs,
        "memory_dfs": new_memory_dfs,
        "current_focus_result": state.get("current_focus_result", {}) if load_focus_result(state.get("current_focus_result", {})) else {},
        "active_assets": {"_mode": "replace", "data": kept_assets}  # GC 清理
    }
