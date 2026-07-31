"""
KnowledgeRouter 节点 - Wiki-First 检索路径决策（M3.1）

职责：
- 在 IntentClassifier 与 Planner 之间，判定本轮请求应走 Wiki / RAG / 双路径
- 三层判定：开关 → 规则关键词 → LLM 结构化分类
- 仅写状态字段（knowledge_path / knowledge_path_reason / knowledge_path_source），
  不修改任何业务数据；当 settings.wiki.first_enabled=False 时直通 rag，无 LLM 调用

设计原则：
- KISS：纯函数 + 单一节点，无副作用（除写状态外）
- 可观测：knowledge_path_source 标识决策来源（disabled / rule / llm / fallback）便于埋点
- 零回归：开关关闭时返回值与 M2 行为字节级等价
"""
import logging
from typing import Literal

from app.config import get_settings
from app.core.llm.async_llm import get_async_llm
from app.supervisor.state import SupervisorState

logger = logging.getLogger(__name__)


KnowledgePath = Literal["wiki", "rag", "both"]
KnowledgePathSource = Literal["disabled", "scope", "rule", "llm", "fallback"]


# ============ LLM 结构化输出 Schema ============

_PATH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "classify_knowledge_path",
        "description": "判断用户问题应走 Wiki 实体页路径、RAG 切片路径，或两者并行",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "enum": ["wiki", "rag", "both"],
                    "description": (
                        "wiki=综合性/概念性/跨文档问题，适合用预编译实体页回答；"
                        "rag=细节/数字/原文摘录/页码定位，必须回到原始切片；"
                        "both=两类信号都有，建议双路径并行"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "用一句话说明判断依据，便于运维排查",
                },
            },
            "required": ["path", "reason"],
            "additionalProperties": False,
        },
    },
}


_LLM_SYSTEM_PROMPT = """\
你是知识检索路由器。判断给定的用户问题更适合：
- wiki：用预编译的实体页（结构化定义/属性/总结）回答，例如：「差旅报销制度是什么？」「德路平台和竞品的区别？」
- rag：必须回到原始文档切片定位具体细节、公式、参数或页码，例如：「飞机超过几小时可以报销？」「文档第三条原文是什么？」「这条规定第几页？」「螺母支承面力矩如何计算？」
- both：两类信号同时存在，建议并行检索后合并，例如：「介绍下差旅制度并列出最新一线城市住宿标准」

请基于问题语气与意图判定，不要受历史对话或工作区配置影响。
"""


# ============ 规则前置（纯函数，可独立单测） ============

def _classify_by_rules(
    query: str,
    rag_keywords: list[str],
    wiki_keywords: list[str],
) -> tuple[KnowledgePath | None, str]:
    """
    根据关键词命中情况做规则前置判定。

    Returns:
        (path, reason)。未命中任何规则时 path=None，调用方应继续走 LLM。
    """
    if not query:
        return None, ""
    q_lower = query.strip().lower()

    formula_or_calculation_markers = [
        "公式",
        "计算公式",
        "计算方法",
        "如何计算",
        "怎么计算",
        "怎样计算",
        "怎么算",
        "计算",
        "推导",
        "换算",
        "系数",
        "参数",
        "力矩",
        "扭矩",
    ]
    hit_formula = [kw for kw in formula_or_calculation_markers if kw in q_lower]
    if hit_formula:
        return "rag", f"规则匹配：公式/计算类问题需回到原始文档切片 {hit_formula[:3]}"

    product_markers = [
        "研发报告",
        "产品",
        "配方",
        "功效",
        "作用",
        "成分",
        "原料",
        "适用人群",
        "主治",
        "成本",
        "价格",
    ]
    dosage_markers = [
        "颗粒",
        "含片",
        "口服液",
        "胶囊",
        "片剂",
        "丸",
        "粉",
        "饮",
        "茶",
        "膏",
    ]
    if any(marker in q_lower for marker in product_markers) and any(
        marker in q_lower for marker in dosage_markers
    ):
        return "both", "规则匹配：产品研发报告类问题，优先同时检索 Wiki 与原始文档"

    engineering_subject_markers = [
        "点焊",
        "焊接",
        "螺栓",
        "螺母",
        "铆接",
        "连接",
        "紧固",
    ]
    engineering_detail_markers = [
        "受力",
        "合理",
        "力矩",
        "扭矩",
        "应力",
        "失效",
        "载荷",
        "结构",
        "方式",
        "分析",
    ]
    if any(marker in q_lower for marker in engineering_subject_markers) and any(
        marker in q_lower for marker in engineering_detail_markers
    ):
        return "both", "规则匹配：工程结构/连接受力类问题，需要同时检索 Wiki 与原始文档"

    hit_rag = [kw for kw in rag_keywords if kw and kw in q_lower]
    hit_wiki = [kw for kw in wiki_keywords if kw and kw in q_lower]

    if hit_rag and hit_wiki:
        return "both", f"规则匹配：同时含 RAG 关键词 {hit_rag[:3]} 与 Wiki 关键词 {hit_wiki[:3]}"
    if hit_rag:
        return "rag", f"规则匹配：含 RAG 关键词 {hit_rag[:3]}"
    if hit_wiki:
        return "wiki", f"规则匹配：含 Wiki 关键词 {hit_wiki[:3]}"
    return None, ""


# ============ 节点主逻辑 ============

async def knowledge_router_node(state: SupervisorState) -> dict:
    """
    Wiki-First 路径决策节点（M3.1）

    路由优先级：
    1. 开关关闭 → rag（直通，无 LLM 调用）
    2. 规则关键词命中 → wiki / rag / both
    3. LLM 结构化分类 → wiki / rag / both
    4. LLM 失败 → settings.wiki.router_fallback_path

    Returns:
        dict: 仅包含 knowledge_path / knowledge_path_reason / knowledge_path_source
    """
    settings = get_settings()
    wiki_cfg = settings.wiki

    # === 1. 总开关关闭：直通 rag，零 LLM 成本 ===
    if not wiki_cfg.first_enabled:
        return {
            "knowledge_path": "rag",
            "knowledge_path_reason": "Wiki-First 开关未开启，使用 RAG 路径",
            "knowledge_path_source": "disabled",
        }

    intent_type = state.get("intent_type", "tool_use")
    if intent_type != "tool_use":
        logger.info(
            "[KnowledgeRouter] intent_type=%s still participates in knowledge routing",
            intent_type,
        )

    query = state.get("user_query", "")

    # === [M3.5] 用户显式 Wiki 范围（domains / wiki_slugs）：直接走 wiki 路径 ===
    user_context = state.get("user_context") or {}
    session_scope = (
        user_context.get("doc_scope") if isinstance(user_context, dict) else None
    )
    if isinstance(session_scope, dict):
        scoped_domains = session_scope.get("domains") or []
        scoped_slugs = session_scope.get("wiki_slugs") or []
        if (isinstance(scoped_domains, list) and scoped_domains) or (
            isinstance(scoped_slugs, list) and scoped_slugs
        ):
            logger.info(
                "[KnowledgeRouter] 用户显式 Wiki 范围命中: domains=%d slugs=%d",
                len(scoped_domains or []), len(scoped_slugs or []),
            )
            return {
                "knowledge_path": "wiki",
                "knowledge_path_reason": "用户显式选择了 Wiki 域 / 实体页",
                "knowledge_path_source": "scope",
            }

    # === 3. 规则前置 ===
    rule_path, rule_reason = _classify_by_rules(
        query,
        rag_keywords=wiki_cfg.router_keywords_rag_list,
        wiki_keywords=wiki_cfg.router_keywords_wiki_list,
    )
    if rule_path is not None:
        logger.info("[KnowledgeRouter] 规则命中: path=%s reason=%s", rule_path, rule_reason)
        return {
            "knowledge_path": rule_path,
            "knowledge_path_reason": rule_reason,
            "knowledge_path_source": "rule",
        }

    # === 4. LLM 结构化判定 ===
    try:
        llm = get_async_llm()
        parsed = await llm.generate_structured(
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": f"用户问题：{query}"},
            ],
            tool_schema=_PATH_TOOL_SCHEMA,
            model=wiki_cfg.router_model or settings.llm.fast_model,
            temperature=0.0,
            max_tokens=80,
        )
        path = (parsed or {}).get("path")
        reason = (parsed or {}).get("reason", "")
        if path in ("wiki", "rag", "both"):
            logger.info("[KnowledgeRouter] LLM 判定: path=%s reason=%s", path, reason)
            return {
                "knowledge_path": path,
                "knowledge_path_reason": reason or "LLM 未给出原因",
                "knowledge_path_source": "llm",
            }
        logger.warning("[KnowledgeRouter] LLM 输出非法 path=%r，走 fallback", path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[KnowledgeRouter] LLM 判定失败，走 fallback: %s", exc)

    # === 5. Fallback ===
    fallback_path = wiki_cfg.router_fallback_path
    if fallback_path not in ("wiki", "rag", "both"):
        fallback_path = "both"
    return {
        "knowledge_path": fallback_path,
        "knowledge_path_reason": "LLM 不可用或输出非法，使用配置回退路径",
        "knowledge_path_source": "fallback",
    }
