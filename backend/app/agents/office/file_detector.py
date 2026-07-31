"""
输出文件检测器

检测沙盒中新生成的输出文件
"""
import os
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


async def detect_output_files(
    mcp_manager,
    sandbox_path: str,
    session_id: str
) -> List[Dict[str, str]]:
    """
    检测沙盒中新生成的输出文件
    
    Returns:
        [{"name": "...", "path": "...", "download_url": "..."}]
    """
    list_tool = mcp_manager.get_tool("list_sandbox_files")
    if not list_tool:
        return []
    
    try:
        files_result = await list_tool.ainvoke({})
        if not files_result or files_result == "(空目录)":
            return []
        
        # 兼容 MCP 和本地工具的不同返回类型
        if isinstance(files_result, list):
            file_lines = files_result
        elif isinstance(files_result, str):
            file_lines = [line.strip() for line in files_result.split("\n") if line.strip()]
        else:
            logger.warning(f"未知的文件列表类型: {type(files_result)}")
            return []
        
        # 解析文件列表
        output_files = []
        for item in file_lines:
            # 处理 dict 或 string
            if isinstance(item, dict):
                filename = item.get('name', item.get('filename', ''))
            elif isinstance(item, str):
                # 提取文件名（可能包含大小信息）
                filename = item.split(" (")[0].strip()
            else:
                continue
            
            if not filename:
                continue
            
            # 只返回输出文件
            lower_name = filename.lower()
            is_output_file = (
                lower_name.startswith(("result", "output")) or 
                lower_name.endswith(('.png', '.jpg', '.pdf', '.docx', '.xlsx'))
            )
            if is_output_file:
                output_files.append({
                    "name": filename,
                    "path": os.path.join(sandbox_path, filename),
                    "download_url": f"/api/files/download/{session_id}/{filename}"
                })
        
        return output_files
        
    except Exception as e:
        logger.warning(f"检测输出文件失败: {e}")
        return []
