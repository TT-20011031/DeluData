"""
Planner 节点主入口

LangGraph 节点入口，负责：
1. 构建首次规划上下文
2. 构建上下文和 Prompt
3. 调用 LLM
4. 分发到 first planner
"""
import logging
from app.supervisor.state import SupervisorState
from app.core.llm.async_llm import get_async_llm
from app.config import get_settings
from prompts.schemas import SUBMIT_PLAN_SCHEMA

from .utils import (
    get_planner_prompt,
    build_planner_context,
    handle_multimodal_images,
    build_llm_messages
)
from .first_planner import first_plan

logger = logging.getLogger(__name__)


async def planner_node(state: SupervisorState) -> dict:
    """
    Planner 节点：首次规划模式
    
    核心能力：
    1. 歧义检测 - 识别模糊概念
    2. 主动建议 - 预设合理默认值
    3. 可编辑步骤 - 生成用户可修改的参数步骤
    """
    # 1. 构建上下文
    if state.get("execution_mode") == "sql_plan":
        query = state.get("user_query", "")
        result = {
            "summary": f"通过SQL查询获取{query}相关数据并生成分析。",
            "steps": [
                {
                    "description": query,
                    "worker": "sql_worker",
                    "editable": True,
                    "params": {},
                }
            ],
            "need_confirm": True,
        }
        logger.info("[Planner] SQL plan mode: created draft sql_worker plan")
        return await first_plan(state, result, True, "high")

    context = build_planner_context(state)
    
    # 2. 处理多模态（先处理，确定是否 VL 模式）
    round_index = state.get("round_index", 0)
    all_images, new_asset = handle_multimodal_images(state, round_index)
    is_vl_mode = bool(all_images)
    
    # 3. 构建 Prompt（根据 VL 模式选择不同提示词）
    system_prompt = get_planner_prompt(is_vl_mode=is_vl_mode, **context)
    
    # 4. 构建消息
    user_context = state.get("user_context", {})
    messages = build_llm_messages(
        system_prompt, 
        state.get("user_query", ""),
        all_images,
        user_context
    )
    
    # 5. 调用 LLM
    try:
        settings = get_settings()
        llm = get_async_llm()
        
        if all_images:
            # VL 模型不支持 tool_choice，使用 generate_json_multimodal
            raw_result = await llm.chat(
                messages,
                model=settings.llm.planner_model,
                max_tokens=settings.llm.planner_max_tokens,
            )
            result = llm._parse_json_from_text(raw_result)
        else:
            result = await llm.generate_structured(
                messages, 
                tool_schema=SUBMIT_PLAN_SCHEMA,
                model=settings.llm.planner_model,
                max_tokens=settings.llm.planner_max_tokens,
            )
        
        # 记录 thinking 字段
        
        # 7. 提取控制变量
        # [优化] 如果 LLM 未返回 need_confirm，根据步骤数量智能判断
        steps = result.get("steps", [])
        logger.info(f"[Planner] LLM 返回: steps_count={len(steps)}, result_keys={list(result.keys())}")
        if not steps:
            logger.warning(f"[Planner] LLM 返回空 steps！完整 result: {result}")
        default_need_confirm = len(steps) > 1  # 单步骤默认不确认，多步骤默认确认
        need_confirm = result.get("need_confirm", default_need_confirm)
        tool_confidence = result.get("tool_confidence", "low")
        
        # [修复] 检查 always_confirm 配置，强制所有任务都需要确认（使用用户级配置）
        from app.models.config.user_agent_config import get_user_agent_config_async
        workspace_id = user_context.get("workspace_id", "default")
        user_id = user_context.get("user_id")
        try:
            if user_id:
                user_config = await get_user_agent_config_async(user_id, workspace_id)
                if user_config.always_confirm:
                    need_confirm = True  # 强制需要确认
        except Exception:
            pass  # 配置获取失败不影响主流程
        
        
        # 9. 分发到 first planner
        logger.info("[Planner] Step 9: 分发到 first_plan")
        plan_result = await first_plan(state, result, need_confirm, tool_confidence)
        logger.info(f"[Planner] Step 9 完成: plan_status={plan_result.get('plan_status')}, steps={len(plan_result.get('task_plan', []))}")
        
        # 10. 添加新资产（如果有）
        if new_asset:
            active_assets = list(state.get("active_assets", []))
            active_assets.append(new_asset)
            plan_result["active_assets"] = active_assets
        
        logger.info(f"[Planner] 完成，返回 plan_result")
        return plan_result
        
    except Exception as e:
        logger.error(f"Planner 生成计划失败: {e}")
        return {
            "plan_status": "error",
            "error_message": f"生成计划失败: {str(e)}"
        }



