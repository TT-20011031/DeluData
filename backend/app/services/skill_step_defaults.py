"""
Skill 步骤默认参数合并工具。

职责：
1. 归一化 Skill steps（兼容 Pydantic/dict）
2. 将 Skill 中的结构化参数稳定写入 planner/dry-run 步骤
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.services.doc_scope import normalize_doc_scope


SKILL_PARAM_FIELDS = (
    "template_id",
    "template_version",
    "template_mode",
    "output_filename",
    "doc_scope",
)


def normalize_skill_steps(raw_steps: Any) -> List[Dict[str, Any]]:
    """将 Skill steps 统一为 dict 列表。"""
    if not isinstance(raw_steps, list):
        return []

    result: List[Dict[str, Any]] = []
    for step in raw_steps:
        if hasattr(step, "model_dump"):
            data = step.model_dump()
        elif isinstance(step, dict):
            data = dict(step)
        else:
            continue
        result.append(data)
    return result


def apply_skill_step_defaults(
    mapped_steps: List[Dict[str, Any]],
    llm_steps: List[Dict[str, Any]],
    skill_steps: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    将 Skill 默认参数合并到步骤中。

    规则：
    - 仅按步骤顺序映射（第 N 个 Skill 步骤 -> 第 N 个执行步骤）
    - 已有 params 字段优先，不覆盖用户/LLM 已明确给出的值
    - doc_scope 会先做规范化，空范围不写入
    """
    if not mapped_steps or not skill_steps:
        return mapped_steps

    for idx, mapped_step in enumerate(mapped_steps):
        if idx >= len(skill_steps):
            break

        skill_step = skill_steps[idx] or {}
        llm_step = llm_steps[idx] if idx < len(llm_steps) and isinstance(llm_steps[idx], dict) else {}

        # 当 LLM 未显式指定 worker 时，允许沿用 Skill 的 tool。
        skill_tool = skill_step.get("tool")
        if skill_tool and not llm_step.get("worker"):
            mapped_step["worker"] = skill_tool

        params = mapped_step.get("params") or {}
        for field in SKILL_PARAM_FIELDS:
            if field in params and params[field] is not None:
                continue
            if field not in skill_step:
                continue

            value = deepcopy(skill_step.get(field))
            if field == "doc_scope":
                value = normalize_doc_scope(value, strict=False, drop_empty=True)
                if value is None:
                    continue
            if value is None:
                continue
            params[field] = value

        mapped_step["params"] = params

    return mapped_steps
