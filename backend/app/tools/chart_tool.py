"""
数据可视化工具 - 纯 LLM 生成 HTML

核心逻辑:
1. 从 memory_dfs 获取数据
2. 先做数据相关性判定（无关则跳过）
3. 选择与 query 直接相关的数据源和片段
4. 调用 LLM 生成可视化 HTML
5. 保存 HTML 并发送 Artifact 事件
"""
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from langchain_core.tools import StructuredTool

from app.api.events import emit_artifact
from app.config import get_settings
from app.core.llm.async_llm import get_async_llm
from app.services.generation_data_guard import evaluate_generation_data_relevance
from app.services.generation_data_service import select_primary_generation_source
from app.services.generation_authorization_guard import filter_authorized_generation_sources
from app.tools.base import BaseToolInput, WorkerResult

logger = logging.getLogger(__name__)


class ChartToolInput(BaseToolInput):
    """数据可视化工具输入参数"""


def _resolve_chart_worker_llm_settings(settings) -> tuple[str, bool]:
    model_name = str(
        getattr(settings.llm, "chart_worker_model", None)
        or getattr(settings.llm, "synthesizer_model", "qwen3.5-plus")
    ).strip()
    enable_thinking = bool(getattr(settings.llm, "chart_worker_enable_thinking", False))
    return model_name, enable_thinking


async def run_chart_task(
    query: str,
    session_id: str = "",
    parent_step_id: str = "",
    user_context: Optional[Dict] = None,
    messages: Optional[list] = None,
    memory_dfs: Optional[Dict] = None,
    round_index: int = 0,
    execution_results: Optional[list] = None,
    **kwargs,
) -> WorkerResult:
    """执行数据可视化任务。"""
    logger.info("chart_tool: 执行可视化, query=%s...", query[:50])

    if not memory_dfs:
        return WorkerResult(
            output="数据无效：没有可用的数据，已跳过图表生成。",
            quality_signal={
                "verdict": "pass",
                "reason_code": "terminal_data_invalid",
                "confidence": 1.0,
                "retryable": False,
            },
            meta={
                "worker_round": round_index,
                "stop_reason": "terminal_data_invalid",
                "step_status": "invalid_data",
                "invalid_reason": "memory_dfs 为空",
            },
        )

    memory_dfs, rejected_sources = await filter_authorized_generation_sources(
        memory_dfs, str((user_context or {}).get("user_id") or kwargs.get("user_id") or ""),
    )
    if not memory_dfs:
        return WorkerResult(
            output="权限已发生变化，原问数结果不能继续生成图表，请重新问数后再试。",
            quality_signal={
                "verdict": "pass",
                "reason_code": "authorization_changed",
                "confidence": 1.0,
                "retryable": True,
            },
            meta={"rejected_sources": rejected_sources, "step_status": "authorization_changed"},
        )

    relevance = await evaluate_generation_data_relevance(
        query=query,
        memory_dfs=memory_dfs,
        execution_results=execution_results,
        messages=messages,
    )
    if not relevance.get("is_relevant", True):
        reason = str(relevance.get("reason", "数据与问题不相关"))
        logger.info("chart_tool: 数据无效，跳过图表生成, reason=%s", reason)
        return WorkerResult(
            output=f"数据无效，已跳过图表生成。原因：{reason}",
            quality_signal={
                "verdict": "pass",
                "reason_code": "terminal_data_invalid",
                "confidence": relevance.get("confidence", 0.9),
                "retryable": False,
            },
            meta={
                "worker_round": round_index,
                "stop_reason": "terminal_data_invalid",
                "step_status": "invalid_data",
                "invalid_reason": reason,
            },
        )

    selected_source = select_primary_generation_source(
        query,
        memory_dfs,
        max_rows=50,
        max_chars_per_source=6000,
    )
    if not selected_source or not selected_source.preview_text.strip():
        return WorkerResult(
            output="数据无效：没有筛选出可用于图表生成的相关数据，已跳过图表生成。",
            quality_signal={
                "verdict": "pass",
                "reason_code": "terminal_data_invalid",
                "confidence": 0.98,
                "retryable": False,
            },
            meta={
                "worker_round": round_index,
                "stop_reason": "terminal_data_invalid",
                "step_status": "invalid_data",
                "invalid_reason": "未筛选出可用于图表生成的相关数据",
            },
        )

    df_key = selected_source.key
    data_preview = selected_source.preview_text
    row_count = int(selected_source.selected_items or selected_source.total_items or 0)
    total_count = int(selected_source.total_items or row_count)
    data_type_map = {
        "dataframe": "表格数据",
        "list": "列表数据",
        "dict": "字典数据",
        "text": "文本数据",
    }
    data_type = data_type_map.get(selected_source.source_type, selected_source.source_type)
    selection_summary = (
        f"已选择数据源: {selected_source.key}\n"
        f"筛选说明: {selected_source.reason}\n"
        f"原始条目: {total_count}\n"
        f"保留条目: {row_count}\n"
    )

    prompt = f"""根据以下数据，生成一个美观的 HTML 页面用于可视化展示。

用户需求: {query}
{selection_summary}
数据类型: {data_type}
数据来源: {df_key}
数据内容:
{data_preview}

要求:
1. 生成完整 HTML 页面（包含 <!DOCTYPE html>）
2. 先理解用户需求，只使用与需求直接相关的字段、行、片段，不要把所有检索到的数据一股脑全部画进去
3. 自由选择最合适的可视化方式（图表、表格、信息图等）
4. 如果 query 指定了时间、地区、对象、指标，只展示匹配范围的数据
5. 数据不足时明确展示“相关数据不足”，不要用无关数据凑图
6. 页面简洁专业，不要长篇文字报告
7. 可以使用内联 CSS 和 JavaScript
8. 直接返回 HTML 代码，不要解释
"""

    messages = [
        {
            "role": "system",
            "content": "你是数据可视化专家。直接返回完整 HTML 代码，不要解释或 markdown 标记。",
        },
        {"role": "user", "content": prompt},
    ]

    llm = get_async_llm()
    settings = get_settings()
    chart_model, enable_thinking = _resolve_chart_worker_llm_settings(settings)
    chat_kwargs = {"model": chart_model}
    if not enable_thinking:
        chat_kwargs["extra_body"] = {"enable_thinking": False}

    try:
        html_content = await llm.chat(messages, **chat_kwargs)

        # 清理可能的 markdown 包装
        if html_content.startswith("```"):
            lines = html_content.split("\n")
            html_content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        sandbox_path = (
            Path(user_context.get("sandbox_path", settings.sandbox.base_dir))
            if user_context
            else Path(settings.sandbox.base_dir)
        )
        sandbox_path.mkdir(parents=True, exist_ok=True)

        import uuid

        file_name = f"visualization_{uuid.uuid4().hex[:8]}.html"
        html_path = sandbox_path / file_name

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info("chart_tool: HTML 已保存到 %s", html_path)

        # 保存到临时产物收纳箱（按 workspace_id + user_id 隔离）
        try:
            from app.services.temp_artifact_service import get_temp_artifact_service

            user_ctx = user_context or {}
            workspace_id = str(user_ctx.get("workspace_id") or kwargs.get("workspace_id") or "default")
            user_id = str(user_ctx.get("user_id") or kwargs.get("user_id") or "anonymous")
            await get_temp_artifact_service().save_chart(
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id or "",
                title=f"数据可视化报告 - {df_key}"[:50],
                html_content=html_content,
            )
        except Exception as save_err:
            logger.warning("chart_tool: 暂存箱保存失败(非致命): %s", save_err)

        # 发送 Artifact 事件供前端展示
        if session_id:
            await emit_artifact(
                session_id,
                artifact_type="html_report",
                artifact_data={
                    "report_id": file_name,
                    "step_id": parent_step_id or "",
                    "title": f"数据可视化报告 - {df_key}",
                    "html_content": html_content,
                    "file_path": str(html_path),
                },
                round_index=round_index,
            )

        return WorkerResult(
            output=f"已生成数据可视化图表（共 {row_count} 条数据）。",
            artifacts={"html_path": str(html_path), "html_content": html_content},
            quality_signal={
                "verdict": "pass",
                "reason_code": "chart_generated",
                "confidence": 0.9,
                "retryable": False,
            },
            meta={
                "worker_round": round_index,
                "stop_reason": "chart_generated",
                "selected_data_source": df_key,
                "selected_data_reason": selected_source.reason,
                "selected_item_count": row_count,
                "total_item_count": total_count,
            },
        )

    except Exception as e:
        logger.error("chart_tool: 生成失败: %s", e)
        return WorkerResult(
            output=f"可视化生成失败: {str(e)}",
            quality_signal={
                "verdict": "fail",
                "reason_code": "chart_generation_error",
                "confidence": 0.9,
                "retryable": True,
            },
            meta={
                "worker_round": round_index,
                "stop_reason": "chart_generation_error",
            },
        )


chart_tool = StructuredTool.from_function(
    func=None,
    coroutine=run_chart_task,
    name="chart_worker",
    description="根据数据生成可视化 HTML 页面（LLM 驱动）。",
    args_schema=ChartToolInput,
)
