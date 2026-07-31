"""
Skill 生成器

使用 LLM 根据自然语言描述生成 Skill 内容

设计原则遵循：
- No Hardcoding: Prompt 模板从 YAML 文件加载
- Schema Validation: 输出经过 Pydantic 校验
- Async First: 异步 LLM 调用
- 安全性: 使用 messages 分离，防止 Prompt Injection
"""
import logging
from pathlib import Path
from typing import Optional, List, Set

import yaml

from app.core.llm.async_llm import get_async_llm
from app.models.config.skill_schemas import (
    SkillGenerateRequest,
    SkillGenerateResponse,
    SkillStepSchema,
    SkillToolType,
)
from app.config import get_settings

logger = logging.getLogger(__name__)


def _load_skill_prompts() -> dict:
    """
    从 YAML 文件加载 Skill Prompt 模板
    
    遵循 No Hardcoding 原则
    """
    prompts_path = Path(__file__).parent.parent.parent / "prompts" / "skill.yaml"
    if not prompts_path.exists():
        logger.warning(f"Skill prompts file not found: {prompts_path}")
        return {}
    
    with open(prompts_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class SkillGenerator:
    """
    Skill 生成器
    
    使用 LLM 将自然语言描述转换为结构化 Skill
    """
    
    # 合法工具集合（用于校验和清洗）
    VALID_TOOLS: Set[str] = {t.value for t in SkillToolType}
    
    def __init__(self):
        self._prompts = _load_skill_prompts()
    
    def _get_tool_list_str(self) -> str:
        """获取工具列表的格式化字符串"""
        tool_types = self._prompts.get("skill_tool_types", [])
        if not tool_types:
            # 降级到硬编码默认值
            return "\n".join([
                f"- {t.value}" for t in SkillToolType
            ])
        return "\n".join([
            f"- {t['name']}: {t['description']}" 
            for t in tool_types
        ])
    
    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        template = self._prompts.get("skill_generate_system", "")
        if not template:
            # 降级提示
            logger.warning("skill_generate_system prompt not found in skill.yaml")
            return "请根据用户描述生成结构化的 Skill 定义。"
        
        return template.format(tool_list=self._get_tool_list_str())
    
    async def generate(self, request: SkillGenerateRequest) -> SkillGenerateResponse:
        """
        根据自然语言描述生成 Skill
        
        安全设计：
        - 用户输入作为独立的 user message，而非 system prompt 的一部分
        - 输出经过工具名校验，清洗非法工具
        
        Args:
            request: 生成请求
            
        Returns:
            生成的 Skill 结构
            
        Raises:
            ValueError: 生成失败
        """
        settings = get_settings()
        llm = get_async_llm()
        
        # 构建 messages（分离 system 和 user，防止 Prompt Injection）
        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": f"请根据以下需求生成 Skill：\n\n{request.user_input}"}
        ]
        
        try:
            result = await llm.generate_json(
                messages=messages,
                model=settings.llm.model,
                temperature=0.3  # 降低温度提高稳定性
            )
            
            # 校验并清洗步骤中的工具名
            cleaned_steps = self._validate_and_clean_steps(result.get("steps", []))
            
            return SkillGenerateResponse(
                title=result.get("title", "未命名 Skill"),
                description=result.get("description", ""),
                steps=cleaned_steps,
                tags=result.get("tags", []),
                example_queries=result.get("example_queries", [])
            )
            
        except Exception as e:
            logger.error(f"Skill 生成失败: {e}")
            raise ValueError(f"Skill 生成失败: {str(e)}")
    
    def _validate_and_clean_steps(
        self,
        steps: List[dict]
    ) -> List[SkillStepSchema]:
        """
        校验并清洗步骤列表
        
        - 过滤非法工具名
        - 规范化步骤格式
        
        Args:
            steps: 原始步骤列表
            
        Returns:
            清洗后的步骤列表
        """
        cleaned = []
        for i, step in enumerate(steps):
            tool = step.get("tool")
            
            # 校验工具名
            if tool and tool not in self.VALID_TOOLS:
                logger.warning(f"LLM 生成了非法工具: '{tool}'，已清洗为 None")
                tool = None
            
            # 转换为 SkillStepSchema
            try:
                cleaned_step = SkillStepSchema(
                    step=step.get("step", i + 1),
                    action=step.get("action", f"步骤 {i + 1}"),
                    tool=tool,
                    template=step.get("template"),
                    keywords=step.get("keywords", []),
                    template_id=step.get("template_id"),
                    template_version=step.get("template_version"),
                    template_mode=step.get("template_mode"),
                    output_filename=step.get("output_filename"),
                    doc_scope=step.get("doc_scope")
                )
                cleaned.append(cleaned_step)
            except Exception as e:
                logger.warning(f"步骤 {i + 1} 格式错误，跳过: {e}")
                continue
        
        return cleaned


# ========== 单例 ==========

_generator: Optional[SkillGenerator] = None


def get_skill_generator() -> SkillGenerator:
    """获取 SkillGenerator 单例"""
    global _generator
    if not _generator:
        _generator = SkillGenerator()
    return _generator
