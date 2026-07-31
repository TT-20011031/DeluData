"""
Creation Handler - Markdown → Word 快速生成

用于处理纯生成类任务（无需读取现有文件）
"""
import os
import re
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from app.core.llm.async_llm import get_async_llm
from app.core.llm.prompt_manager import get_prompt
from app.config import get_settings
from app.core.utils.docx_style_engine import apply_dynamic_styles, resolve_style_config, DEFAULT_THEME
from app.services.generation_data_guard import evaluate_generation_data_relevance
from app.services.generation_data_service import (
    build_query_focused_context,
    extract_query_focused_result_text,
)

from .base import TaskHandler, TaskContext

# 基础模板路径
_BASE_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "static", "templates", "base_template.docx"
)

logger = logging.getLogger(__name__)


class CreationHandler(TaskHandler):
    """
    Creation 快速路径：LLM 生成 Markdown → 转换为 Word
    """
    
    async def handle(self, ctx: TaskContext) -> Dict[str, Any]:
        """
        执行 Creation 任务
        
        流程：
        1. 调用 LLM 生成 Markdown
        2. 使用 python-docx 转换为 Word
        3. 保存到沙盒
        """
        await self.on_step(ctx, "生成文档", "running", "正在生成文档内容...")
        
        llm = get_async_llm()
        settings = get_settings()
        
        # 构建数据上下文（生成模式优先使用完整检索文本，避免切片预览被截断）
        data_context = self._build_creation_context(ctx)

        relevance = await evaluate_generation_data_relevance(
            query=ctx.task_description,
            memory_dfs=ctx.memory_dfs,
            execution_results=ctx.execution_results,
            messages=ctx.messages,
        )
        if not relevance.get("is_relevant", True):
            reason = str(relevance.get("reason", "数据与问题不相关"))
            await self.on_step(ctx, "生成文档", "invalid_data", "数据无效，已跳过生成")
            return {
                "success": True,
                "output": f"数据无效，已跳过文档生成。原因：{reason}",
                "output_files": [],
                "quality_signal": {
                    "verdict": "pass",
                    "reason_code": "terminal_data_invalid",
                    "confidence": relevance.get("confidence", 0.9),
                    "retryable": False,
                },
                "meta": {
                    "worker_round": ctx.round_index,
                    "stop_reason": "terminal_data_invalid",
                    "step_status": "invalid_data",
                    "invalid_reason": reason,
                },
            }
        
        # 获取提示词
        system_prompt = get_prompt("creation_markdown_system", module="office")
        user_prompt = get_prompt(
            "creation_markdown_user",
            module="office",
            task_description=ctx.task_description,
            data_context=data_context
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            markdown_content = await llm.chat(messages, model=settings.llm.office_worker_model)
            
            # 清理 markdown 代码块包装
            if markdown_content.startswith("```"):
                lines = markdown_content.split("\n")
                if lines[-1].strip() == "```":
                    markdown_content = "\n".join(lines[1:-1])
                else:
                    markdown_content = "\n".join(lines[1:])
            
            logger.info(f"CreationHandler: LLM 生成 Markdown 成功, 长度={len(markdown_content)}")
            
            # 转换为 Word
            word_path = await self._markdown_to_docx(
                markdown_content,
                ctx.sandbox_path,
                ctx.task_description,
            )
            
            if word_path:
                filename = os.path.basename(word_path)
                download_url = f"/api/files/download/{ctx.session_id}/{filename}"
                
                await self.on_step(ctx, "生成文档", "done", "文档生成完成")

                # 保存到临时产物收纳箱（按 workspace_id + user_id 隔离）
                try:
                    from app.services.temp_artifact_service import get_temp_artifact_service
                    user_ctx = ctx.user_context or {}
                    workspace_id = str(user_ctx.get("workspace_id") or "default")
                    user_id = str(user_ctx.get("user_id") or ctx.user_id or "anonymous")
                    await get_temp_artifact_service().save_doc(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        session_id=ctx.session_id or "",
                        title=filename[:50],
                        download_url=download_url,
                        file_name=filename,
                        file_kind="word",
                    )
                except Exception as save_err:
                    logger.warning("CreationHandler: 暂存箱保存失败(非致命): %s", save_err)
                
                # [Bug Fix] 发送 FILE_RESULT SSE 事件，供前端更新 Artifact 的 download_url
                if ctx.session_id:
                    from app.api.events import emit_file_result
                    await emit_file_result(
                        session_id=ctx.session_id,
                        step_id=ctx.parent_step_id,
                        file_type="word",
                        file_name=filename,
                        download_url=download_url,
                        round_index=ctx.round_index
                    )
                
                return {
                    "success": True,
                    "output": f"已成功生成文档 [{filename}]({download_url})。",
                    "output_files": [{"name": filename, "path": word_path, "download_url": download_url}],
                    "quality_signal": {
                        "verdict": "pass",
                        "reason_code": "doc_generated",
                        "confidence": 0.9,
                        "retryable": False,
                    },
                    "meta": {
                        "worker_round": ctx.round_index,
                        "stop_reason": "doc_generated",
                    },
                }
            else:
                await self.on_step(ctx, "生成文档", "error", "Word 文档生成失败")
                return {
                    "success": False,
                    "error": "Word 文档生成失败",
                    "quality_signal": {
                        "verdict": "fail",
                        "reason_code": "doc_generation_error",
                        "confidence": 0.9,
                        "retryable": True,
                    },
                    "meta": {
                        "worker_round": ctx.round_index,
                        "stop_reason": "doc_generation_error",
                    },
                }
                
        except Exception as e:
            logger.error(f"CreationHandler 失败: {e}", exc_info=True)
            await self.on_step(ctx, "生成文档", "error", str(e)[:50])
            return {
                "success": False,
                "error": str(e),
                "quality_signal": {
                    "verdict": "fail",
                    "reason_code": "doc_generation_error",
                    "confidence": 0.9,
                    "retryable": True,
                },
                "meta": {
                    "worker_round": ctx.round_index,
                    "stop_reason": "doc_generation_error",
                },
            }
    
    async def _markdown_to_docx(
        self,
        markdown_content: str,
        sandbox_path: str,
        task_description: str,
        style_config: Dict[str, Any] | None = None,
    ) -> Optional[str]:
        """
        将 Markdown 转换为 Word 文档
        
        流程：加载基础模板 → 动态样式注入 → AST 解析写入内容
        """
        try:
            from docx import Document
        except ImportError:
            logger.error("python-docx 未安装")
            return None
        
        # 1. 加载基础模板（含预设样式、页边距、页脚）
        if os.path.exists(_BASE_TEMPLATE_PATH):
            doc = Document(_BASE_TEMPLATE_PATH)
            logger.info("CreationHandler: 加载基础模板 %s", _BASE_TEMPLATE_PATH)
        else:
            doc = Document()
            logger.warning("CreationHandler: 基础模板不存在，使用空文档")
        
        # 2. 动态样式注入
        resolved_config = resolve_style_config(
            user_config=style_config,
        )
        applied_config = apply_dynamic_styles(doc, resolved_config)
        
        # 3. Markdown 转换
        try:
            from app.core.utils.markdown_converter import convert_markdown_to_docx_elements
            convert_markdown_to_docx_elements(markdown_content, doc, style_config=applied_config)
            logger.info("CreationHandler: 使用 AST 解析转换 Markdown")
        except ImportError:
            logger.warning("markdown_converter 不可用，回退到简单解析")
            for line in markdown_content.split("\n"):
                if line.strip():
                    doc.add_paragraph(line)
        except Exception as e:
            logger.warning(f"AST 解析失败，回退到简单处理: {e}")
            for line in markdown_content.split("\n"):
                if line.strip():
                    doc.add_paragraph(line)
        
        # 提取文件名
        filename = self._extract_filename(task_description) or "生成的文档.docx"
        if not filename.endswith(".docx"):
            filename += ".docx"
        
        # 保存
        Path(sandbox_path).mkdir(parents=True, exist_ok=True)
        file_path = os.path.join(sandbox_path, filename)
        doc.save(file_path)
        
        logger.info(f"CreationHandler: Word 文档已保存到 {file_path}")
        return file_path
    
    def _add_table(self, doc, table_rows):
        """添加表格到文档"""
        if not table_rows:
            return
        cols = len(table_rows[0])
        table = doc.add_table(rows=len(table_rows), cols=cols)
        table.style = "Table Grid"
        for i, row_data in enumerate(table_rows):
            row = table.rows[i]
            for j, cell_text in enumerate(row_data):
                if j < len(row.cells):
                    row.cells[j].text = cell_text
    
    def _extract_filename(self, task_description: str) -> str:
        """从任务描述中提取文件名"""
        # 匹配 《xxx》 格式
        match = re.search(r"《(.+?)》", task_description)
        if match:
            return match.group(1)
        
        # 匹配 "生成xxx报告" 格式
        match = re.search(r"生成(.+?)(报告|文档|分析)", task_description)
        if match:
            return match.group(1) + match.group(2)
        
        return ""

    def _build_creation_context(self, ctx: TaskContext) -> str:
        """构建生成模式上下文，只保留与当前任务直接相关的数据。"""
        base_context = build_query_focused_context(
            query=ctx.task_description,
            memory_dfs=ctx.memory_dfs,
            context_title="筛选后的可用数据（只保留与任务直接相关的数据）",
            max_sources=3,
            max_rows=20,
            max_chars=18000,
            max_chars_per_source=4500,
        )

        result_text = extract_query_focused_result_text(
            query=ctx.task_description,
            execution_results=ctx.execution_results,
            max_blocks=8,
            max_chars=8000,
        )

        if not result_text:
            return base_context

        full_block = (
            "\n\n## 筛选后的检索片段（只保留与问题直接相关的内容）\n"
            f"{result_text}\n"
        )
        if base_context:
            return base_context + full_block
        return full_block
