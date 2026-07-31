# LangChain & LangGraph MCP 工具使用说明

这是一个 MCP (Model Context Protocol) 服务器，旨在帮助模型阅读和参考 LangChain 与 LangGraph 的官方文档。

## 功能介绍

- `search_docs(query)`: 在文档中搜索关键词并返回相关页面的路径。
- `fetch_langchain_doc(path)`: 读取指定路径的 LangChain 文档（输入相对路径，如 `oss/python/langchain/overview`）。
- `fetch_langgraph_doc(path)`: 读取指定路径的 LangGraph 文档（输入相对路径，如 `oss/python/langgraph/overview`）。

## 安装与配置

### 1. 安装依赖
确保已安装 Python，并在项目目录下运行：
```bash
pip install mcp httpx beautifulsoup4 markdownify
```

### 2. 集成到 Claude Desktop (Windows)
编辑你的 MCP 配置文件（通常位于 `%APPDATA%\Claude\claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "langchain-docs": {
      "command": "python",
      "args": ["d:/DeLu/DeLuData/DeluData/mcp-langchain/server.py"]
    }
  }
}
```

### 3. 集成到 Windsurf / Cursor
在对应的 MCP 配置界面添加一个新的 MCP 服务器：
- **Name**: langchain-docs
- **Type**: command
- **Command**: `python d:/DeLu/DeLuData/DeluData/mcp-langchain/server.py`

## 使用技巧
如果你不知道具体路径，可以先调用 `search_docs` 搜索关键词（如 "agent", "state", "graph"），然后再使用 `fetch_*` 工具读取感兴趣的页面。
