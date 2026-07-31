"""
工具注册中心

职责：
- 集中管理所有可用工具
- 提供动态工具获取接口
- 新增工具只需在 TOOLS_MAP 注册，无需修改其他代码
"""
import logging
from typing import Dict, Optional

from langchain_core.tools import BaseTool

from app.tools.sql_tool import sql_tool
from app.tools.doc_tool import doc_tool
from app.tools.chart_tool import chart_tool
from app.tools.office_tool import office_tool
from app.tools.inspect_file_tool import inspect_file_tool

logger = logging.getLogger(__name__)


# ========== 工具注册表 ==========
# 新增工具只需在此添加，Executor 无需修改
TOOLS_MAP: Dict[str, BaseTool] = {
    "sql_worker": sql_tool,
    "doc_worker": doc_tool,
    "chart_worker": chart_tool,
    "office_worker": office_tool,
    "inspect_file": inspect_file_tool,  # 文件预览工具
}



def get_tool(name: str) -> Optional[BaseTool]:
    """
    根据名称获取工具
    
    Args:
        name: 工具名称（对应 worker 类型）
        
    Returns:
        工具实例，如果不存在返回 None
    """
    tool = TOOLS_MAP.get(name)
    if not tool:
        logger.warning(f"工具未找到: {name}, 可用工具: {list(TOOLS_MAP.keys())}")
    return tool


def list_tools() -> list[str]:
    """获取所有可用工具名称"""
    return list(TOOLS_MAP.keys())
