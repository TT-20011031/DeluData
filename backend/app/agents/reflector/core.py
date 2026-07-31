"""
[DEPRECATED] Legacy Reflector 核心逻辑（已停用）

状态说明：
- 本模块已从当前 Supervisor 主链路移除，不再参与在线任务调度。
- 当前执行链路为：intent_classifier -> planner -> executor -> router_agent -> synthesizer。
- 保留该文件仅用于历史回溯与旧实现参考，请勿在新逻辑中继续接入。
"""
import logging
from typing import Dict, Any, List

from app.api.events import emit_step_update

from .quick_check import (
    quick_check,
    get_failed_layer,
    is_creation_task,
    CREATION_WORKERS,
    EMPTY_KEYWORDS
)
from .llm_check import llm_check
from app.models.config.user_agent_config import get_user_agent_config_async, SYSTEM_DEFAULTS

logger = logging.getLogger(__name__)

# 默认熔断阈值（当配置获取失败时使用）
DEFAULT_MAX_RETRIES = 2


def _has_related_data(execution_results: List[Dict[str, Any]]) -> bool:
    """判断是否已有可用数据（首轮宽松判定）。"""
    for r in execution_results:
        if r.get("error"):
            continue
        artifacts = r.get("artifacts")
        if artifacts:
            return True
        text = str(r.get("result", "")).strip()
        if not text:
            continue
        lower = text.lower()
        if any(kw in lower for kw in EMPTY_KEYWORDS):
            continue
        return True
    return False


class Reflector:
    """
    反思/质检智能体
    
    分流策略:
    - Extraction (sql_worker, doc_worker): Quick Check + LLM Check
    - Creation (office_worker, chart_worker): Quick Check 只检查错误+文件
    """
    
    async def reflect(
        self,
        user_query: str,
        execution_results: List[Dict[str, Any]],
        retry_count: int,
        session_id: str = "",
        round_index: int = 0,
        workspace_id: str = "default",
        failed_step: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        执行反思审计
        
        Args:
            user_query: 用户原始问题
            execution_results: 执行结果列表
            retry_count: 当前重试次数
            session_id: 会话ID
            round_index: 当前轮次
            workspace_id: 工作空间ID（用于获取配置）
            failed_step: 失败的具体步骤（用于步骤级熔断）
            
        Returns:
            {
                "verdict": "pass" | "fail" | "partial" | "break",
                "feedback": "错误原因/修正建议",
                "dimension": "syntax" | "validity" | "relevance" | "permission" | None,
                "failed_layer": "extraction" | "terminal" | None,
                "step_meltdown": bool  # 是否为步骤级熔断
            }
        """
        await emit_step_update(
            session_id,
            step_id="reflector",
            status="running",
            label="正在审核执行结果..."
        )
        
        # 0. 获取用户级配置的熔断阈值（此处 workspace_id 已有，但缺少 user_id）
        # 注意: Reflector.reflect() 目前不接收 user_id，使用系统默认值
        # 实际熔断检查在 reflector/node.py 中使用用户级配置
        max_retries = SYSTEM_DEFAULTS["max_retries"]
        
        # [重要] 熔断检查移到评估后执行，确保当前轮次结果先被评估
        # 见下方 _check_meltdown() 调用
        
        # 1. 判断任务类型
        is_creation = is_creation_task(execution_results)
        
        # 3. 快速检查（所有任务都执行）
        quick_result = quick_check(execution_results, is_creation_task=is_creation)
        if quick_result:
            await emit_step_update(
                session_id,
                step_id="reflector",
                status="completed",
                label=f"审核失败: {quick_result['dimension']}"
            )
            
            # 权限错误：不重试 SQL，但可以 Failover
            if quick_result.get("no_retry") or quick_result["dimension"] == "permission":
                return {
                    "verdict": "fail",
                    "feedback": quick_result["suggestion"],
                    "dimension": "permission",
                    "sql_no_retry": True
                }
            
            # 普通失败
            failed_layer = get_failed_layer(execution_results)
            return {
                "verdict": "fail",
                "feedback": quick_result["suggestion"],
                "dimension": quick_result["dimension"],
                "failed_layer": failed_layer
            }
        
        # 4. [分流策略] Creation 任务：跳过 LLM，直接通过
        if is_creation:
            await emit_step_update(
                session_id,
                step_id="reflector",
                status="completed",
                label="审核通过（Creation）"
            )
            
            # 为所有步骤发送 completed
            for r in execution_results:
                step_id = r.get("step_id")
                if step_id:
                    await emit_step_update(
                        session_id,
                        step_id=step_id,
                        status="completed",
                        label="已完成"
                    )
            
            return {
                "verdict": "pass",
                "feedback": None,
                "dimension": None,
                "user_thought": None  # Creation 任务使用模板生成
            }

        # 4.5 [首轮宽松] 首次尝试(未重试)只要有相关数据就通过，避免过严导致重复提取
        if retry_count == 0 and _has_related_data(execution_results):
            await emit_step_update(
                session_id,
                step_id="reflector",
                status="completed",
                label="审核通过（首轮宽松）"
            )
            for r in execution_results:
                step_id = r.get("step_id")
                if step_id:
                    await emit_step_update(
                        session_id,
                        step_id=step_id,
                        status="completed",
                        label="已完成"
                    )
            return {
                "verdict": "pass",
                "feedback": None,
                "dimension": None,
                "user_thought": "已找到相关数据，进入后续生成步骤。",
                "can_skip_remaining": False
            }

        # 5. [分流策略] Extraction 任务：执行 LLM 深度检查
        try:
            llm_result = await llm_check(user_query, execution_results, round_index)
            verdict = llm_result.get('verdict', 'unknown')
            
            if llm_result.get("verdict") == "fail":
                await emit_step_update(
                    session_id,
                    step_id="reflector",
                    status="completed",
                    label=f"审核失败: {llm_result.get('dimension', 'relevance')}"
                )
                failed_layer = get_failed_layer(execution_results)
                return {
                    "verdict": "fail",
                    "feedback": llm_result.get("suggestion", "结果与问题不相关"),
                    "dimension": llm_result.get("dimension", "relevance"),
                    "failed_layer": failed_layer,
                    "user_thought": llm_result.get("user_thought")  # 传递 LLM 生成的
                }
            
            if llm_result.get("verdict") == "partial":
                # 为无错误的步骤发送 completed
                for r in execution_results:
                    step_id = r.get("step_id")
                    if step_id and not r.get("error"):
                        await emit_step_update(
                            session_id,
                            step_id=step_id,
                            status="completed",
                            label="已完成"
                        )
                return {
                    "verdict": "partial",
                    "feedback": llm_result.get("suggestion", ""),
                    "dimension": llm_result.get("dimension", "completeness"),
                    "missing_info": llm_result.get("missing_info", ""),
                    "user_thought": llm_result.get("user_thought")  # 传递 LLM 生成的
                }
            
            # 全部通过
            await emit_step_update(
                session_id,
                step_id="reflector",
                status="completed",
                label="审核通过"
            )
            
            for r in execution_results:
                step_id = r.get("step_id")
                if step_id:
                    await emit_step_update(
                        session_id,
                        step_id=step_id,
                        status="completed",
                        label="已完成"
                    )
            
            return {
                "verdict": "pass",
                "feedback": None,
                "dimension": None,
                "user_thought": llm_result.get("user_thought"),
                "can_skip_remaining": llm_result.get("can_skip_remaining", False)  # [修复] 传递智能跳过标志
            }
            
        except Exception as e:
            logger.error(f"Reflector LLM 检查失败: {e}")
            # LLM 失败时默认通过
            await emit_step_update(
                session_id,
                step_id="reflector",
                status="completed",
                label="审核完成（LLM跳过）"
            )
            return {
                "verdict": "pass",
                "feedback": None,
                "dimension": None
            }
