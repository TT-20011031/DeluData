# MCP (Model Context Protocol) module
# 使用惰性导入避免循环依赖，外部应直接从子模块导入
# 例如: from app.core.mcp.mcp_client import get_mcp_client

__all__ = [
    "mcp_client",
]
