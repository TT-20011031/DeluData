"""
Executor 节点
执行确认后的任务计划，调用各 Worker 完成具体任务
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional
from langchain_core.messages import BaseMessage
from app.supervisor.state import SupervisorState
from app.config import get_settings
from app.api.events import emit_step_update, emit_plan_complete
from app.services.doc_scope import resolve_effective_doc_scope
from app.supervisor.nodes.readiness_guard import (
    build_readiness_state_patch,
    resolve_runtime_readiness,
)
from app.services.workspace_readiness_service import WorkspaceReadinessService
logger = logging.getLogger(__name__)


def _infer_reason_code(worker: str, result_text: str, has_error: bool = False) -> str:
    """从执行结果文本推断 reason_code（兼容旧 Worker 无质量信号场景）。"""
    text = (result_text or "").lower()
    if has_error:
        if "权限" in result_text or "permission" in text:
            return "sql_permission_denied" if worker == "sql_worker" else "permission_denied"
        if "syntax" in text or "语法" in result_text:
            return "sql_syntax_error" if worker == "sql_worker" else "syntax_error"
        if "timeout" in text or "超时" in result_text:
            return "timeout"
        return "worker_exception"

    if worker == "sql_worker":
        if "无结果" in result_text or "0 行" in result_text or "row_count: 0" in text:
            return "sql_zero_rows"
        return "sql_ok"
    if worker == "doc_worker":
        if "未找到" in result_text or "无相关" in result_text:
            return "rag_empty"
        if "过滤" in result_text and "0" in result_text:
            return "rag_low_relevance"
        return "rag_ok"
    return "ok"


def _build_default_quality_signal(worker: str, result_text: str, has_error: bool = False) -> Dict[str, Any]:
    reason_code = _infer_reason_code(worker, result_text, has_error=has_error)
    if has_error:
        return {
            "verdict": "fail",
            "reason_code": reason_code,
            "confidence": 0.4,
            "retryable": reason_code in {"worker_exception", "timeout", "sql_syntax_error", "syntax_error"},
        }

    if reason_code in {"sql_zero_rows", "rag_empty", "rag_low_relevance"}:
        return {
            "verdict": "partial",
            "reason_code": reason_code,
            "confidence": 0.7,
            "retryable": False,
        }

    return {
        "verdict": "pass",
        "reason_code": reason_code,
        "confidence": 0.8,
        "retryable": False,
    }


def _build_readiness_block_signal(reason_code: str) -> Dict[str, Any]:
    return {
        "verdict": "fail",
        "reason_code": reason_code or "readiness_blocked",
        "confidence": 0.95,
        "retryable": False,
    }


def _resolve_step_status(meta: Dict[str, Any]) -> str:
    """
    Resolve final step status from worker meta.
    Supported custom states are intentionally narrow to keep routing stable.
    """
    raw = str((meta or {}).get("step_status", "completed")).strip().lower()
    if raw in {"completed", "invalid_data"}:
        return raw
    return "completed"


def _build_step_update_label(step_status: str, description: str) -> str:
    prefix = "数据无效，已跳过" if step_status == "invalid_data" else "已完成"
    return f"{prefix}: {description[:30]}..."


def _apply_doc_scope_to_params(step_params: dict, user_context: dict) -> tuple[dict, dict]:
    """
    将步骤级/会话级 doc_scope 合并为最终执行参数。
    优先级：步骤级 > 会话级。
    """
    params = dict(step_params or {})
    session_scope = (user_context or {}).get("doc_scope") if isinstance(user_context, dict) else None
    effective_scope, scope_summary = resolve_effective_doc_scope(
        params.get("doc_scope"),
        session_scope,
    )
    if effective_scope is None:
        params.pop("doc_scope", None)
    else:
        params["doc_scope"] = effective_scope
    return params, {
        "scope_source": scope_summary.source,
        "folder_count": scope_summary.folder_count,
        "file_count": scope_summary.file_count,
        "include_subfolders": scope_summary.include_subfolders,
    }


def _apply_knowledge_path_to_params(step_params: dict, knowledge_path: str) -> dict:
    """
    将 KnowledgeRouter 的判定结果注入 doc_worker 步骤参数（M3.3）。

    优先级：步骤级显式 knowledge_path > KnowledgeRouter 判定 > 默认 rag。
    步骤级允许 Planner 在特殊场景下覆盖（保留扩展空间）。
    """
    params = dict(step_params or {})
    if params.get("knowledge_path"):
        return params
    if knowledge_path and knowledge_path in ("wiki", "rag", "both"):
        params["knowledge_path"] = knowledge_path
    return params


async def _record_wiki_route_metrics(
    *,
    results: List[Dict[str, Any]],
    state: SupervisorState,
    workspace_id: Optional[str],
    session_id: Optional[str],
    user_id: Optional[str],
) -> None:
    """[M3.5] 把本轮所有 doc_worker 的 meta 落库为 wiki_route_metrics 记录。

    - 仅处理 worker == 'doc_worker' 的 result
    - 写失败仅 log，不影响主流程
    - 路由判定字段统一从 SupervisorState 读取（KnowledgeRouter 已写入）
    """
    if not workspace_id or not results:
        return

    doc_results = [r for r in results if r.get("worker") == "doc_worker"]
    if not doc_results:
        return

    try:
        from app.services.wiki_metrics_service import get_wiki_metrics_service
    except Exception:  # noqa: BLE001
        return

    service = get_wiki_metrics_service()
    user_query = state.get("user_query", "") or ""
    knowledge_path_state = state.get("knowledge_path") or "rag"
    knowledge_path_source = state.get("knowledge_path_source")
    knowledge_path_reason = state.get("knowledge_path_reason")

    for r in doc_results:
        meta = r.get("meta") or {}
        wiki_gap = meta.get("wiki_gap_signal") or {}
        path = str(meta.get("knowledge_path") or knowledge_path_state or "rag")
        quality_signal = r.get("quality_signal") or {}
        issues = list(meta.get("issues") or [])
        final_context = list(meta.get("final_context") or [])
        extra_meta = {
            "answer_context_source": meta.get("answer_context_source") or "empty",
            "issues": issues[:20],
            "wiki_fallback_to_rag": bool(meta.get("wiki_fallback_to_rag")),
            "rag_chunks_used": int(meta.get("rag_chunks_used") or 0),
            "wiki_chunks_used": int(meta.get("wiki_chunks_used") or 0),
            "wiki_hit_but_not_used": "wiki_hit_but_not_used" in issues,
            "final_context": [
                {
                    "source": item.get("source"),
                    "title": item.get("title"),
                    "slug": item.get("slug"),
                    "file_name": item.get("file_name"),
                    "page_number": item.get("page_number"),
                    "preview": str(item.get("preview") or "")[:220],
                }
                for item in final_context[:10]
                if isinstance(item, dict)
            ],
            "tool_repair_trace": quality_signal.get("tool_repair_trace") or [],
            "wrong_tool_repair": bool(quality_signal.get("wrong_tool_repair", False)),
            "route_action": quality_signal.get("route_action"),
            "repair_reason": quality_signal.get("repair_reason") or quality_signal.get("reason_code"),
        }
        await service.record_route_metric(
            workspace_id=workspace_id,
            knowledge_path=path,
            knowledge_path_source=knowledge_path_source,
            knowledge_path_reason=knowledge_path_reason,
            session_id=session_id,
            message_id=str(r.get("step_id") or "") or None,
            user_id=user_id,
            chunks_used=int(meta.get("chunks_used") or 0),
            wiki_chunks_count=int(meta.get("wiki_chunks_count") or 0),
            wiki_chunks_used=int(meta.get("wiki_chunks_used") or 0),
            has_wiki_gap=bool(wiki_gap),
            wiki_gap_task_id=(wiki_gap.get("task_id") if isinstance(wiki_gap, dict) else None),
            latency_ms=int(meta.get("latency_ms") or 0),
            stop_reason=str(meta.get("stop_reason") or "") or None,
            user_query=user_query,
            extra_meta=extra_meta,
        )


async def _enqueue_wiki_gap_task(
    *,
    wiki_gap_signal: Optional[Dict[str, Any]],
    workspace_id: Optional[str],
    user_id: Optional[str],
    session_id: Optional[str],
    user_query: Optional[str],
) -> Optional[str]:
    """[M4.1] 检测到 Wiki Gap 信号时自动入队一个 reflection 编译任务。

    设计要点：
    - fire-and-forget 语义；任何异常都仅 log，不影响主流程
    - 依赖 WikiCompileQueueService 自带的 (workspace, trigger_type='reflection') 去重，
      同一工作区有 pending/running 任务时会自动合并 file_ids
    - payload 写入 missing 列表 + 上下文，便于 worker 决策与审计

    Returns:
        新入队 / 已存在的 task_id；任何异常返回 None。
    """
    if not workspace_id:
        return None
    if not isinstance(wiki_gap_signal, dict):
        return None
    missing = wiki_gap_signal.get("missing") or wiki_gap_signal.get("missing_topics") or []
    if not isinstance(missing, list):
        missing = []
    # 清洗：仅保留非空字符串，最多 20 项
    missing_clean = [
        str(item).strip() for item in missing
        if isinstance(item, str) and str(item).strip()
    ][:20]
    if not missing_clean:
        return None

    try:
        from app.services.wiki_compile_queue_service import get_wiki_compile_queue
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Executor] 无法导入 wiki_compile_queue: %s", exc)
        return None

    payload = {
        "missing": missing_clean,
        "trigger_source": "supervisor_wiki_gap",
        "session_id": session_id or "",
        "user_query": (user_query or "")[:200],
    }
    # 透传 task_id（如果上游 Synthesizer 已经预生成）便于关联
    upstream_task_id = wiki_gap_signal.get("task_id")
    if upstream_task_id:
        payload["upstream_task_id"] = str(upstream_task_id)

    try:
        queue = get_wiki_compile_queue()
        task = await queue.enqueue_task(
            workspace_id=workspace_id,
            trigger_type="reflection",
            user_id=user_id,
            payload=payload,
            deduplicate=True,
        )
        logger.info(
            "[Executor] Wiki Gap 自动入队 task_id=%s workspace=%s missing=%s",
            task.id, workspace_id, missing_clean[:3],
        )
        return task.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Executor] Wiki Gap 入队失败（已忽略）: %s", exc)
        return None


async def _prepare_worker_params(
    worker: str,
    step: dict,
    state: SupervisorState,
    user_context: dict,
    template_cache: dict,
    step_id: str,
) -> dict:
    """
    统一预处理 worker 参数，确保串行/并行路径行为一致。
    """
    step_params = (step.get("params") or {}).copy()
    if worker == "office_worker":
        step_params = await _sanitize_office_step_params(step, state, user_context, template_cache)
    if worker == "doc_worker":
        step_params, scope_info = _apply_doc_scope_to_params(step_params, user_context)
        # [M3.3] 注入 KnowledgeRouter 判定的检索路径（rag/wiki/both）
        knowledge_path = (state.get("knowledge_path") or "rag").strip().lower()
        step_params = _apply_knowledge_path_to_params(step_params, knowledge_path)
        logger.info(
            "[DocScope] step=%s scope_source=%s folder_count=%s file_count=%s include_subfolders=%s knowledge_path=%s",
            step_id,
            scope_info["scope_source"],
            scope_info["folder_count"],
            scope_info["file_count"],
            scope_info["include_subfolders"],
            step_params.get("knowledge_path", "rag"),
        )
    return step_params


async def _sanitize_office_step_params(
    step: dict,
    state: SupervisorState,
    user_context: dict,
    template_cache: dict
) -> dict:
    """模板不存在或越权时，清掉 template 参数并回退到 creation。"""
    params = (step.get("params") or {}).copy()
    template_id = params.get("template_id")
    template_mode = params.get("template_mode")
    if not template_id and not template_mode:
        return params

    if not template_id:
        if template_mode:
            logger.warning("[Executor] office_worker 缺少 template_id，清除 template_mode 回退 creation")
            params.pop("template_mode", None)
            step["params"] = params
        return params

    try:
        template_id_int = int(template_id)
    except (TypeError, ValueError):
        logger.warning(f"[Executor] office_worker 非法 template_id={template_id}，回退 creation")
        params.pop("template_id", None)
        params.pop("template_mode", None)
        step["params"] = params
        return params

    # 缓存模板存在性，避免重复查询
    if template_id_int in template_cache:
        template_ok = template_cache[template_id_int]
    else:
        try:
            from app.models.config.template import get_template_async
            template = await get_template_async(template_id_int)
            template_ok = bool(template) and (
                not user_context.get("workspace_id")
                or template.workspace_id == user_context.get("workspace_id")
            )
        except Exception as e:
            logger.warning(f"[Executor] 模板检查失败: template_id={template_id_int}, error={e}")
            template_ok = False
        template_cache[template_id_int] = template_ok

    if not template_ok:
        logger.warning(f"[Executor] office_worker 模板不存在或无权限: template_id={template_id_int}，回退 creation")
        params.pop("template_id", None)
        params.pop("template_mode", None)
        step["params"] = params
        return params

    return params
async def executor_node(state: SupervisorState) -> SupervisorState:
    """
    Executor 节点：执行确认后的任务计划
    支持部分执行：跳过 status="completed" 的步骤（热修补支持）
    新增：
    - 生成 step_execution_context 供动态规划使用
    - 全局步骤计数器防止死循环
    """
    settings = get_settings()
    MAX_STEPS = settings.supervisor.max_steps  # 从配置读取，不再硬编码
    task_plan = state.get("task_plan", [])
    user_context = state.get("user_context", {})
    session_id = state.get("session_id", "")
    # [Session Round] 获取当前轮次供透传给 Worker
    round_index = state.get("round_index", 0)
    # [死循环防护] 检查步骤计数
    steps_executed_count = state.get("steps_executed_count", 0)
    if steps_executed_count >= MAX_STEPS:
        logger.warning(f"Executor: 达到最大步骤限制 ({MAX_STEPS})，强制终止")
        return {
            "plan_status": "error",
            "error": f"任务执行步骤过多 ({steps_executed_count}/{MAX_STEPS})，已强制终止以防止死循环"
        }
    # 传递消息历史给 Worker (比如 RAG 需要)
    messages = state.get("messages", [])
    results = []
    final_memory_updates = {} # 收集所有的内存更新
    # [行内累积模式] 创建本地累积副本，确保后续步骤能获取前序步骤的数据
    cumulative_memory = state.get("memory_dfs", {}).copy()
    template_cache: dict = {}
    has_error = False
    executed_steps = 0  # 本次执行的步骤数
    last_executed_step = None  # 记录最后执行的步骤信息
    latest_quality_signal = state.get("latest_quality_signal", {}) or {}
    # ========== [重试机制] 预存配置供路由边使用 ==========
    # 使用用户级配置（带层级合并）
    from app.models.config.user_agent_config import get_user_agent_config_async, SYSTEM_DEFAULTS
    from app.models.config.agent_config import get_agent_config_async
    workspace_id = user_context.get("workspace_id", "default")
    user_id = user_context.get("user_id")

    readiness_service = WorkspaceReadinessService()
    runtime_readiness = await resolve_runtime_readiness(
        state,
        default_available=False,
    )
    readiness_state_patch = build_readiness_state_patch(runtime_readiness)
    
    if user_id:
        user_config = await get_user_agent_config_async(user_id, workspace_id)
        _max_retries = user_config.max_retries
        logger.info(f"[Executor] 用户配置: user_id={user_id}, max_retries={_max_retries}")
    else:
        # Fallback: user_id 缺失时优先使用工作区配置，确保租户管理员配置生效
        workspace_config = await get_agent_config_async(workspace_id)
        if workspace_config:
            _max_retries = workspace_config.max_retries
            logger.info(f"[Executor] user_id 缺失，使用工作区配置: workspace_id={workspace_id}, max_retries={_max_retries}")
        else:
            _max_retries = SYSTEM_DEFAULTS["max_retries"]
            logger.warning(f"[Executor] user_id 缺失，使用系统默认值: max_retries={_max_retries}")

    try:
        _max_retries = max(0, int(_max_retries))
    except (TypeError, ValueError):
        _max_retries = SYSTEM_DEFAULTS["max_retries"]
    # ========== [逻辑门控] Gated Execution ==========
    # 提取类优先，终结类延后，确保数据完整后再生成文件
    from app.worker_categories import get_worker_category, WorkerCategory, is_terminal_worker, is_extraction_worker
    # 获取所有 pending 状态的任务
    pending_steps = [s for s in task_plan if s.get("status") == "pending"]
    # 按类别分类
    pending_extraction = [
        s for s in pending_steps
        if get_worker_category(s.get("worker", "")) == WorkerCategory.EXTRACTION
    ]
    _has_terminal_tasks = any(
        get_worker_category(s.get("worker", "")) == WorkerCategory.TERMINAL
        for s in task_plan
    )
    pending_terminal = [
        s for s in pending_steps
        if get_worker_category(s.get("worker", "")) == WorkerCategory.TERMINAL
    ]
    # 门控策略：
    # - 分支 A：有提取类任务 → 只执行提取类，终结类标记为 waiting
    # - 分支 B：无提取类但有终结类 → 并行执行终结类
    # [异步流式响应] 终结类任务并行执行标记
    parallel_terminal_mode = False
    if pending_extraction:
        # 将终结类任务标记为 waiting
        for s in pending_terminal:
            s["status"] = "waiting"
        steps_to_execute_ids = {s.get("step_id") for s in pending_extraction}
    elif pending_terminal:
        # [异步流式响应] 多个终结类任务使用并行模式
        if len(pending_terminal) > 1:
            parallel_terminal_mode = True
        if any(s.get("worker") == "office_worker" for s in pending_terminal):
            parallel_terminal_mode = False
        steps_to_execute_ids = {s.get("step_id") for s in pending_terminal}
    else:
        # 只有 utility 类或无任务
        steps_to_execute_ids = {s.get("step_id") for s in pending_steps}
    # ========== [异步流式响应] 并行执行终结类任务 ==========
    if parallel_terminal_mode and pending_terminal:
        import asyncio
        from app.api.events import emit_chart_status
        async def execute_terminal_task(step):
            """并行执行单个终结类任务的包装函数"""
            step_id = step.get("step_id")
            worker = step.get("worker", "")
            description = step.get("description", "")
            availability = readiness_service.check_worker_availability(worker, runtime_readiness)
            if not availability.allowed:
                step["status"] = "error"
                step["result"] = availability.message
                if session_id:
                    await emit_step_update(
                        session_id,
                        step_id,
                        "error",
                        availability.message,
                        round_index=round_index,
                    )
                return {
                    "step": step,
                    "output": None,
                    "error": availability.message,
                    "reason_code": availability.reason_code or "readiness_blocked",
                }
            # 发送开始状态
            step["status"] = "running"
            if session_id:
                await emit_step_update(session_id, step_id, "running", f"正在执行: {description[:30]}...", round_index=round_index)
                # 如果是图表任务，发送 chart_status: generating
                if worker == "chart_worker":
                    await emit_chart_status(session_id, step_id, "generating", description[:50], round_index=round_index)
            try:
                step_params = await _prepare_worker_params(
                    worker=worker,
                    step=step,
                    state=state,
                    user_context=user_context,
                    template_cache=template_cache,
                    step_id=step_id,
                )
                exec_output = await execute_worker_task(
                    worker=worker,
                    description=description,
                    user_context=user_context,
                    session_id=session_id,
                    parent_step_id=step_id,
                    user_query=state.get("user_query", ""),
                    messages=messages,
                    memory_dfs=cumulative_memory,
                    round_index=round_index,  # [Session Round] 透传轮次
                    execution_results=state.get("execution_results", []),  # [P1] 传递已执行的结果
                    summary=state.get("summary", ""),
                    current_focus_result=state.get("current_focus_result", {}),
                    step_params=step_params,
                )
                meta = exec_output.get("meta") or {}
                step_status = _resolve_step_status(meta)
                step["status"] = step_status
                step["result"] = exec_output["result"]
                step["verified"] = True
                if session_id:
                    await emit_step_update(
                        session_id,
                        step_id,
                        step_status,
                        _build_step_update_label(step_status, description),
                        result=exec_output["result"],
                        round_index=round_index,
                    )
                    if worker == "chart_worker" and step_status == "completed":
                        await emit_chart_status(session_id, step_id, "completed", description[:50], round_index=round_index)
                return {"step": step, "output": exec_output, "error": None}
            except Exception as e:
                logger.error(f"[异步流式] 终结任务 {step_id} 执行失败: {e}")
                step["status"] = "error"
                if session_id:
                    await emit_step_update(session_id, step_id, "error", str(e)[:50], round_index=round_index)
                    if worker == "chart_worker":
                        await emit_chart_status(session_id, step_id, "error", str(e)[:50], round_index=round_index)
                return {"step": step, "output": None, "error": str(e)}
        # 并行执行所有终结类任务
        parallel_results = await asyncio.gather(*[execute_terminal_task(s) for s in pending_terminal], return_exceptions=True)
        # 处理并行执行结果
        for pr in parallel_results:
            if isinstance(pr, Exception):
                has_error = True
                continue
            if pr.get("error"):
                has_error = True
                step = pr.get("step") or {}
                reason_code = pr.get("reason_code") or "readiness_blocked"
                failed_signal = _build_readiness_block_signal(reason_code)
                latest_quality_signal = failed_signal
                results.append(
                    {
                        "step_id": step.get("step_id"),
                        "worker": step.get("worker"),
                        "error": pr.get("error"),
                        "quality_signal": failed_signal,
                        "meta": {
                            "worker_round": round_index,
                            "token_used": 0,
                            "chunks_used": 0,
                            "stop_reason": reason_code,
                            "latency_ms": 0,
                        },
                    }
                )
            else:
                step = pr["step"]
                output = pr["output"]
                quality_signal = output.get("quality_signal") or _build_default_quality_signal(
                    step["worker"], output.get("result", ""), has_error=False
                )
                meta = output.get("meta") or {
                    "worker_round": round_index,
                    "token_used": 0,
                    "chunks_used": 0,
                    "stop_reason": quality_signal.get("reason_code", "ok"),
                    "latency_ms": 0,
                }
                results.append({
                    "step_id": step["step_id"],
                    "worker": step["worker"],
                    "result": output["result"],
                    "quality_signal": quality_signal,
                    "meta": meta,
                })
                latest_quality_signal = quality_signal
                # 合并内存更新
                for key, value in output.get("memory_update", {}).items():
                    prefixed_key = f"df_{step['step_id']}_{key.replace('df_', '', 1)}" if not key.startswith(f"df_{step['step_id']}_") else key
                    final_memory_updates[prefixed_key] = value
                    cumulative_memory[prefixed_key] = value
        executed_steps = len(pending_terminal)
        if session_id:
            await emit_plan_complete(session_id, has_error=has_error)
        # 跳过 for 循环，直接返回结果
        return {
            "task_plan": task_plan,
            "execution_results": results,
            "plan_status": "error" if has_error else "completed",
            "pending_artifacts": final_memory_updates,
            "steps_executed_count": steps_executed_count + executed_steps,
            "tried_workers": [s.get("worker") for s in pending_terminal],
            "last_executed_step": {"step_id": pending_terminal[-1].get("step_id"), "worker": pending_terminal[-1].get("worker"), "error": has_error},
            "latest_quality_signal": latest_quality_signal or _build_default_quality_signal(
                pending_terminal[-1].get("worker", ""),
                "parallel_terminal_error",
                has_error=has_error,
            ),
            "route_action": "continue",
            "round_index": round_index,  # [BUG FIX] 确保持久化
            "_max_retries": _max_retries,  # [重试机制] 供路由边读取
            "_has_terminal_tasks": _has_terminal_tasks,  # [重试机制] 供路由边读取
            **readiness_state_patch,
        }
    for i, step in enumerate(task_plan):
        step_id = step.get("step_id", str(i + 1))
        step_status = step.get("status", "pending")
        worker = step.get("worker", "sql_worker")
        description = step.get("description", "")
        # [热修补支持] 跳过已完成的步骤
        if step_status in {"completed", "invalid_data"}:
            # 保留已完成步骤的结果
            if step.get("result"):
                results.append({
                    "step_id": step_id,
                    "worker": worker,
                    "result": step.get("result")
                })
            continue
        # [逻辑门控] 跳过 waiting 状态或不在本轮执行列表中的步骤
        if step_status == "waiting" or step_id not in steps_to_execute_ids:
            continue
        availability = readiness_service.check_worker_availability(worker, runtime_readiness)
        if not availability.allowed:
            step["status"] = "error"
            step["result"] = availability.message
            failed_signal = _build_readiness_block_signal(
                availability.reason_code or "readiness_blocked"
            )
            latest_quality_signal = failed_signal
            results.append(
                {
                    "step_id": step_id,
                    "worker": worker,
                    "error": availability.message,
                    "quality_signal": failed_signal,
                    "meta": {
                        "worker_round": round_index,
                        "token_used": 0,
                        "chunks_used": 0,
                        "stop_reason": availability.reason_code or "readiness_blocked",
                        "latency_ms": 0,
                    },
                }
            )
            has_error = True
            last_executed_step = {
                "step_id": step_id,
                "worker": worker,
                "error": True,
            }
            if session_id:
                await emit_step_update(
                    session_id,
                    step_id,
                    "error",
                    availability.message,
                    round_index=round_index,
                )
            break
        executed_steps += 1
        # 更新步骤状态并推送 SSE
        step["status"] = "running"
        if session_id:
            await emit_step_update(session_id, step_id, "running", f"正在执行: {description[:30]}...", round_index=round_index)
        # [Pro Tips 11.2] terminal 任务执行前检查 memory_dfs
        if is_terminal_worker(worker):
            memory_dfs = state.get("memory_dfs", {})
            if not memory_dfs:
                logger.warning(f"[Gated Check] Terminal 任务 {step_id} 执行时 memory_dfs 为空，数据可能未准备好")
        try:
            step_params = await _prepare_worker_params(
                worker=worker,
                step=step,
                state=state,
                user_context=user_context,
                template_cache=template_cache,
                step_id=step_id,
            )
            # Execute Task
            exec_output = await execute_worker_task(
                worker=worker,
                description=description,
                user_context=user_context,
                session_id=session_id,
                parent_step_id=step_id,
                user_query=state.get("user_query", ""),
                messages=messages,
                memory_dfs=cumulative_memory,  # [行内累积] 使用累积数据，不是原始 state
                round_index=round_index,  # [Session Round] 透传轮次
                execution_results=state.get("execution_results", []),  # [P1] 传递已执行的结果
                summary=state.get("summary", ""),
                current_focus_result=state.get("current_focus_result", {}),
                step_params=step_params,
            )
            result = exec_output["result"]
            memory_updates = exec_output["memory_update"]
            quality_signal = exec_output.get("quality_signal") or _build_default_quality_signal(
                worker, result, has_error=False
            )
            meta = exec_output.get("meta") or {
                "worker_round": round_index,
                "token_used": 0,
                "chunks_used": 0,
                "stop_reason": quality_signal.get("reason_code", "ok"),
                "latency_ms": 0,
            }
            step_status = _resolve_step_status(meta)
            latest_quality_signal = quality_signal
            if isinstance(memory_updates, dict) and "template_preview" in memory_updates:
                preview_payload = memory_updates.get("template_preview") or {}
                step["status"] = "waiting"
                step["result"] = "等待用户确认字段预览"
                if session_id:
                    await emit_step_update(
                        session_id,
                        step_id,
                        "waiting",
                        "等待用户确认字段预览",
                        round_index=round_index
                    )
                return {
                    "task_plan": task_plan,
                    "execution_results": results,
                    "plan_status": "confirmed",
                    "interrupt_signal": {
                        "message": "请确认模板字段预览",
                        "options": [],
                        "target_step_id": step_id,
                        "source": "executor",
                        "type": "template_preview",
                        "payload": preview_payload
                    },
                    "pending_artifacts": final_memory_updates,
                    "steps_executed_count": steps_executed_count + executed_steps,
                    "tried_workers": list(set((state.get("tried_workers", []) or []) + [worker])),
                    "last_executed_step": {"step_id": step_id, "worker": worker, "error": False},
                    "latest_quality_signal": quality_signal,
                    "route_action": "ask_clarify",
                    "round_index": round_index,
                    "_max_retries": _max_retries,
                    "_has_terminal_tasks": _has_terminal_tasks,
                    **readiness_state_patch,
                }
            # Merge memory updates back to state
            # Note: In SupervisorState we need to return this update.
            # We can't modify `state` because it's input.
            # We will gather all updates and return them at the end.
            # [P2 修复] 为 memory key 添加 step_id 前缀，防止多轮覆盖
            # 原本 key: df_result -> 新 key: df_{step_id}_result
            if final_memory_updates is None: final_memory_updates = {}
            for key, value in memory_updates.items():
                # 如果 key 已经包含 step_id 前缀则跳过
                if key.startswith(f"df_{step_id}_"):
                    prefixed_key = key
                else:
                    # 去掉原有的 df_ 前缀（如有），添加新的带 step_id 的前缀
                    base_key = key.replace("df_", "", 1) if key.startswith("df_") else key
                    prefixed_key = f"df_{step_id}_{base_key}"
                final_memory_updates[prefixed_key] = value
                # [关键修复] 反向修补 result 文本，确保 Synthesizer 能找到
                # 否则 LLM 看到 "存入 df_a"，但内存里是 "df_1_a"，会导致幻觉
                if key != prefixed_key and result:
                    result = result.replace(key, prefixed_key)
                    step["result"] = result
                    # 同时更新 results 列表中的记录
                    for r in results:
                        if r.get("step_id") == step_id:
                            r["result"] = result
            # Update local list of steps for internal loop (visual only since we dump at end)
            step["status"] = step_status
            step["result"] = result
            # ========== [验证链修复] 终结类任务执行成功即视为验证通过 ==========
            if is_terminal_worker(worker):
                step["verified"] = True
            # 记录最后执行的步骤信息（用于路由判断）
            last_executed_step = {
                "step_id": step_id,
                "worker": worker,
                "error": False
            }
            results.append({
                "step_id": step_id,
                "worker": worker,
                "result": result,
                "quality_signal": quality_signal,
                "meta": meta,
            })
            # [行内累积] 同时更新到累积变量，确保后续步骤能获取
            cumulative_memory.update(final_memory_updates)  # 使用 prefixed 版本
            # 推送状态：终结类任务直接标记完成，提取类任务待审核
            if session_id:
                await emit_step_update(
                    session_id,
                    step_id,
                    step_status,
                    _build_step_update_label(step_status, description),
                    result=result,
                    round_index=round_index
                )
            # ========== [步进式执行] 提取类任务执行后立即中断，等待 Router 决策 ==========
            if is_extraction_worker(worker):
                break
        except Exception as e:
            logger.error(f"步骤 {step_id} 执行失败: {e}")
            step["status"] = "error"
            step["result"] = str(e)
            failed_signal = _build_default_quality_signal(worker, str(e), has_error=True)
            latest_quality_signal = failed_signal
            results.append({
                "step_id": step_id,
                "worker": worker,
                "error": str(e),
                "quality_signal": failed_signal,
                "meta": {
                    "worker_round": round_index,
                    "token_used": 0,
                    "chunks_used": 0,
                    "stop_reason": failed_signal.get("reason_code", "worker_exception"),
                    "latency_ms": 0,
                },
            })
            has_error = True
            # 记录失败的步骤信息
            last_executed_step = {
                "step_id": step_id,
                "worker": worker,
                "error": True
            }
            if session_id:
                await emit_step_update(session_id, step_id, "error", f"执行失败: {str(e)[:50]}...", round_index=round_index)
            break
    if session_id:
        await emit_plan_complete(session_id, has_error=has_error)
    # ========== [死循环防护] StalledError 检测 ==========
    # 如果本轮没有执行任何任务，但还有 pending 任务，说明可能存在死锁
    if executed_steps == 0 and not has_error:
        pending_count = len([s for s in task_plan if s.get("status") == "pending"])
        if pending_count > 0:
            return {
                "task_plan": task_plan,
                "plan_status": "error",
                "error": "系统检测到执行死循环（依赖关系可能存在死锁）",
                "last_executed_step": last_executed_step,
                "latest_quality_signal": latest_quality_signal,
                "round_index": round_index,  # [BUG FIX] 确保持久化
                "_max_retries": _max_retries,
                "_has_terminal_tasks": _has_terminal_tasks,
                **readiness_state_patch,
            }
    # [Ping-Pong 防护] 收集本轮执行过的所有工具类型
    executed_worker_types = list(set(
        step.get("worker") for step in task_plan
        if step.get("status") in ["completed", "error", "invalid_data"]
    ))
    current_tried = state.get("tried_workers", [])
    all_tried = list(set(current_tried + executed_worker_types))

    # [M3.4] 从本轮 worker meta 中抽取 wiki_gap_signal（取最近一个非空），暴露到 state
    extracted_wiki_gap_signal: Optional[Dict[str, Any]] = None
    for r in reversed(results):
        sig = (r.get("meta") or {}).get("wiki_gap_signal")
        if sig:
            extracted_wiki_gap_signal = sig
            break

    # [M3.5] 异步落库 wiki_route_metrics（每个 doc_worker 一行；失败软降级）
    try:
        await _record_wiki_route_metrics(
            results=results,
            state=state,
            workspace_id=workspace_id,
            session_id=session_id,
            user_id=user_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Executor] wiki_route_metrics 落库异常（已忽略）: %s", exc)

    # [M4.1] 检测到 Wiki Gap 时自动入队 reflection 编译任务
    # （fire-and-forget；service 自带 deduplicate 防风暴）
    enqueued_gap_task_id: Optional[str] = None
    if extracted_wiki_gap_signal:
        try:
            enqueued_gap_task_id = await _enqueue_wiki_gap_task(
                wiki_gap_signal=extracted_wiki_gap_signal,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                user_query=state.get("user_query"),
            )
            # 把入队 id 回写到 signal，便于前端 / 审计追踪
            if enqueued_gap_task_id and isinstance(extracted_wiki_gap_signal, dict):
                extracted_wiki_gap_signal = {
                    **extracted_wiki_gap_signal,
                    "compile_task_id": enqueued_gap_task_id,
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Executor] Wiki Gap 自动入队异常（已忽略）: %s", exc)

    # [延迟保存] 将 artifacts 暂存到 pending_artifacts，由 Synthesizer 统一合并
    return {
        "task_plan": task_plan,
        "execution_results": results,
        "plan_status": "error" if has_error else "completed",
        "pending_artifacts": final_memory_updates,
        "steps_executed_count": steps_executed_count + executed_steps,
        "tried_workers": all_tried,
        "last_executed_step": last_executed_step,
        "latest_quality_signal": latest_quality_signal,
        "route_action": "continue",
        "round_index": round_index,  # [BUG FIX] 确保持久化
        # [M3.4] 透传 wiki 缺失信号（None 时不覆盖 state）
        **({"wiki_gap_signal": extracted_wiki_gap_signal} if extracted_wiki_gap_signal else {}),
        "_max_retries": _max_retries,  # [重试机制] 供路由边读取
        "_has_terminal_tasks": _has_terminal_tasks,  # [重试机制] 供路由边读取
        **readiness_state_patch,
    }
async def execute_worker_task(
    worker: str,
    description: str,
    user_context: dict,
    session_id: str = "",
    parent_step_id: str = "",
    user_query: str = "",
    messages: List[BaseMessage] | None = None,
    memory_dfs: dict | None = None,
    round_index: int = 0,  # [Session Round] 透传执行轮次
    execution_results: list | None = None,  # [P1] 任务执行结果，用于数据源匹配
    summary: str = "",
    current_focus_result: dict | None = None,
    step_params: dict = None,  # Step-level structured params
) -> dict:
    """
    使用标准接口执行 Worker 任务
    Returns:
        dict: {"result": str, "memory_update": dict, "quality_signal": dict, "meta": dict}
    """
    from app.tools.registry import get_tool
    from app.tools.base import WorkerResult
    messages = messages or []
    memory_dfs = memory_dfs or {}
    execution_results = execution_results or []
    user_id = user_context.get("user_id", "default_user")
    if worker == "parameter_definition":
        signal = _build_default_quality_signal(worker, f"参数定义完成: {description}", has_error=False)
        return {
            "result": f"参数定义完成: {description}",
            "memory_update": {},
            "quality_signal": signal,
            "meta": {
                "worker_round": round_index,
                "token_used": 0,
                "chunks_used": 0,
                "stop_reason": signal.get("reason_code", "ok"),
                "latency_ms": 0,
            },
        }
    # finish worker 是直接回复，不需要工具调用
    if worker == "finish":
        signal = _build_default_quality_signal(worker, description, has_error=False)
        return {
            "result": description,
            "memory_update": {},
            "quality_signal": signal,
            "meta": {
                "worker_round": round_index,
                "token_used": 0,
                "chunks_used": 0,
                "stop_reason": signal.get("reason_code", "ok"),
                "latency_ms": 0,
            },
        }
    tool = get_tool(worker)
    if not tool:
        raise ValueError(f"找不到工具: {worker}")
    settings = get_settings()
    terminal_timeout_sec = int(
        max(1, getattr(settings.supervisor, "terminal_worker_timeout_sec", 300) or 300)
    )
    # 基于 BaseToolInput 的标准化参数
    ALLOWED_STEP_PARAMS = {
        "template_id",
        "template_version",
        "template_mode",
        "doc_scope",
        "output_filename",
        "knowledge_path",
        "semantic_clarification",
    }
    safe_step_params = step_params or {}
    tool_args = {
        "query": description,
        "user_id": user_id,
        "session_id": session_id,
        "parent_step_id": parent_step_id,
        "user_context": user_context,
        "messages": messages,
        # 传递副本以避免状态的原地修改
        "memory_dfs": memory_dfs.copy() if memory_dfs else {},
        # [Session Round] 透传执行轮次供 Worker SSE 使用
        "round_index": round_index,
        # [P1] 传递任务执行结果供 chart_tool/office_tool 数据源匹配
        "execution_results": execution_results,
    }
    if worker == "sql_worker":
        tool_args["summary"] = summary
        tool_args["current_focus_result"] = current_focus_result or {}
    if worker == "doc_worker":
        tool_args["original_query"] = user_query
    if safe_step_params:
        for k in ALLOWED_STEP_PARAMS:
            if k in safe_step_params:
                tool_args[k] = safe_step_params[k]
    # 执行工具
    try:
        # 我们期望工具返回 WorkerResult 对象 (或兼容的字典)
        # 注意: 如果 Tool 返回 Pydantic 模型，LangChain 'ainvoke' 可能会返回字典
        if worker in {"chart_worker", "office_worker"}:
            output = await asyncio.wait_for(tool.ainvoke(tool_args), timeout=terminal_timeout_sec)
        else:
            output = await tool.ainvoke(tool_args)
        result_text = ""
        artifacts = {}
        quality_signal = {}
        meta = {}
        if isinstance(output, WorkerResult):
            result_text = output.output
            artifacts = output.artifacts or {}
            quality_signal = output.quality_signal or {}
            meta = output.meta or {}
        elif isinstance(output, dict) and "output" in output:
             result_text = output["output"]
             artifacts = output.get("artifacts", {}) or {}
             quality_signal = output.get("quality_signal", {}) or {}
             meta = output.get("meta", {}) or {}
        else:
             # 旧版工具返回字符串的回退处理
             result_text = str(output)
             artifacts = {}
             quality_signal = {}
             meta = {}
        if not quality_signal:
            quality_signal = _build_default_quality_signal(worker, result_text, has_error=False)
        if not meta:
            meta = {
                "worker_round": round_index,
                "chunks_used": 0,
                "token_used": 0,
                "stop_reason": quality_signal.get("reason_code", "ok"),
                "latency_ms": 0,
            }
        return {
            "result": result_text,
            "memory_update": artifacts,
            "quality_signal": quality_signal,
            "meta": meta,
        }
    except asyncio.TimeoutError as e:
        logger.error(f"Worker 执行超时: worker={worker}, timeout={terminal_timeout_sec}s")
        raise TimeoutError(f"{worker} 执行超时 ({terminal_timeout_sec}s)") from e
    except Exception as e:
        logger.error(f"Worker 执行失败: {e}")
        raise e
