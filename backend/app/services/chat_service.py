"""
对话服务层 (Chat Service)

职责：封装 LangGraph 状态机操作和业务逻辑
Router 不再需要知道 SupervisorState 内部结构

设计要点：
- Service 内部负责事件推送 (emit_message_end 等)
- 后台任务通过 Router 的 BackgroundTasks 触发
- 状态机流转逻辑封装在 Service 内部
"""
import uuid
import logging
import asyncio
from typing import Optional, Any
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.models.common.context import UserContext
from app.config import get_settings
from app.api.events import (
    emit_message_end,
    emit_error,
    event_queue,
    EventEnvelope,
    EventChannel,
    EventType,
)
from app.services.chat_context import merge_safe_extra_context

logger = logging.getLogger(__name__)


def _session_graph_config(
    session_id: str,
    user_context: UserContext,
    *,
    recursion_limit: Optional[int] = None,
) -> dict[str, Any]:
    """Build a fail-closed LangGraph config for an owned user session."""
    user_id = str(user_context.user_id or "").strip()
    workspace_id = str(user_context.workspace_id or "").strip()
    if not user_id or not workspace_id:
        raise ValueError("session operations require user_id and workspace_id")
    config: dict[str, Any] = {
        "configurable": {
            "thread_id": session_id,
            "user_id": user_id,
            "workspace_id": workspace_id,
        }
    }
    if recursion_limit is not None:
        config["recursion_limit"] = recursion_limit
    return config


def _resolve_reply_model_selection(settings, reply_model_key: Optional[str]) -> tuple[str, str]:
    """
    解析正常回答模型档位，只开放受控模型档位。
    """
    model_map = {
        "flash": str(
            getattr(
                settings.llm,
                "final_reply_model_flash",
                getattr(settings.llm, "final_reply_model", "qwen3.5-flash"),
            )
            or getattr(settings.llm, "final_reply_model", "qwen3.5-flash")
        ).strip(),
        "plus": str(
            getattr(settings.llm, "final_reply_model_plus", "qwen3.5-plus")
            or "qwen3.5-plus"
        ).strip(),
        "max": str(
            getattr(settings.llm, "final_reply_model_max", "qwen3.7-max")
            or "qwen3.7-max"
        ).strip(),
    }

    default_key_raw = str(
        getattr(settings.llm, "final_reply_model_default_key", "flash") or "flash"
    ).strip().lower()
    default_key = default_key_raw if default_key_raw in model_map else "flash"

    normalized_key = str(reply_model_key or "").strip().lower()
    if normalized_key not in model_map:
        normalized_key = default_key

    return normalized_key, model_map[normalized_key]


class ChatService:
    """
    对话服务
    
    封装 LangGraph Supervisor 的操作细节
    Router 层只需调用业务方法，无需了解状态机内部结构
    
    [架构优化] 已移除 _session_states 内存缓存，所有状态通过 LangGraph Checkpoint 持久化
    """
    
    async def start_new_session(
        self,
        message: str,
        user_context: UserContext,
        session_id: Optional[str] = None,
        extra_context: Optional[dict] = None,
        reply_model_key: Optional[str] = None,
        image_url: Optional[str] = None,
        image_name: Optional[str] = None,
        file_path: Optional[str] = None,
        file_name: Optional[str] = None,
        skill_id: Optional[str] = None,
        execution_mode: Optional[str] = None,  # [NEW] 直连执行模式（优先于全局配置）
        deep_search: bool = False,
        mcp_ask_evidence_top_k: Optional[int] = None,
        mcp_ask_evidence_token_budget: Optional[int] = None,
        mcp_ask_required_file_ids: Optional[list[str]] = None,
        mcp_ask_preserve_related_candidates: bool = False,
    ) -> dict:
        """
        开启新会话，生成任务计划 (Phase 1)
        
        Args:
            message: 用户问题
            user_context: 用户上下文
            session_id: 可选的会话 ID
            extra_context: 前端传来的额外上下文 (file_context 等)
            image_url: 图片 URL（多模态支持）
            image_name: 图片文件名
            file_path: 上传文件路径
            file_name: 上传文件名
            skill_id: 选中的 DeluSkill ID（严格模式）
            reply_model_key: 正常回答模型档位
            
        Returns:
            包含 session_id, plan_id, summary, steps, status, message 的字典
        """
        from app.supervisor import get_supervisor_graph
        from app.supervisor.state import SupervisorState
        
        sid = session_id or str(uuid.uuid4())
        plan_id = str(uuid.uuid4())[:12]
        
        
        # 创建沙盒路径
        settings = get_settings()
        sandbox_path = Path(settings.sandbox.base_dir) / f"session_{sid}"
        sandbox_path.mkdir(parents=True, exist_ok=True)
        resolved_reply_model_key, resolved_reply_model = _resolve_reply_model_selection(
            settings,
            reply_model_key,
        )
        
        # 构建 user_context 字典
        user_context_dict = user_context.model_dump()
        user_context_dict["sandbox_path"] = str(sandbox_path.absolute())
        user_context_dict["deep_search"] = bool(deep_search)
        
        # 合并额外上下文
        user_context_dict = merge_safe_extra_context(user_context_dict, extra_context)
        if mcp_ask_evidence_top_k is not None:
            user_context_dict["mcp_ask_evidence_top_k"] = int(mcp_ask_evidence_top_k)
        if mcp_ask_evidence_token_budget is not None:
            user_context_dict["mcp_ask_evidence_token_budget"] = int(
                mcp_ask_evidence_token_budget
            )
        if mcp_ask_required_file_ids:
            user_context_dict["mcp_ask_required_file_ids"] = [
                str(file_id)
                for file_id in mcp_ask_required_file_ids
                if str(file_id)
            ]
        if mcp_ask_preserve_related_candidates:
            user_context_dict["mcp_ask_preserve_related_candidates"] = True
        
        # 注入多模态信息
        if image_url:
            user_context_dict["image_url"] = image_url
            user_context_dict["image_name"] = image_name
        
        # 注入文件上传信息
        if file_path:
            user_context_dict["file_path"] = file_path
            user_context_dict["file_name"] = file_name
        
        try:
            graph = get_supervisor_graph()
            config = _session_graph_config(sid, user_context)
            
            # [修复] 跨轮次追问：检查 Checkpointer 是否有之前的 state
            # 如果有则保留 memory_dfs，确保追问时能            # 尝试获取之前的 state
            previous_memory_dfs = {}
            previous_focus_result = {}
            previous_round_index = 0  # [Session Round] 默认第0轮
            try:
                # 获取 latest state
                state_snapshot = await graph.aget_state(config)
                if state_snapshot and state_snapshot.values:
                    previous_memory_dfs = state_snapshot.values.get("memory_dfs", {})
                    previous_focus_result = state_snapshot.values.get("current_focus_result", {})
                    previous_round_index = state_snapshot.values.get("round_index", 0)  # 获取上一轮索引
            except Exception as e:
                pass
            
            # 构造带有 ID 的消息
            initial_msg = HumanMessage(content=message, id=str(uuid.uuid4()))
            
            # [NEW] 预加载用户级配置和解析模板 Prompt（Context 透传优化）
            from app.models.config.user_agent_config import get_user_agent_config_async
            from app.templates_config.synthesizer_templates import resolve_template_prompt
            
            workspace_id = user_context.workspace_id
            user_id = str(user_context.user_id) if user_context.user_id else None

            from app.services.workspace_readiness_service import WorkspaceReadinessService

            readiness_service = WorkspaceReadinessService()
            try:
                readiness = await readiness_service.get_workspace_readiness(
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
            except Exception as exc:
                logger.warning(
                    "[ChatService] Readiness snapshot fallback: user_id=%s workspace_id=%s error=%s",
                    user_id,
                    workspace_id,
                    exc,
                )
                readiness = WorkspaceReadinessService.readiness_from_state(
                    {},
                    default_available=False,
                )
            
            # 获取用户配置（带层级合并）
            user_agent_config = await get_user_agent_config_async(user_id, workspace_id) if user_id else None
            
            # 预解析模板 Prompt（避免在 Node 内查库）
            active_template_prompt = await resolve_template_prompt(
                template_id=user_agent_config.synthesizer_template if user_agent_config else "default",
                workspace_id=workspace_id
            )
            synthesizer_custom_prompt = user_agent_config.synthesizer_custom_prompt if user_agent_config else ""
            
            # [Phase 2] Skill 操作手册检索
            from app.services.skill_retriever import retrieve_skill_for_query, prepare_skill_state
            
            logger.info(f"[ChatService] 开始 Skill 检索: query={message[:50]}...")
            skill_id, skill_name, skill_context, skill_steps = await retrieve_skill_for_query(
                query=message,
                user_context=user_context,
                skill_id=skill_id,
            )
            logger.info(f"[ChatService] Skill 检索完成: skill_id={skill_id}, skill_name={skill_name}")
            skill_state = prepare_skill_state(skill_id, skill_name, skill_context, skill_steps)
            
            if skill_id:
                logger.info(f"[ChatService] 匹配到 Skill: {skill_name}")
            
            # 初始化状态（隐藏 SupervisorState 细节）
            requested_execution_mode = execution_mode or (
                user_agent_config.execution_mode.value if user_agent_config else "auto"
            )
            graph_execution_mode = requested_execution_mode
            if requested_execution_mode == "sql_only" and not (
                "*" in user_context.capabilities or "database:query" in user_context.capabilities
            ):
                graph_execution_mode = "sql_plan"

            initial_state: SupervisorState = {
                "user_query": message,
                "user_context": user_context_dict,
                "session_id": sid,
                "plan_id": plan_id,
                "task_plan": [],
                "plan_summary": "",
                "plan_status": "draft",
                "messages": [initial_msg],
                "execution_results": [],
                "final_answer": "",
                "error": None,
                "retry_count": 0,
                "latest_quality_signal": {},
                "route_action": "continue",
                "need_confirm": True,
                "confirmed_sql_query": False,
                "interrupt_signal": None,
                "memory_dfs": previous_memory_dfs,  # [修复] 保留跨轮次数据
                "current_focus_result": previous_focus_result,
                "thought_nodes": [],  # [修复] 新一轮清除上一轮的反思气泡
                "reasoning_traces": [],  # [新增] 安全思考摘要持久化（刷新可恢复）
                "round_index": previous_round_index + 1,  # [Session Round] 递增轮次索引
                # [NEW] Synthesizer 自定义 Prompt（Context 透传）
                "active_template_prompt": active_template_prompt,
                "synthesizer_custom_prompt": synthesizer_custom_prompt,
                # [NEW] 直连执行模式（参数优先于用户配置）
                "execution_mode": graph_execution_mode,
                "requested_execution_mode": requested_execution_mode,
                "deep_search": bool(deep_search),
                "reply_model_key": resolved_reply_model_key,
                "final_reply_model": resolved_reply_model,
                "has_db_connection": readiness.has_db,
                "has_knowledge_base": readiness.has_knowledge,
                "readiness_reasons": readiness.reasons,
                "is_direct_execution": False,  # 由 direct_execute_node 设为 True
                # [快速路径] 意图分类默认值
                "skip_planner": False,
                "intent_type": "tool_use",
                # [M3.1] Wiki-First 知识路由默认值（实际值由 knowledge_router 节点写入）
                "knowledge_path": "rag",
                "knowledge_path_reason": "",
                "knowledge_path_source": "disabled",
                "wiki_gap_signal": None,
                # [Phase 2] Skill 操作手册模式
                **skill_state,
            }
            
            # [DEBUG] 诊断直连模式
            logger.info(f"[ChatService] initial_state execution_mode={initial_state.get('execution_mode')}, param={execution_mode}")
            
            result = await graph.ainvoke(
                initial_state,
                config=_session_graph_config(sid, user_context, recursion_limit=100)
            )
            
            # 检查错误
            if result.get("error"):
                raise Exception(result["error"])
            
            # [架构优化] 状态已由 LangGraph Checkpointer 持久化，无需额外内存缓存
            
            # [快速路径] 闲聊/直接回答 - 无 task_plan 但有 final_answer
            task_plan = result.get("task_plan", [])
            final_answer = result.get("final_answer", "")
            if not task_plan and final_answer:
                logger.info(f"[ChatService] 快速路径完成 (intent={result.get('intent_type', 'unknown')})")
                return {
                    "session_id": sid,
                    "plan_id": plan_id,
                    "summary": "",
                    "steps": [],
                    "status": "completed",
                    "message": final_answer,
                    "need_confirm": False,
                    "selected_skill_name": result.get("selected_skill_name"),
                }
            
            # [直连模式] 已完成执行，直接返回结果
            is_direct = result.get("is_direct_execution", False)
            if is_direct:
                logger.info(f"[ChatService] 直连模式完成，直接返回结果")
                return {
                    "session_id": sid,
                    "plan_id": plan_id,
                    "summary": result.get("plan_summary", ""),
                    "steps": result.get("task_plan", []),
                    "status": "completed",  # 直连模式已完成
                    "message": result.get("final_answer", ""),  # 返回最终答案
                    "need_confirm": False,  # 无需确认
                    "selected_skill_name": result.get("selected_skill_name"),
                }
            
            return {
                "session_id": sid,
                "plan_id": plan_id,
                "summary": result.get("plan_summary", ""),
                "steps": result.get("task_plan", []),
                "status": "draft",
                "message": result.get("plan_summary", "已为您生成任务计划，请在右侧确认。"),
                "need_confirm": result.get("need_confirm", True),  # [Auto-Confirm] 传递给 Router
                "selected_skill_name": result.get("selected_skill_name"),
            }
            
        except Exception as e:
            logger.error(f"生成计划失败: {e}")
            raise
    
    async def confirm_and_execute(
        self,
        session_id: str,
        plan_id: str,
        user_context: UserContext,
        modified_steps: Optional[list] = None,
    ) -> dict:
        """
        确认并执行计划 (Phase 2) - 后台任务
        
        此方法由 Router 的 BackgroundTasks 调用，异步执行
        内部负责事件推送 (SSE)
        
        Args:
            session_id: 会话 ID
            plan_id: 计划 ID
            modified_steps: 用户修改后的步骤（可选）
        """
        from app.supervisor import get_supervisor_graph
        
        # [安全] 初始化 round_index 默认值，确保 except 块中可访问
        round_index = 0
        
        try:
            graph = get_supervisor_graph()
            config = _session_graph_config(session_id, user_context)

            owned_state = await graph.aget_state(config)
            if not owned_state or not owned_state.values:
                raise PermissionError("session not found for current user")
            
            # 准备更新的数据
            effective_steps = modified_steps if modified_steps is not None else owned_state.values.get("task_plan", [])
            update_payload = {
                "plan_status": "confirmed",
                "confirmed_sql_query": any(
                    str(step.get("worker") or "") == "sql_worker"
                    for step in effective_steps
                    if isinstance(step, dict)
                ),
            }
            if modified_steps is not None:
                update_payload["task_plan"] = modified_steps
            
            # [关键] 使用 as_node="planner" 以上一个节点身份更新状态
            await graph.aupdate_state(config, update_payload, as_node="planner")
            
            # 获取当前状态
            current_state = await graph.aget_state(config)
            
            # [临时调试] 追踪状态更新结果
            plan_status = current_state.values.get("plan_status") if current_state else "N/A"
            next_nodes = current_state.next if current_state else []
            logger.info(f"[Auto-Confirm] 状态更新后: plan_status={plan_status}, next={next_nodes}")
            
            # [Session Round] 在执行开始时发送 PLAN_UPDATE
            from app.api.events import EventType
            
            task_plan = current_state.values.get("task_plan", []) if current_state else modified_steps or []
            round_index = current_state.values.get("round_index", 0) if current_state else 0
            
            await event_queue.publish(session_id, EventEnvelope(
                channel=EventChannel.TELEMETRY,
                type=EventType.PLAN_UPDATE,
                payload={
                    "action": "replace",
                    "steps": task_plan,
                    "status": "confirmed",
                    "round_index": round_index
                }
            ))
            
            # 恢复执行
            final_state = await graph.ainvoke(
                None,
                config=_session_graph_config(
                    session_id,
                    user_context,
                    recursion_limit=100,
                )
            )
            
            # 推送执行完成状态
            plan_status = final_state.get("plan_status", "completed")
            await event_queue.publish(session_id, EventEnvelope(
                channel=EventChannel.CONVERSATION,
                type="PLAN_STATUS",
                payload={"status": plan_status}
            ))
            return final_state
            
        except Exception as e:
            logger.error(f"执行计划失败: {e}")
            
            # 推送执行失败状态
            await event_queue.publish(session_id, EventEnvelope(
                channel=EventChannel.CONVERSATION,
                type="PLAN_STATUS",
                payload={"status": "error"}
            ))
            
            # 使用错误分析服务
            from app.services.error_analyzer import get_error_analyzer
            
            analyzer = get_error_analyzer()
            analysis = analyzer.analyze(str(e))
            user_msg = analysis.to_user_message()
            
            await emit_message_end(
                session_id=session_id,
                message_id=plan_id,
                full_content=user_msg,
                round_index=round_index  # [修复] 携带 round_index
            )
            
            await emit_error(
                session_id=session_id,
                error=analysis.raw_error,
                recoverable=False
            )
            return {
                "plan_status": "error",
                "final_answer": user_msg,
                "error": analysis.raw_error,
            }
    
    async def resume_session(
        self,
        session_id: str,
        plan_id: str,
        user_input: str,
        user_context: UserContext,
    ) -> None:
        """
        恢复挂起的会话 (Inline HITL) - 后台任务
        
        Args:
            session_id: 会话 ID
            plan_id: 计划 ID
            user_input: 用户补充的信息
        """
        from app.supervisor import get_supervisor_graph
        
        
        try:
            graph = get_supervisor_graph()
            config = _session_graph_config(session_id, user_context)

            current_state = await graph.aget_state(config)
            values = current_state.values if current_state else {}
            if not values:
                raise PermissionError("session not found for current user")
            if values.get("interrupt_signal"):
                from app.supervisor.nodes.common import build_interrupt_resume_updates

                update_payload = build_interrupt_resume_updates(values, user_input)
                await graph.aupdate_state(config, update_payload, as_node="suspend")
                resumed_state = await graph.aget_state(config)
                resumed_values = resumed_state.values if resumed_state else {}
                resumed_task_plan = resumed_values.get("task_plan", [])
                if resumed_task_plan:
                    await event_queue.publish(session_id, EventEnvelope(
                        channel=EventChannel.TELEMETRY,
                        type=EventType.PLAN_UPDATE,
                        payload={
                            "action": "replace",
                            "steps": resumed_task_plan,
                            "status": "executing",
                            "round_index": resumed_values.get("round_index", 0),
                        }
                    ))
                final_state = await graph.ainvoke(
                    None,
                    config=_session_graph_config(
                        session_id,
                        user_context,
                        recursion_limit=100,
                    )
                )
            else:
                # Backward compatibility for sessions suspended by native LangGraph interrupts.
                await graph.aupdate_state(config, {"interrupt_emitted": True})
                final_state = await graph.ainvoke(
                    Command(resume=user_input),
                    config=_session_graph_config(
                        session_id,
                        user_context,
                        recursion_limit=100,
                    )
                )
            
            plan_status = final_state.get("plan_status", "completed")
            task_plan = final_state.get("task_plan", [])
            plan_summary = final_state.get("plan_summary", "")
            state_plan_id = final_state.get("plan_id") or plan_id
            
            
            # 如果是 draft 状态，发送 PLAN_UPDATE
            if plan_status == "draft" and task_plan:
                await event_queue.publish(session_id, EventEnvelope(
                    channel=EventChannel.CONVERSATION,
                    type="plan_complete",
                    payload={
                        "session_id": session_id,
                        "plan_id": state_plan_id,
                        "summary": plan_summary,
                        "steps": task_plan,
                        "status": "draft"
                    }
                ))
            
            # 推送状态更新
            await event_queue.publish(session_id, EventEnvelope(
                channel=EventChannel.CONVERSATION,
                type="PLAN_STATUS",
                payload={"status": plan_status}
            ))
            
        except Exception as e:
            logger.error(f"Resume 失败: {e}")
            await emit_error(
                session_id=session_id,
                error=str(e),
                recoverable=False
            )
    
    async def get_session_plan(self, session_id: str, user_context: UserContext) -> dict:
        """
        获取会话的任务计划
        
        从 Checkpoint 恢复，支持刷新/切换后恢复任务面板和产物
        
        Args:
            session_id: 会话 ID
            
        Returns:
            包含 session_id, plan_id, summary, steps, status, thought_nodes, 
            execution_results, round_index 的字典
        """
        from app.supervisor import get_supervisor_graph
        
        try:
            graph = get_supervisor_graph()
            config = _session_graph_config(session_id, user_context)
            state_snapshot = await graph.aget_state(config)
            
            if not state_snapshot or not state_snapshot.values:
                return {
                    "session_id": session_id,
                    "plan_id": "",
                    "summary": "",
                    "steps": [],
                    "status": "none",
                    "selected_skill_name": None,
                    "execution_results": [],
                    "round_index": 0,
                }
            
            values = state_snapshot.values
            return {
                "session_id": session_id,
                "plan_id": values.get("plan_id", ""),
                "summary": values.get("plan_summary", ""),
                "steps": values.get("task_plan", []),
                "status": values.get("plan_status", "completed"),
                "selected_skill_name": values.get("selected_skill_name"),
                "thought_nodes": values.get("thought_nodes", []),
                # [FIX] 返回执行结果以支持 artifact 恢复
                "execution_results": values.get("execution_results", []),
                "round_index": values.get("round_index", 0),
            }
            
        except Exception as e:
            logger.warning(f"获取会话计划失败 {session_id}: {e}")
            return {
                "session_id": session_id,
                "plan_id": "",
                "summary": "",
                "steps": [],
                "status": "error",
                "selected_skill_name": None,
                "execution_results": [],
                "round_index": 0,
            }
    
    async def get_session_history(self, session_id: str, user_context: UserContext) -> dict:
        """
        获取会话历史消息
        
        Args:
            session_id: 会话 ID
            
        Returns:
            包含 session_id, messages, created_at, last_active 的字典
        """
        from app.core.db.checkpointer import MySQLSaver
        from langchain_core.messages import AIMessage, SystemMessage, ToolMessage, HumanMessage
        from datetime import datetime
        
        try:
            saver = MySQLSaver()
            config = _session_graph_config(session_id, user_context)
            checkpoint_tuple = await saver.aget_tuple(config)
            
            if not checkpoint_tuple:
                return {
                    "session_id": session_id,
                    "messages": [],
                    "created_at": None,
                    "last_active": None
                }
            
            checkpoint = checkpoint_tuple.checkpoint
            metadata = checkpoint_tuple.metadata or {}
            
            # 提取消息
            channel_values = checkpoint.get("channel_values", {})
            messages = channel_values.get("messages", [])
            reasoning_traces = channel_values.get("reasoning_traces", [])

            reasoning_by_message_id: dict[str, dict[str, Any]] = {}
            if isinstance(reasoning_traces, list):
                for trace in reasoning_traces:
                    if not isinstance(trace, dict):
                        continue
                    trace_message_id = str(trace.get("message_id") or "").strip()
                    trace_content = str(trace.get("content") or "").strip()
                    if not trace_message_id or not trace_content:
                        continue
                    duration_raw = trace.get("duration_ms", 0)
                    try:
                        duration_ms = max(0, int(duration_raw))
                    except (TypeError, ValueError):
                        duration_ms = 0
                    reasoning_by_message_id[trace_message_id] = {
                        "thinkingContent": trace_content,
                        "thinkingDurationMs": duration_ms,
                        "isThinkingDone": True,
                    }
            
            history = []
            for msg in messages:
                role = "user"
                if isinstance(msg, AIMessage):
                    role = "assistant"
                elif isinstance(msg, SystemMessage):
                    role = "system"
                elif isinstance(msg, ToolMessage):
                    continue  # 隐藏工具调用细节
                elif isinstance(msg, HumanMessage):
                    role = "user"
                else:
                    continue

                message_id = str(getattr(msg, "id", None) or str(uuid.uuid4()))
                message_payload: dict[str, Any] = {
                    "id": message_id,
                    "role": role,
                    "content": msg.content,
                    "timestamp": datetime.now().isoformat()
                }
                if role == "assistant" and message_id in reasoning_by_message_id:
                    message_payload.update(reasoning_by_message_id[message_id])

                history.append(message_payload)
            
            return {
                "session_id": session_id,
                "messages": history,
                "created_at": metadata.get("created_at"),
                "last_active": None
            }
            
        except Exception as e:
            logger.error(f"获取会话历史失败 {session_id}: {e}")
            raise
    
    async def validate_session_exists(
        self,
        session_id: str,
        user_context: UserContext,
    ) -> bool:
        """
        验证会话是否存在
        
        Args:
            session_id: 会话 ID
            
        Returns:
            是否存在
        """
        from app.supervisor import get_supervisor_graph
        
        try:
            graph = get_supervisor_graph()
            config = _session_graph_config(session_id, user_context)
            state_snapshot = await graph.aget_state(config)
            return state_snapshot is not None and bool(state_snapshot.values)
        except Exception:
            return False
