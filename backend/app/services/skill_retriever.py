"""
Skill Retriever 服务

在 Graph 入口处检索匹配的 Skill，并准备注入 Planner 的上下文。
遵循设计原则：Async First, Schema Validation, No Hardcoding
"""
import logging
from typing import Optional, Tuple, List, Dict, Any

from app.services.skill_service import get_skill_service
from app.services.skill_step_defaults import normalize_skill_steps
from app.models.common.context import UserContext

logger = logging.getLogger(__name__)


async def retrieve_skill_for_query(
    query: str,
    user_context: UserContext,
    skill_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], str, List[Dict[str, Any]]]:
    """
    根据用户查询检索最匹配的 Skill
    
    Args:
        query: 用户问题
        user_context: 用户上下文
        
    Returns:
        (skill_id, skill_name, skill_context, skill_steps)
        - 如果无匹配则返回 (None, None, "", [])
    """
    logger.info(
        f"[SkillRetriever] 开始检索: query={query[:50]}..., workspace={user_context.workspace_id}, skill_id={skill_id}"
    )
    skill_service = get_skill_service()

    if skill_id:
        logger.info("[SkillRetriever] 使用前端显式指定的 Skill: %s", skill_id)
        raw_skill = await skill_service.get_skill(skill_id)
        if not raw_skill:
            raise ValueError("所选 DeLuSkill 不存在或已删除")
        if raw_skill.workspace_id != user_context.workspace_id:
            raise ValueError("所选 DeLuSkill 不属于当前工作区或无权限访问")

        skill_context = skill_service.format_skill_for_prompt(raw_skill)
        skill_name = raw_skill.title
        skill_steps = normalize_skill_steps(raw_skill.steps)
        logger.info("[SkillRetriever] 显式 Skill 校验通过: %s", skill_name)
        return raw_skill.id, skill_name, skill_context, skill_steps

    try:
        # 检索匹配的 Skills
        logger.debug("[SkillRetriever] 调用 skill_service.search_skills...")
        search_results, decision = await skill_service.search_skills(
            query=query,
            user_context=user_context,
            top_k=1  # 只取最匹配的一个
        )
        logger.info(f"[SkillRetriever] 检索返回: results={len(search_results)}, decision={decision}")
        
        if not search_results or decision == "none":
            logger.debug(f"[SkillRetriever] 未找到匹配的 Skill: {query[:50]}...")
            return None, None, "", []
        
        # 取第一个结果
        top_result = search_results[0]
        skill = top_result.skill
        score = top_result.score
        
        # 使用 SkillService 的格式化方法
        skill_context = skill_service.format_skill_for_prompt(skill)
        skill_id = skill.id
        skill_name = skill.title
        skill_steps = normalize_skill_steps(skill.steps)
        
        logger.info(f"[SkillRetriever] 匹配到 Skill: {skill_name} (score={score:.2f}, decision={decision})")
        
        return skill_id, skill_name, skill_context, skill_steps
        
    except Exception as e:
        logger.error(f"[SkillRetriever] 检索失败: {e}", exc_info=True)
        return None, None, "", []


def prepare_skill_state(
    skill_id: Optional[str],
    skill_name: Optional[str],
    skill_context: str,
    skill_steps: Optional[List[Dict[str, Any]]] = None,
) -> dict:
    """
    构建 Skill 相关的 State 字段
    
    用于在 Graph 入口初始化时合并到 SupervisorState
    """
    return {
        "skill_mode": bool(skill_id),
        "selected_skill_id": skill_id,
        "selected_skill_name": skill_name,
        "skill_context": skill_context,
        "skill_steps": skill_steps or [],
    }
