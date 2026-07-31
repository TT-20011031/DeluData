"""
Skill Dry Run 服务

模拟 Planner 规划过程，预览生成的执行计划（不实际执行）

设计原则遵循：
- Async First: 异步 LLM 调用
- No Hardcoding: Prompt 模板从 YAML 文件加载
- Schema Validation: 输出经过 Pydantic 校验
- 安全性: 使用 messages 分离，防止 Prompt Injection
"""
import logging
from pathlib import Path
from typing import Optional, List

import yaml

from app.core.llm.async_llm import get_async_llm
from app.services.skill_service import get_skill_service
from app.services.skill_step_defaults import apply_skill_step_defaults, normalize_skill_steps
from app.models.config.skill_schemas import (
    DryRunRequest,
    DryRunResponse,
    DryRunStepPreview,
)
from app.models.common.context import UserContext
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


class DryRunService:
    """
    Skill Dry Run 服务
    
    模拟 Planner 规划过程，生成预测的执行计划
    """
    
    def __init__(self):
        self._prompts = _load_skill_prompts()
    
    def _get_system_prompt(self, skill_content: str) -> str:
        """获取系统提示词"""
        template = self._prompts.get("dry_run_system", "")
        if not template:
            # 降级提示
            logger.warning("dry_run_system prompt not found in skill.yaml")
            return f"根据以下操作手册生成执行计划。\n\n{skill_content}"
        
        return template.format(skill_content=skill_content)
    
    async def run(
        self,
        request: DryRunRequest,
        user_context: UserContext
    ) -> DryRunResponse:
        """
        执行 Dry Run 测试
        
        Args:
            request: Dry Run 请求
            user_context: 用户上下文
            
        Returns:
            Dry Run 响应，包含预测的执行计划
            
        Raises:
            ValueError: Skill 不存在或测试失败
        """
        # 1. 加载 Skill
        skill_service = get_skill_service()
        skill = await skill_service.get_skill(request.skill_id, user_context)
        
        if not skill:
            raise ValueError("Skill 不存在或无权访问")
        
        # 2. 格式化 Skill 上下文
        skill_context = skill_service.format_skill_for_prompt(skill)
        skill_steps = normalize_skill_steps(skill.steps)
        
        # 3. 构建 messages（分离 system/user，防止 Prompt Injection）
        messages = [
            {"role": "system", "content": self._get_system_prompt(skill_context)},
            {"role": "user", "content": f"用户问题：{request.test_query}\n\n请根据操作手册，为此问题生成执行计划："}
        ]
        
        # 4. 调用 LLM
        settings = get_settings()
        llm = get_async_llm()
        
        try:
            result = await llm.generate_json(
                messages=messages,
                model=settings.llm.planner_model if hasattr(settings.llm, 'planner_model') else settings.llm.model,
                temperature=0.2  # 低温度提高稳定性
            )
        except Exception as e:
            logger.error(f"Dry Run LLM 调用失败: {e}")
            raise ValueError(f"预测失败: {str(e)}")
        
        raw_steps = result.get("predicted_steps", result.get("steps", []))
        if not isinstance(raw_steps, list):
            raw_steps = []

        # 与 Planner 执行链路复用同一套 Skill 参数合并逻辑，保证 dry-run 结果一致。
        mapped_steps = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                mapped_steps.append({"worker": "unknown", "params": {}})
                continue
            mapped_steps.append(
                {
                    "worker": raw_step.get("worker", "unknown"),
                    "params": raw_step.get("params", {}) or {},
                }
            )
        merged_steps = apply_skill_step_defaults(mapped_steps, raw_steps, skill_steps)

        for idx, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, dict):
                continue
            merged = merged_steps[idx] if idx < len(merged_steps) else {}
            raw_step["worker"] = merged.get("worker", raw_step.get("worker", "unknown"))
            raw_step["params"] = merged.get("params", raw_step.get("params", {}) or {})

        # 5. 构建响应
        predicted_steps = self._parse_steps(raw_steps)
        
        return DryRunResponse(
            skill_used=skill.title,
            test_query=request.test_query,
            predicted_steps=predicted_steps,
            thinking=result.get("thinking", "")
        )
    
    def _parse_steps(self, steps: List[dict]) -> List[DryRunStepPreview]:
        """
        解析并校验步骤列表
        
        Args:
            steps: 原始步骤列表
            
        Returns:
            解析后的步骤预览列表
        """
        parsed = []
        for i, step in enumerate(steps):
            try:
                preview = DryRunStepPreview(
                    step_id=str(step.get("step_id", i + 1)),
                    instruction=step.get("instruction", f"步骤 {i + 1}"),
                    worker=step.get("worker", "unknown"),
                    params=step.get("params", {}) or {}
                )
                parsed.append(preview)
            except Exception as e:
                logger.warning(f"解析步骤 {i + 1} 失败: {e}")
                continue
        
        return parsed


# ========== 单例 ==========

_dry_run_service: Optional[DryRunService] = None


def get_dry_run_service() -> DryRunService:
    """获取 DryRunService 单例"""
    global _dry_run_service
    if not _dry_run_service:
        _dry_run_service = DryRunService()
    return _dry_run_service
