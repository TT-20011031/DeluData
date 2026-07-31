"""
基于工具必要性的意图分类器

判断用户请求是否需要调用工具（数据库、知识库、图表等）
- chitchat: 纯闲聊 → 跳过 Planner
- direct_answer: 基于已有上下文回答 → 跳过 Planner
- tool_use: 需要新数据/工具 → 进入 Planner
"""
import logging
from typing import Any
from app.supervisor.state import SupervisorState
from app.supervisor.focus_result import (
    FollowupIntentDecision,
    build_focus_result_section,
    build_recent_dialogue,
    load_focus_result,
)
from app.core.llm.async_llm import get_async_llm
from app.core.llm.prompt_manager import get_prompt
from app.config import get_settings
from app.models.config.user_agent_config import get_user_agent_config_async, SYSTEM_DEFAULTS

logger = logging.getLogger(__name__)

def _has_uploaded_asset(user_context: Any) -> bool:
    if not isinstance(user_context, dict):
        return False
    image_url = str(user_context.get("image_url") or "").strip()
    file_path = str(user_context.get("file_path") or "").strip()
    return bool(image_url or file_path)


def _is_source_followup_query(query: str) -> bool:
    text = str(query or "").strip().lower()
    if not text:
        return False
    source_terms = ("哪张表", "哪个表", "哪一张表", "什么表", "来源", "出自", "来自", "引用表", "数据表", "字段来源", "表来源")
    subject_terms = ("库存", "当前库存", "数据", "字段", "信息", "结果", "周转率", "出库")
    return any(term in text for term in source_terms) and any(term in text for term in subject_terms)


_INTENT_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "classify_intent",
        "description": "分类用户意图并判断是否需要进入 planner",
        "parameters": {
            "type": "object",
            "properties": {
                "intent_type": {
                    "type": "string",
                    "enum": ["chitchat", "direct_answer", "tool_use"],
                },
                "is_followup_to_existing_result": {"type": "boolean"},
                "has_sufficient_session_context": {"type": "boolean"},
                "requires_new_tool": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": [
                "intent_type",
                "is_followup_to_existing_result",
                "has_sufficient_session_context",
                "requires_new_tool",
                "reason",
            ],
            "additionalProperties": False,
        },
    },
}


async def intent_classifier_node(state: SupervisorState) -> dict:
    """
    基于工具必要性的意图分类
    
    分类逻辑：
    0. [最高优先级] 直连执行模式检测（用户显式选择）
    1. 规则匹配（零延迟）
    2. LLM 分类（快速模型，约 1-2 秒）
    3. 默认策略：不确定时选择 tool_use
    """
    # [最高优先级] 直连执行模式：用户显式选择了模式，跳过 LLM 分类
    execution_mode = state.get("execution_mode", "auto")
    logger.info(f"[IntentClassifier] execution_mode={execution_mode}, in_state={'execution_mode' in state}")
    if execution_mode == "sql_plan":
        logger.info("[IntentClassifier] SQL plan mode detected, routing to planner")
        return {"intent_type": "tool_use", "skip_planner": False}
    if execution_mode in ["rag_only", "sql_only", "chart_only", "office_only"]:
        logger.info(f"⚡ [IntentClassifier] 直连模式检测: {execution_mode}, 跳过 LLM 分类")
        # 返回特殊意图类型，让 route_after_intent 路由到 direct_execute
        return {"intent_type": "direct_execution", "skip_planner": False}
    
    query = state.get("user_query", "")
    summary = state.get("summary", "无历史")
    user_context = state.get("user_context", {})
    focus_result = load_focus_result(state.get("current_focus_result", {}))

    # 1. [强约束] 带上传资产时，必须走工具链（禁止跳过 Planner）
    if _has_uploaded_asset(user_context):
        logger.info("⚡ [IntentClassifier] 当前请求包含上传资产，优先走工具链")
        return {"intent_type": "tool_use", "skip_planner": False}

    # 2. [最高优先级] 零延迟规则匹配 - 闲聊关键词（在任何配置检查前）
    settings = get_settings()
    query_lower = query.strip().lower()
    if query_lower in settings.supervisor.chitchat_keywords_list:
        logger.info(f"⚡ [IntentClassifier] 闲聊关键词匹配: {query_lower}")
        return {"intent_type": "chitchat", "skip_planner": True}

    if focus_result is not None and _is_source_followup_query(query) and (
        focus_result.source_tables or focus_result.source_sql
    ):
        logger.info("[IntentClassifier] 数据来源追问命中焦点结果，直接回答")
        return {"intent_type": "direct_answer", "skip_planner": True}
    
    # 3. [用户偏好] 检查是否启用"始终确认"模式（使用用户级配置）
    workspace_id = user_context.get("workspace_id", "default")
    user_id = user_context.get("user_id")
    
    try:
        if user_id:
            user_config = await get_user_agent_config_async(user_id, workspace_id)
            if user_config.always_confirm:
                return {"intent_type": "tool_use", "skip_planner": False}
        else:
            # Fallback: user_id 缺失时使用系统默认值
            if SYSTEM_DEFAULTS["always_confirm"]:
                return {"intent_type": "tool_use", "skip_planner": False}
    except Exception as e:
        logger.warning(f"[IntentClassifier] 获取用户配置失败: {e}")
    
    # 4. LLM 判断（使用快速模型）
    settings = get_settings()
    llm = get_async_llm()
    
    # 从 YAML 获取分类 Prompt
    classify_prompt = get_prompt(
        "supervisor.intent_classifier.classify_prompt",
        summary=summary,
        query=query,
        recent_dialogue=build_recent_dialogue(state.get("messages", [])),
        focus_result_section=build_focus_result_section(focus_result),
    )
    
    try:
        structured_prompt = (
            f"{classify_prompt}\n\n"
            "补充约束：\n"
            "- 先判断是否在追问当前会话里已经存在的结果，再判断现有信息是否足够回答。\n"
            "- 只要用户要求重新查询、重新筛选、重新统计、生成图表、生成文件、导出、检索或处理当前上传的资产，requires_new_tool 必须为 true。\n"
            "- 仅当无需新动作，且现有会话结果足够回答时，has_sufficient_session_context 才能为 true。\n"
            "- 若 requires_new_tool 为 true，则 intent_type 必须为 tool_use。\n"
            "- 若是围绕当前结果的追问，且现有信息足够，intent_type 应为 direct_answer。\n"
            "- 不确定时，输出 tool_use。\n"
        )

        parsed = await llm.generate_structured(
            messages=[{"role": "user", "content": structured_prompt}],
            tool_schema=_INTENT_TOOL_SCHEMA,
            model=settings.llm.fast_model,
            temperature=0.0,
            max_tokens=80,
        )
        decision = FollowupIntentDecision.model_validate(parsed)
        intent = decision.intent_type
        
        if intent == "chitchat":
            logger.info("[IntentClassifier] 判定闲聊: %s", decision.reason)
            return {"intent_type": "chitchat", "skip_planner": True}

        if decision.requires_new_tool:
            logger.info("[IntentClassifier] 判定工具执行: %s", decision.reason)
            return {"intent_type": "tool_use", "skip_planner": False}

        if decision.has_sufficient_session_context and (
            decision.is_followup_to_existing_result or intent == "direct_answer"
        ):
            logger.info("[IntentClassifier] 判定直接回答: %s", decision.reason)
            return {"intent_type": "direct_answer", "skip_planner": True}
        else:
            logger.info("[IntentClassifier] 判定工具执行(上下文不足): %s", decision.reason)
            return {"intent_type": "tool_use", "skip_planner": False}
            
    except Exception as e:
        logger.warning(f"[IntentClassifier] 结构化分类失败，回退文本分类: {e}")
        try:
            fallback_prompt = (
                f"{structured_prompt}\n\n"
                "回退模式：只输出一个词，不要解释：chitchat / direct_answer / tool_use"
            )
            result = await llm.chat(
                messages=[{"role": "user", "content": fallback_prompt}],
                model=settings.llm.fast_model,
                max_tokens=8
            )
            intent = str(result or "").strip().lower().strip("`\"' ")
            if intent not in {"chitchat", "direct_answer", "tool_use"}:
                logger.warning("[IntentClassifier] 回退文本分类输出非法，默认 tool_use: %s", result)
                return {"intent_type": "tool_use", "skip_planner": False}

            return {"intent_type": intent, "skip_planner": intent != "tool_use"}
        except Exception as e2:
            logger.warning(f"[IntentClassifier] 回退文本分类失败，默认 tool_use: {e2}")
        return {"intent_type": "tool_use", "skip_planner": False}
