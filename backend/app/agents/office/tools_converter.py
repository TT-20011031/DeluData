"""
工具格式转换器

将 LangChain BaseTool 转换为 OpenAI 兼容的工具定义格式
"""
from typing import List
from langchain_core.tools import BaseTool


def convert_tools_to_openai_format(tools: List[BaseTool]) -> List[dict]:
    """
    将 LangChain BaseTool 转换为 OpenAI 兼容的工具定义格式
    
    Returns:
        [{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}]
    """
    openai_tools = []
    
    for tool in tools:
        # 获取参数 schema
        if hasattr(tool, 'args_schema') and tool.args_schema:
            try:
                # Pydantic v2
                if hasattr(tool.args_schema, 'model_json_schema'):
                    parameters = tool.args_schema.model_json_schema()
                # Pydantic v1
                elif hasattr(tool.args_schema, 'schema'):
                    parameters = tool.args_schema.schema()
                else:
                    parameters = {"type": "object", "properties": {}}
            except Exception:
                parameters = {"type": "object", "properties": {}}
        else:
            # 没有 schema，构建默认参数
            if tool.name == "execute_python":
                parameters = {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "要执行的 Python 代码"
                        }
                    },
                    "required": ["code"]
                }
            elif tool.name == "list_sandbox_files":
                parameters = {
                    "type": "object",
                    "properties": {}
                }
            else:
                parameters = {"type": "object", "properties": {}}
        
        # 移除 Pydantic schema 中的元数据字段
        for key in ['title', 'definitions', '$defs']:
            parameters.pop(key, None)
        
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or f"工具: {tool.name}",
                "parameters": parameters
            }
        })
    
    return openai_tools
