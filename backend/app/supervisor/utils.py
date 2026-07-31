"""
Supervisor 辅助函数
包含节点间共享的工具函数
"""
import logging
logger = logging.getLogger(__name__)
def _map_and_create_steps(
    llm_steps: list,
    id_prefix: str = "",
    description_prefix: str = ""
) -> list:
    """
    将 LLM 输出的步骤转换为 TaskStep，并处理 ID 映射
    Args:
        llm_steps: LLM 输出的步骤列表
        id_prefix: 步骤 ID 前缀（如 "round_1_"）
        description_prefix: 描述前缀（如 "[补充] "）
    Returns:
        TaskStep 字典列表
    """
    from app.models.common.context import TaskStep as TaskStepModel
    from app.worker_categories import get_worker_category
    # 建立 LLM_ID -> SYSTEM_ID 映射
    id_mapping = {}
    for i, step in enumerate(llm_steps):
        llm_id = step.get("step_id", str(i + 1))
        system_id = f"{id_prefix}{i + 1}"
        id_mapping[llm_id] = system_id
    # 生成 TaskStep
    steps = []
    for i, step in enumerate(llm_steps):
        llm_id = step.get("step_id", str(i + 1))
        system_id = id_mapping[llm_id]
        worker = step.get("worker", "sql_worker")
        description = step.get("description", "")
        if description_prefix:
            description = f"{description_prefix}{description}"
        step_obj = TaskStepModel(
            step_id=system_id,
            description=description,
            worker=worker,
            status="pending",
            result=None,
            category=get_worker_category(worker).value,
            editable=step.get("editable", False),
            suggested_value=str(step.get("suggested_value")) if step.get("suggested_value") is not None else None,
            input_type=step.get("input_type"),
            input_label=step.get("input_label"),
            options=step.get("options", []),
            params=step.get("params", {}) or {}
        )
        steps.append(step_obj.model_dump())
    if id_mapping:
        logger.debug(f"Planner: ID 映射表: {id_mapping}")
    return steps
# ========== [步进式反思] TaskStateManager ==========
class TaskStateManager:
    """
    任务状态管理器
    职责：
    - 验证 (verify): 标记步骤为已验证
    - 解锁 (unlock): 所有提取任务结束后激活终结类任务
    - 剪枝 (prune): 全部提取失败时取消终结类任务
    设计原则：
    - 单一职责：仅处理任务状态变更，不涉及 LLM 调用
    - 模块化解耦：作为编排层通用状态工具
    """
    def __init__(self, task_plan: list):
        """
        初始化状态管理器
        Args:
            task_plan: 任务计划列表
        """
        self.task_plan = task_plan
    def is_step_verified(self, step_id: str) -> bool:
        """检查指定步骤是否已通过验证"""
        for step in self.task_plan:
            if step.get("step_id") == step_id:
                return step.get("verified", False)
        return False
    def verify_step(self, step_id: str) -> bool:
        """标记步骤为已验证"""
        for step in self.task_plan:
            if step.get("step_id") == step_id:
                step["verified"] = True
                return True
        logger.warning(f"[Verify] 未找到步骤 {step_id}")
        return False
    def unlock_terminal_steps(self) -> int:
        """
        尝试解锁终结类任务
        逻辑：只有当【所有】提取类任务都已结束（无论 pass/fail），才解锁终结类
        终结类只使用 memory_dfs 中的数据
        Returns:
            解锁的任务数量
        """
        from app.worker_categories import is_terminal_worker
        # 已结束的状态集合
        finished_statuses = {"completed", "error", "skipped", "cancelled"}
        # 1. 检查是否还有提取类任务未结束
        for step in self.task_plan:
            worker = step.get("worker", "")
            status = step.get("status", "")
            if not is_terminal_worker(worker):
                if status not in finished_statuses:
                    return 0
        # 2. 所有提取任务已结束，解锁终结类
        unlocked = 0
        for step in self.task_plan:
            if step.get("status") == "waiting" and is_terminal_worker(step.get("worker", "")):
                step["status"] = "pending"
                unlocked += 1
        return unlocked
    def prune_terminal_steps(self) -> int:
        """
        剪枝终结类任务（当所有提取都失败时）
        Returns:
            剪枝的任务数量
        """
        from app.worker_categories import is_terminal_worker
        pruned = 0
        for step in self.task_plan:
            if step.get("status") == "waiting" and is_terminal_worker(step.get("worker", "")):
                step["status"] = "cancelled"
                step["result"] = "跳过：无有效数据"
                pruned += 1
        return pruned
    def get_pending_steps(self) -> list:
        """获取所有待执行步骤"""
        return [s for s in self.task_plan if s.get("status") == "pending"]
    def get_waiting_steps(self) -> list:
        """获取所有等待中步骤"""
        return [s for s in self.task_plan if s.get("status") == "waiting"]
    def has_remaining_work(self) -> bool:
        """检查是否还有待完成的工作"""
        for step in self.task_plan:
            if step.get("status") in ["pending", "waiting"]:
                return True
        return False
