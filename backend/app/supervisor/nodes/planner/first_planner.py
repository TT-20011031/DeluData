"""
首次规划模块

职责：
- 处理首次规划请求
- 步骤状态初始化（pending/waiting）
- 返回 draft 状态等待确认
"""
import logging
from app.supervisor.state import SupervisorState
from app.worker_categories import is_terminal_worker, is_extraction_worker
from .utils import map_and_create_steps
from app.services.skill_step_defaults import apply_skill_step_defaults

logger = logging.getLogger(__name__)


async def first_plan(
    state: SupervisorState,
    llm_result: dict,
    need_confirm: bool = True,
    tool_confidence: str = "low"
) -> dict:
    """
    首次规划：生成完整任务链
    
    Args:
        state: 当前状态
        llm_result: LLM 返回的规划结果
        need_confirm: 是否需要用户确认
        tool_confidence: 工具选择自信度
        
    Returns:
        状态更新字典
    """
    # 1. 转换步骤格式
    llm_steps = llm_result.get("steps", [])
    steps = map_and_create_steps(llm_steps)
    # 将 Skill 的结构化默认参数合并到执行步骤，保证 doc_scope 等参数稳定落地。
    skill_steps = state.get("skill_steps", []) or []
    steps = apply_skill_step_defaults(steps, llm_steps, skill_steps)
    
    # 2. 检查是否存在提取类任务
    has_extraction = any(is_extraction_worker(s.get("worker", "")) for s in steps)
    
    # 3. 步骤状态初始化
    # 设计原则：只有存在提取类任务时，终结类才需要等待
    # 场景 A：有提取类 + 终结类 → 终结类 waiting
    # 场景 B：只有终结类 → 终结类 pending（直接用上下文执行）
    for step in steps:
        worker = step.get("worker", "")
        if is_terminal_worker(worker):
            step["status"] = "waiting" if has_extraction else "pending"
            step["verified"] = False
        else:
            step["status"] = "pending"
            step["verified"] = False
    
    # 4. 构建返回状态
    return {
        "task_plan": steps,
        "plan_summary": llm_result.get("summary", ""),
        "plan_status": "draft",  # 等待用户确认
        "need_confirm": need_confirm,
        "tool_confidence": tool_confidence,
        "steps_executed_count": 0,
        "retry_count": 0,
        "tried_workers": [],
        "route_action": "continue",
        "latest_quality_signal": {},
        # round_index 由 chat_service.py 正确设置，此处不覆盖
    }

