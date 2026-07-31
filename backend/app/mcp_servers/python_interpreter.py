"""
Python Interpreter MCP Server

安全执行 Python 代码的 MCP Server
预装数据处理库：pandas, openpyxl, matplotlib 等

使用方式:
    python -m app.mcp_servers.python_interpreter
"""
import os
import sys

# [NOTE] Windows 上 Python 3.8+ 默认使用 ProactorEventLoop，支持子进程
# 不要设置 SelectorEventLoop，它不支持子进程！
import asyncio

import subprocess
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("Python-Interpreter")

# 从环境变量获取沙盒路径和超时时间
SANDBOX_PATH = os.environ.get("SANDBOX_PATH", os.path.join(os.getcwd(), "data", "sandbox"))
TIMEOUT = int(os.environ.get("TIMEOUT", "60"))


@mcp.tool()
async def execute_python(code: str) -> str:
    """
    在沙盒中执行 Python 代码
    
    Args:
        code: 要执行的 Python 代码
        
    Returns:
        执行结果（stdout + stderr）
    
    注意:
        - 代码在 SANDBOX_PATH 目录下执行
        - 预装库: pandas, openpyxl, xlsxwriter, python-docx, matplotlib, seaborn
        - 超时限制: 默认 60 秒
    """
    import asyncio
    import subprocess
    
    # 确保沙盒目录存在
    os.makedirs(SANDBOX_PATH, exist_ok=True)
    
    # [SECURITY FIX] 使用 repr() 安全转义路径，防止代码注入
    safe_path = repr(SANDBOX_PATH)
    
    # 注入沙盒路径作为工作目录
    wrapped_code = f'''
import os
import sys

# 设置工作目录 (使用 repr 转义的安全路径)
target_path = {safe_path}
os.chdir(target_path)

# 添加到 Python 路径
sys.path.insert(0, target_path)

# 用户代码开始
{code}
'''
    
    def run_subprocess():
        """在线程池中运行同步 subprocess（兼容 Windows SelectorEventLoop）"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", wrapped_code],
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                cwd=SANDBOX_PATH,
                encoding='utf-8',  # [FIX] 显式指定 UTF-8 编码
                errors='replace',  # [FIX] 遇到无法解码的字符时替换，避免崩溃
                env={
                    **os.environ,
                    "PYTHONIOENCODING": "utf-8",
                }
            )
            
            output = result.stdout
            stderr = result.stderr
            
            if stderr and result.returncode != 0:
                stderr_clean = stderr.strip()
                if stderr_clean:
                    output += f"\n[STDERR]: {stderr_clean}"
            
            if result.returncode != 0:
                output = f"[EXIT CODE: {result.returncode}]\n{output}"
            
            return output.strip() or "(执行成功，无输出)"
            
        except subprocess.TimeoutExpired:
            return f"[ERROR] 代码执行超时（>{TIMEOUT}秒），请简化代码或减少数据量"
        except Exception as e:
            import traceback
            error_msg = str(e) if str(e) else repr(e)
            error_type = type(e).__name__
            tb = traceback.format_exc()
            sys.stderr.write(f"[MCP Error] {error_type}: {error_msg}\n{tb}\n")
            sys.stderr.flush()
            return f"[ERROR] {error_type}: {error_msg}"
    
    # [关键修复] 使用 asyncio.to_thread 在线程池中运行
    # 这样既不阻塞 Event Loop，又兼容 Windows SelectorEventLoop
    try:
        return await asyncio.to_thread(run_subprocess)
    except Exception as e:
        import traceback
        error_msg = str(e) if str(e) else repr(e)
        error_type = type(e).__name__
        return f"[ERROR] {error_type}: {error_msg}"


@mcp.tool()
async def list_sandbox_files() -> str:
    """
    列出沙盒目录中的所有文件
    
    Returns:
        文件列表，每行一个文件名
    """
    try:
        os.makedirs(SANDBOX_PATH, exist_ok=True)
        
        files = []
        for item in os.listdir(SANDBOX_PATH):
            item_path = os.path.join(SANDBOX_PATH, item)
            if os.path.isfile(item_path):
                size = os.path.getsize(item_path)
                if size < 1024:
                    size_str = f"{size}B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size / 1024 / 1024:.1f}MB"
                files.append(f"{item} ({size_str})")
            else:
                files.append(f"{item}/")
        
        if not files:
            return "(空目录)"
        
        return "\n".join(sorted(files))
        
    except Exception as e:
        return f"[ERROR] 无法列出文件: {str(e)}"


@mcp.tool()
async def get_file_preview(filename: str, max_lines: int = 20) -> str:
    """
    预览文件内容（仅支持文本文件）
    
    Args:
        filename: 文件名
        max_lines: 最大预览行数，默认 20
        
    Returns:
        文件内容预览
    """
    try:
        file_path = os.path.join(SANDBOX_PATH, filename)
        
        # 安全检查：确保文件在沙盒内
        real_path = os.path.realpath(file_path)
        sandbox_real = os.path.realpath(SANDBOX_PATH)
        if not real_path.startswith(sandbox_real):
            return "[ERROR] 非法路径访问"
        
        if not os.path.exists(file_path):
            return f"[ERROR] 文件不存在: {filename}"
        
        # 检查文件扩展名
        ext = os.path.splitext(filename)[1].lower()
        binary_exts = ['.xlsx', '.xls', '.docx', '.doc', '.pdf', '.png', '.jpg', '.jpeg', '.gif']
        
        if ext in binary_exts:
            return f"[INFO] {filename} 是二进制文件，请使用 execute_python 配合 pandas/openpyxl 读取"
        
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        
        if len(lines) > max_lines:
            preview = ''.join(lines[:max_lines])
            return f"{preview}\n... (共 {len(lines)} 行，显示前 {max_lines} 行)"
        
        return ''.join(lines)
        
    except Exception as e:
        return f"[ERROR] 读取失败: {str(e)}"


def run_http_server(host: str = "127.0.0.1", port: int = 8765):
    """
    以 HTTP 模式运行 MCP Server
    
    Windows 上使用 HTTP 传输可绕过 stdio 管道超时问题
    """
    import sys
    sys.stderr.write(f"[Python Interpreter MCP Server - HTTP Mode]\n")
    sys.stderr.write(f"Sandbox Path: {SANDBOX_PATH}\n")
    sys.stderr.write(f"Timeout: {TIMEOUT}s\n")
    sys.stderr.write(f"Listening on: http://{host}:{port}/mcp\n")
    sys.stderr.flush()
    
    mcp.run(transport="http", host=host, port=port)


def run_stdio_server():
    """
    以 stdio 模式运行 MCP Server（Linux/Mac 默认）
    """
    import sys
    sys.stderr.write(f"[Python Interpreter MCP Server - STDIO Mode]\n")
    sys.stderr.write(f"Sandbox Path: {SANDBOX_PATH}\n")
    sys.stderr.write(f"Timeout: {TIMEOUT}s\n")
    sys.stderr.flush()
    
    mcp.run()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Python Interpreter MCP Server")
    parser.add_argument("--http", action="store_true", help="使用 HTTP 传输（Windows 推荐）")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 监听端口")
    
    args = parser.parse_args()
    
    if args.http:
        run_http_server(args.host, args.port)
    else:
        run_stdio_server()
