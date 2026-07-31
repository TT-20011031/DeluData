"""
Inspect File Tool - 文件预览工具

用于预执行阶段，读取文件的前几行和列名，帮助 Planner 更好地理解文件结构。
遵循设计说明.md 原则：模块化设计，异步操作。
"""
import os
from typing import Optional, Dict, Any, List
from langchain_core.tools import tool
import logging

from app.tools.base import BaseToolInput, WorkerResult

logger = logging.getLogger(__name__)


class InspectFileToolInput(BaseToolInput):
    """Inspect File 工具输入"""
    file_path: str = ""
    preview_rows: int = 5  # 预览行数
    

@tool(args_schema=InspectFileToolInput)
async def inspect_file_tool(
    query: str,
    user_id: str = "default_user",
    session_id: str = "",
    parent_step_id: str = "",
    user_context: Optional[Dict[str, Any]] = None,
    messages: Optional[list] = None,
    memory_dfs: Optional[Dict[str, Any]] = None,
    file_path: str = "",
    preview_rows: int = 5
) -> WorkerResult:
    """
    预览文件结构（前几行和列名）
    
    支持的文件类型：
    - Excel (.xlsx, .xls)
    - CSV (.csv)
    - Word (.docx) - 返回前几段文字
    
    Args:
        query: 预览任务描述
        file_path: 文件路径
        preview_rows: 预览行数（默认5行）
        
    Returns:
        WorkerResult: 包含文件结构信息
    """
    # 从 user_context 获取文件路径（如果未直接传入）
    if not file_path and user_context:
        file_context = user_context.get("file_context", {})
        file_path = file_context.get("file_path", "")
    
    if not file_path:
        return WorkerResult(
            output="❌ 未指定文件路径",
            artifacts={"error": "no_file_path"}
        )
    
    if not os.path.exists(file_path):
        return WorkerResult(
            output=f"❌ 文件不存在: {file_path}",
            artifacts={"error": "file_not_found"}
        )
    
    file_ext = os.path.splitext(file_path)[1].lower()
    file_name = os.path.basename(file_path)
    
    try:
        if file_ext in ['.xlsx', '.xls']:
            result = await _preview_excel(file_path, preview_rows)
        elif file_ext == '.csv':
            result = await _preview_csv(file_path, preview_rows)
        elif file_ext == '.docx':
            result = await _preview_docx(file_path, preview_rows)
        else:
            return WorkerResult(
                output=f"⚠️ 不支持预览的文件类型: {file_ext}",
                artifacts={"error": "unsupported_file_type", "file_ext": file_ext}
            )
        
        # 格式化输出
        output = f"## 📄 文件预览: {file_name}\n\n"
        output += f"**文件类型**: {result.get('file_type', file_ext)}\n"
        
        if result.get("columns"):
            output += f"**列数**: {len(result['columns'])}\n"
            output += f"**列名**: {', '.join(result['columns'][:20])}"
            if len(result['columns']) > 20:
                output += f" ... (共{len(result['columns'])}列)"
            output += "\n"
        
        if result.get("row_count") is not None:
            output += f"**总行数**: {result['row_count']}\n"
        
        output += f"\n### 数据预览 (前{preview_rows}行)\n\n"
        
        if result.get("preview_data"):
            # 转为 Markdown 表格
            data = result["preview_data"]
            if data and isinstance(data, list) and len(data) > 0:
                headers = result.get("columns", list(data[0].keys()) if isinstance(data[0], dict) else [])
                output += "| " + " | ".join(str(h)[:20] for h in headers) + " |\n"
                output += "| " + " | ".join(["---"] * len(headers)) + " |\n"
                for row in data[:preview_rows]:
                    if isinstance(row, dict):
                        values = [str(row.get(h, ""))[:30] for h in headers]
                    else:
                        values = [str(v)[:30] for v in row]
                    output += "| " + " | ".join(values) + " |\n"
        elif result.get("preview_text"):
            output += result["preview_text"]
        
        logger.info(f"文件预览成功: {file_name}, 列数: {len(result.get('columns', []))}")
        
        return WorkerResult(
            output=output,
            artifacts={
                "file_name": file_name,
                "file_path": file_path,
                "file_type": result.get("file_type"),
                "columns": result.get("columns", []),
                "row_count": result.get("row_count"),
                "preview_rows": preview_rows
            }
        )
        
    except Exception as e:
        logger.error(f"文件预览失败: {e}")
        return WorkerResult(
            output=f"❌ 文件预览失败: {str(e)}",
            artifacts={"error": str(e)}
        )


async def _preview_excel(file_path: str, preview_rows: int) -> Dict[str, Any]:
    """预览 Excel 文件"""
    import pandas as pd
    
    # 异步读取（使用线程池）
    import asyncio
    loop = asyncio.get_event_loop()
    
    def read_excel():
        df = pd.read_excel(file_path, nrows=preview_rows + 1)
        return {
            "file_type": "Excel",
            "columns": df.columns.tolist(),
            "row_count": len(pd.read_excel(file_path)),
            "preview_data": df.head(preview_rows).to_dict(orient="records")
        }
    
    return await loop.run_in_executor(None, read_excel)


async def _preview_csv(file_path: str, preview_rows: int) -> Dict[str, Any]:
    """预览 CSV 文件"""
    import pandas as pd
    import asyncio
    
    loop = asyncio.get_event_loop()
    
    def read_csv():
        # 尝试检测编码
        encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1']
        df = None
        for encoding in encodings:
            try:
                df = pd.read_csv(file_path, nrows=preview_rows + 1, encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        
        if df is None:
            raise ValueError("无法识别文件编码")
        
        # 获取总行数
        with open(file_path, 'r', encoding=encoding) as f:
            total_rows = sum(1 for _ in f) - 1  # 减去标题行
        
        return {
            "file_type": "CSV",
            "columns": df.columns.tolist(),
            "row_count": total_rows,
            "preview_data": df.head(preview_rows).to_dict(orient="records")
        }
    
    return await loop.run_in_executor(None, read_csv)


async def _preview_docx(file_path: str, preview_rows: int) -> Dict[str, Any]:
    """预览 Word 文件"""
    import asyncio
    
    loop = asyncio.get_event_loop()
    
    def read_docx():
        from docx import Document
        doc = Document(file_path)
        
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        preview_text = "\n\n".join(paragraphs[:preview_rows])
        
        return {
            "file_type": "Word",
            "row_count": len(paragraphs),
            "preview_text": preview_text
        }
    
    return await loop.run_in_executor(None, read_docx)
