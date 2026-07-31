"""
OfficeWorker - 办公文件处理智能体
Facade 模块：路由 + 策略选择
策略模式:
- 生成处理器：Markdown → Word 快速生成（无文件或简单生成）
- 编辑处理器：ReAct/MCP 模式（有文件 + 修改意图）
"""
import logging
from typing import Dict, Any, Optional, List
from app.agents.office.handlers import TaskContext, CreationHandler, ExcelCreationHandler, TemplateHandler
from app.services.generation_authorization_guard import filter_authorized_generation_sources
logger = logging.getLogger(__name__)


EXCEL_OUTPUT_EXTENSIONS = (".xlsx", ".xls", ".xlsm")
EXCEL_REQUEST_KEYWORDS = ("excel", ".xlsx", ".xls", "工作簿", "电子表格", "xlsx")


class OfficeWorker:
    """
    办公文件处理智能体
    使用策略模式，根据任务类型选择不同的 Handler
    """
    def __init__(self):
        self.handlers = {
            "creation": CreationHandler(),
            "excel_creation": ExcelCreationHandler(),
            "template": TemplateHandler(),
        }
    async def execute_task(
        self,
        task_description: str,
        user_id: str,
        sandbox_path: str,
        session_id: str = "",
        parent_step_id: str = "",
        messages: Optional[List[Any]] = None,
        file_context: Optional[Dict[str, str]] = None,
        memory_dfs: Optional[Dict[str, Any]] = None,
        round_index: int = 0,  # 会话轮次 执行轮次
        execution_results: Optional[list] = None,  # [P1] 任务执行结果
        template_id: Optional[int] = None,
        template_version: Optional[str] = None,
        template_mode: Optional[str] = None,
        output_filename: Optional[str] = None,
        user_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        执行办公文件处理任务
        返回:
            task_description: 任务描述
            user_id: 用户 ID
            sandbox_path: 沙盒目录路径
            session_id: 会话 ID
            parent_step_id: 父步骤 ID
            file_context: 文件上下文
            memory_dfs: 内存数据
            round_index: 执行轮次
            execution_results: 任务执行结果（用于数据源匹配）
        返回:
            执行结果 dict
        """
        logger.info(f"OfficeWorker: 开始执行, task={task_description[:50]}..., sandbox={sandbox_path}")
        memory_dfs, rejected_sources = await filter_authorized_generation_sources(
            memory_dfs, user_id,
        )
        if rejected_sources and not memory_dfs:
            return {
                "success": False,
                "error": "authorization_changed",
                "friendly_error": "权限已发生变化，原问数结果不能继续生成 Office 文件，请重新问数后再试。",
                "rejected_sources": rejected_sources,
            }
        # 1. 构建不可变上下文
        ctx = TaskContext(
            task_description=task_description,
            sandbox_path=sandbox_path,
            session_id=session_id,
            user_id=user_id,
            parent_step_id=parent_step_id,
            user_context=user_context,
            template_id=template_id,
            template_version=template_version,
            template_mode=template_mode,
            output_filename=output_filename,
            file_context=file_context,
            messages=messages,
            memory_dfs=memory_dfs,
            round_index=round_index,  # 会话轮次 透传轮次
            execution_results=execution_results,  # [P1] 透传任务结果
        )
        # 2. 检测任务模式
        if template_id or (template_mode and template_mode in ["template", "render", "draft", "preview"]):
            task_mode = "template"
        else:
            task_mode = self._detect_task_mode(ctx)
        logger.info(f"OfficeWorker: 任务模式 = {task_mode}")
        # 3. 选择 Handler 并执行
        handler = self.handlers.get(task_mode, self.handlers["creation"])
        try:
            return await handler.handle(ctx)
        except Exception as e:
            logger.error(f"OfficeWorker 执行失败: {e}", exc_info=True)
            from app.agents.office.error_translator import translate_python_error
            friendly_error = translate_python_error(str(e))
            return {
                "success": False,
                "error": str(e),
                "friendly_error": friendly_error
            }
    def _detect_task_mode(self, ctx: TaskContext) -> str:
        """
        检测任务模式（三模式架构）
        参数:
            "creation" - 快速生成模式
            "excel_creation" - 高质量 Excel 生成
        """
        output_filename = (ctx.output_filename or "").strip().lower()
        if output_filename.endswith(EXCEL_OUTPUT_EXTENSIONS):
            return "excel_creation"
        if any(keyword in output_filename for keyword in EXCEL_REQUEST_KEYWORDS):
            return "excel_creation"

        task_description = (ctx.task_description or "").strip().lower()
        if any(keyword in task_description for keyword in EXCEL_REQUEST_KEYWORDS):
            return "excel_creation"

        return "creation"
# 单例
_office_worker: Optional[OfficeWorker] = None
def get_office_worker() -> OfficeWorker:
    """获取 OfficeWorker 单例"""
    global _office_worker
    if _office_worker is None:
        _office_worker = OfficeWorker()
    return _office_worker
