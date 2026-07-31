"""
Office 工具 - LangChain Tool 封装

将 OfficeWorker 能力封装为标准 LangChain Tool
供 Supervisor Graph 的 Executor 调用
"""
from typing import Optional, Dict, Any
from langchain_core.tools import tool

from app.tools.base import BaseToolInput, WorkerResult


class OfficeToolInput(BaseToolInput):
    """Office 工具输入"""
    sandbox_path: str = ""
    file_context: Optional[Dict[str, str]] = None
    execution_results: Optional[list] = None  # [P1] 任务执行结果，用于数据源匹配
    template_id: Optional[int] = None
    template_version: Optional[str] = None
    template_mode: Optional[str] = None
    output_filename: Optional[str] = None


@tool(args_schema=OfficeToolInput)
async def office_tool(
    query: str,
    user_id: str = "default_user",
    session_id: str = "",
    parent_step_id: str = "",
    user_context: Optional[Dict[str, Any]] = None,
    messages: Optional[list] = None,
    memory_dfs: Optional[Dict[str, Any]] = None,
    sandbox_path: str = "",
    file_context: Optional[Dict[str, str]] = None,
    round_index: int = 0,  # 会话轮次 执行轮次
    execution_results: Optional[list] = None,  # [P1] 任务执行结果
    template_id: Optional[int] = None,
    template_version: Optional[str] = None,
    template_mode: Optional[str] = None,
    output_filename: Optional[str] = None
) -> WorkerResult:
    """
    处理办公文件（Excel、Word、CSV 等）
    
    可执行的任务包括：
    - 读取和分析 Excel/CSV 数据
    - 数据转换和计算（如货币转换、求和、筛选）
    - 生成新的 Excel/Word 文件
    - 数据可视化图表生成
    
    参数:
        query: 任务描述（如 "把 Excel 的金额列转成美元"）
        user_id: 用户 ID
        session_id: 会话 ID（用于 SSE 推送和文件下载链接）
        parent_step_id: 父步骤 ID
        user_context: 用户上下文（包含 sandbox_path）
        sandbox_path: 沙盒目录路径
        file_context: 文件上下文
        
    返回:
        WorkerResult: 包含输出文本和 artifacts
    """
    from app.agents.office_worker import get_office_worker
    
    worker = get_office_worker()
    
    # 从 user_context 获取沙盒路径（如果未直接传入）
    if not sandbox_path and user_context:
        sandbox_path = user_context.get("sandbox_path", "")
    
    # 如果仍然没有沙盒路径，使用默认路径
    if not sandbox_path:
        from app.config import get_settings
        import os
        settings = get_settings()
        sandbox_path = os.path.join(
            settings.sandbox.base_dir, 
            f"session_{session_id or 'default'}"
        )
    
    # 从 user_context 获取文件上下文
    if not file_context and user_context:
        file_context = user_context.get("file_context")
    
    result = await worker.execute_task(
        task_description=query,
        user_id=user_id,
        sandbox_path=sandbox_path,
        session_id=session_id,
        parent_step_id=parent_step_id,
        messages=messages,
        file_context=file_context,
        memory_dfs=memory_dfs,
        round_index=round_index,  # 会话轮次 透传轮次
        execution_results=execution_results,
        template_id=template_id,
        template_version=template_version,
        template_mode=template_mode,
        output_filename=output_filename,
        user_context=user_context
    )
    
    if result.get("success"):
        output = result.get("output", "")
        artifacts = {
            "output_files": result.get("output_files", []),
            "code": result.get("code", ""),
            "sandbox_path": sandbox_path
        }
        quality_signal = result.get("quality_signal") or {}
        meta = result.get("meta") or {}
        extra_artifacts = result.get("artifacts") or {}
        if isinstance(extra_artifacts, dict):
            artifacts.update(extra_artifacts)
        if "template_preview" in result:
            artifacts["template_preview"] = result.get("template_preview")
        
        # 注意：下载链接已经在 OfficeWorker 中正确生成
        # 格式为 /api/files/download/{session_id}/{filename}
        
        return WorkerResult(
            output=output,
            artifacts=artifacts,
            quality_signal=quality_signal,
            meta=meta,
        )
    else:
        # [FIX] 优先使用友好错误信息
        friendly_error = result.get("friendly_error", "")
        error_msg = friendly_error or result.get("error", "未知错误")
        code = result.get("code", "")
        
        output = f"## 处理失败\n{error_msg}"
        if code:
            output += f"\n\n## 生成的代码\n```python\n{code}\n```"
        
        return WorkerResult(
            output=output,
            artifacts={"error": error_msg},
            quality_signal={
                "verdict": "fail",
                "reason_code": "office_worker_error",
                "confidence": 0.9,
                "retryable": True,
            },
            meta={
                "worker_round": round_index,
                "stop_reason": "office_worker_error",
            },
        )
