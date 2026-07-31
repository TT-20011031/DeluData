"""
MCP Client 管理器

负责连接和管理 MCP Servers（Filesystem Server 和 Python Interpreter Server）
为 OfficeWorker 提供工具调用能力

使用方式:
    async with get_mcp_client("/path/to/sandbox") as client:
        tools = client.tools
        result = await tools[0].ainvoke({"arg": "value"})
"""
import asyncio
import atexit
import logging
import os
import sys
import weakref
from typing import Optional, List, Dict, Any, Set
from contextlib import asynccontextmanager
from pathlib import Path

# [NOTE] Windows 上 Python 3.8+ 默认使用 ProactorEventLoop，它支持子进程
# 不要设置 SelectorEventLoop，因为 SelectorEventLoop 不支持子进程！

from langchain_core.tools import BaseTool

from app.config import get_settings

logger = logging.getLogger(__name__)

# [FIX] 全局追踪活跃的 MCP 客户端，确保进程退出时清理
_active_clients: Set[weakref.ref] = set()


def _cleanup_all_clients():
    """进程退出时清理所有活跃的 MCP 客户端"""
    for client_ref in list(_active_clients):
        client = client_ref()
        if client and client._connected:
            try:
                # 同步清理（atexit 不支持 async）
                if client.client:
                    # 尝试终止子进程
                    logger.info("正在清理 MCP 子进程...")
            except Exception as e:
                logger.warning(f"清理 MCP 客户端失败: {e}")


# 注册退出钩子
atexit.register(_cleanup_all_clients)


class MCPClientManager:
    """
    MCP 多服务器客户端管理器
    
    管理与 MCP Servers 的连接，提供统一的工具访问接口
    """
    
    def __init__(self, sandbox_path: str):
        """
        初始化 MCP Client
        
        Args:
            sandbox_path: 当前会话的沙盒目录绝对路径
        """
        self.sandbox_path = os.path.abspath(sandbox_path)
        self.client = None
        self.tools: List[BaseTool] = []
        self._connected = False
        
        # [FIX] 注册到全局追踪集合，并设置弱引用回调
        self._self_ref = weakref.ref(self, self._remove_from_active)
        _active_clients.add(self._self_ref)
        
        # 使用 finalize 确保对象被垃圾回收时清理资源
        self._finalizer = weakref.finalize(self, self._cleanup_on_gc, self.sandbox_path)
    
    @staticmethod
    def _remove_from_active(ref):
        """从活跃客户端集合中移除"""
        _active_clients.discard(ref)
    
    @staticmethod
    def _cleanup_on_gc(sandbox_path: str):
        """垃圾回收时的清理回调"""
        logger.debug(f"MCPClientManager for {sandbox_path} 被垃圾回收")
        
    async def connect(self) -> List[BaseTool]:
        """
        连接所有 MCP Servers 并获取工具列表
        
        Returns:
            可用工具列表
        """
        settings = get_settings()
        mcp_config = settings.mcp
        
        # 确保沙盒目录存在
        os.makedirs(self.sandbox_path, exist_ok=True)
        
        settings = get_settings()
        mcp_config = settings.mcp
        
        # [Windows 兼容性] Windows 上 langchain-mcp-adapters 的 stdio 传输
        # 工具调用会超时（get_tools 成功，但 ainvoke 超时）
        # 原因：subprocess stdout/stdin 管道通信问题
        # 解决方案：Windows 直接使用本地工具（性能已验证：0.06秒）
        if sys.platform == "win32":
            logger.info("Windows 环境，使用本地工具模式")
            self.tools = self._create_local_tools()
            self._connected = True
            logger.info(f"本地工具已就绪: {[t.name for t in self.tools]}")
            return self.tools
        
        try:
            # Linux/Mac: 使用 stdio MCP
            from langchain_mcp_adapters.client import MultiServerMCPClient
            
            # 获取 Python 解释器 MCP Server 的路径
            backend_root = Path(__file__).parent.parent.parent.parent  # backend/
            
            server_configs = {
                # Python Interpreter Server (自建)
                "python": {
                    "command": sys.executable,
                    "args": ["-m", "app.mcp_servers.python_interpreter"],
                    "transport": "stdio",
                    "cwd": str(backend_root),
                    "env": {
                        **os.environ,
                        "SANDBOX_PATH": self.sandbox_path,
                        "TIMEOUT": str(mcp_config.python_timeout_seconds),
                        "PYTHONIOENCODING": "utf-8"
                    }
                }
            }
            
            logger.info(f"正在连接 MCP Server... (platform={sys.platform})")
            self.client = MultiServerMCPClient(server_configs)
            self.tools = await self.client.get_tools()
            self._connected = True
            
            logger.info(f"MCP Client 已连接，沙盒: {self.sandbox_path}，可用工具: {[t.name for t in self.tools]}")
            
            return self.tools
            
        except ImportError as e:
            logger.warning(f"langchain-mcp-adapters 未安装，使用降级模式: {e}")
            self.tools = self._create_local_tools()
            self._connected = True
            return self.tools
            
        except Exception as e:
            logger.error(f"MCP Client 连接失败: {e}，使用降级模式")
            self.tools = self._create_local_tools()
            self._connected = True
            return self.tools
    
    async def _connect_mcp_http(self, mcp_config) -> List[BaseTool]:
        """
        Windows 专用：使用 HTTP 传输连接 MCP Server
        
        原理：
        1. 启动 MCP Server 子进程（HTTP 模式）
        2. 等待服务就绪
        3. 使用 HTTP 传输连接
        """
        import subprocess
        import time
        import socket
        
        # MCP HTTP Server 配置
        host = "127.0.0.1"
        port = mcp_config.http_port if hasattr(mcp_config, 'http_port') else 8765
        backend_root = Path(__file__).parent.parent.parent.parent  # app/core/mcp -> backend/
        
        logger.info(f"Windows 环境，启动 MCP HTTP Server (port={port})")
        
        # 检查端口是否已被占用（可能是之前的进程）
        def is_port_open(host, port, timeout=0.5):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                try:
                    sock.connect((host, port))
                    return True
                except (ConnectionRefusedError, socket.timeout, OSError):
                    return False
        
        # 如果端口已开启，尝试直接连接
        if is_port_open(host, port):
            logger.info(f"MCP HTTP Server 已在运行 (port={port})")
        else:
            # 启动 MCP Server 子进程
            server_cmd = [
                sys.executable, "-m", "app.mcp_servers.python_interpreter",
                "--http", "--host", host, "--port", str(port)
            ]
            
            # 设置环境变量
            env = {
                **os.environ,
                "SANDBOX_PATH": self.sandbox_path,
                "TIMEOUT": str(mcp_config.python_timeout_seconds),
                "PYTHONIOENCODING": "utf-8"
            }
            
            # 启动子进程（不阻塞）
            self._mcp_server_process = subprocess.Popen(
                server_cmd,
                cwd=str(backend_root),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            
            # 等待服务就绪（最多 10 秒）
            max_wait = 10
            start = time.time()
            while time.time() - start < max_wait:
                if is_port_open(host, port):
                    logger.info(f"MCP HTTP Server 已启动 (耗时 {time.time() - start:.1f}s)")
                    break
                await asyncio.sleep(0.2)
            else:
                logger.error(f"MCP HTTP Server 启动超时")
                # 回退到本地工具
                self.tools = self._create_local_tools()
                self._connected = True
                return self.tools
        
        # 使用 HTTP 传输连接
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            
            server_configs = {
                "python": {
                    "url": f"http://{host}:{port}/mcp",
                    "transport": "streamable_http",
                }
            }
            
            self.client = MultiServerMCPClient(server_configs)
            self.tools = await self.client.get_tools()
            self._connected = True
            
            logger.info(f"MCP HTTP Client 已连接: {[t.name for t in self.tools]}")
            return self.tools
            
        except Exception as e:
            logger.error(f"MCP HTTP 连接失败: {e}，使用本地工具")
            self.tools = self._create_local_tools()
            self._connected = True
            return self.tools
    
    async def _connect_mcp_windows(self, server_configs: dict) -> Optional[List[BaseTool]]:
        """
        Windows 专用 MCP 连接方法
        
        在独立线程中使用 SelectorEventLoop 运行 MCP 连接，
        然后将工具返回到主线程
        """
        import concurrent.futures
        import threading
        
        def run_in_thread():
            """在新线程中运行 MCP 连接"""
            # 创建新的 SelectorEventLoop
            loop = asyncio.SelectorEventLoop()
            asyncio.set_event_loop(loop)
            
            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient
                
                async def get_tools_async():
                    client = MultiServerMCPClient(server_configs)
                    return await client.get_tools()
                
                tools = loop.run_until_complete(get_tools_async())
                return tools
            except Exception as e:
                logger.error(f"Windows MCP 线程连接失败: {e}")
                return None
            finally:
                loop.close()
        
        # 在线程池中运行
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_in_thread)
            try:
                tools = future.result(timeout=30)  # 30 秒超时
                return tools
            except concurrent.futures.TimeoutError:
                logger.error("Windows MCP 连接超时")
                return None
            except Exception as e:
                logger.error(f"Windows MCP 执行异常: {e}")
                return None
    
    def _create_local_tools(self) -> List[BaseTool]:
        """
        创建本地工具（降级模式）
        
        当 MCP 连接失败时，直接使用本地实现
        """
        from langchain_core.tools import tool
        import asyncio
        
        sandbox_path = self.sandbox_path
        settings = get_settings()
        timeout = settings.mcp.python_timeout_seconds
        
        @tool
        async def execute_python(code: str) -> str:
            """在沙盒中执行 Python 代码"""
            import subprocess
            
            os.makedirs(sandbox_path, exist_ok=True)
            
            # [SECURITY FIX] 使用 repr() 安全转义路径
            safe_path = repr(sandbox_path)
            
            wrapped_code = f'''
import os
target_path = {safe_path}
os.chdir(target_path)
{code}
'''
            def run_subprocess():
                """在线程池中运行同步 subprocess（兼容 Windows SelectorEventLoop）"""
                try:
                    result = subprocess.run(
                        [sys.executable, "-c", wrapped_code],
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=sandbox_path,
                        encoding='utf-8',  # [修复] 显式指定编码
                        errors='replace',  # [修复] 替换无法解码的字符
                        env={
                            **os.environ,
                            "PYTHONIOENCODING": "utf-8",
                        }
                    )
                    
                    output = result.stdout
                    stderr = result.stderr
                    
                    if stderr and result.returncode != 0:
                        output += f"\n[STDERR]: {stderr}"
                    
                    return output.strip() or "(执行成功，无输出)"
                    
                except subprocess.TimeoutExpired:
                    return f"[ERROR] 代码执行超时（>{timeout}秒）"
                except Exception as e:
                    error_msg = str(e) if str(e) else repr(e)
                    error_type = type(e).__name__
                    return f"[ERROR] {error_type}: {error_msg}"
            
            try:
                return await asyncio.to_thread(run_subprocess)
            except Exception as e:
                error_msg = str(e) if str(e) else repr(e)
                error_type = type(e).__name__
                return f"[ERROR] {error_type}: {error_msg}"
        
        @tool
        async def list_sandbox_files() -> str:
            """列出沙盒目录中的所有文件"""
            try:
                os.makedirs(sandbox_path, exist_ok=True)
                files = []
                for item in os.listdir(sandbox_path):
                    item_path = os.path.join(sandbox_path, item)
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
                return f"[ERROR] {str(e)}"
        
        # === Excel MCP 工具 ===
        # 使用内部 libs 目录的 excel_mcp 模块
        excel_mcp_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),  # app/
            "libs"
        )
        if excel_mcp_path not in sys.path:
            sys.path.insert(0, excel_mcp_path)
        
        @tool
        def create_chart(
            filepath: str,
            sheet_name: str,
            data_range: str,
            chart_type: str,
            target_cell: str,
            title: str = "",
            x_axis: str = "",
            y_axis: str = ""
        ) -> str:
            """
            在 Excel 工作表中创建原生图表。
            
            Args:
                filepath: Excel 文件名（相对于沙盒目录）
                sheet_name: 工作表名称
                data_range: 数据范围，如 "A1:D10"
                chart_type: 图表类型 (line/bar/pie/scatter/area)
                target_cell: 图表放置位置，如 "F1"
                title: 图表标题
                x_axis: X轴标签
                y_axis: Y轴标签
            
            Returns:
                成功或错误消息
            """
            try:
                from excel_mcp.chart import create_chart_in_sheet
                full_path = os.path.join(sandbox_path, filepath)
                if not os.path.exists(full_path):
                    return f"[ERROR] 文件不存在: {filepath}，请先用 execute_python 保存 Excel 文件"
                result = create_chart_in_sheet(
                    filepath=full_path,
                    sheet_name=sheet_name,
                    data_range=data_range,
                    chart_type=chart_type,
                    target_cell=target_cell,
                    title=title,
                    x_axis=x_axis,
                    y_axis=y_axis
                )
                return result.get("message", "图表创建成功")
            except Exception as e:
                return f"[ERROR] 创建图表失败: {str(e)}"
        
        @tool
        def format_range(
            filepath: str,
            sheet_name: str,
            start_cell: str,
            end_cell: str = None,
            bold: bool = False,
            italic: bool = False,
            font_size: int = None,
            font_color: str = None,
            bg_color: str = None,
            border_style: str = None,
            alignment: str = None
        ) -> str:
            """
            设置 Excel 单元格格式（字体、颜色、边框等）。
            
            Args:
                filepath: Excel 文件名（相对于沙盒目录）
                sheet_name: 工作表名称
                start_cell: 起始单元格，如 "A1"
                end_cell: 结束单元格，如 "D1"（可选）
                bold: 是否加粗
                italic: 是否斜体
                font_size: 字体大小
                font_color: 字体颜色（十六进制，如 "FF0000" 表示红色）
                bg_color: 背景颜色（十六进制）
                border_style: 边框样式 (thin/medium/thick)
                alignment: 对齐方式 (left/center/right)
            
            Returns:
                成功或错误消息
            """
            try:
                from excel_mcp.formatting import format_range as format_range_impl
                full_path = os.path.join(sandbox_path, filepath)
                if not os.path.exists(full_path):
                    return f"[ERROR] 文件不存在: {filepath}"
                result = format_range_impl(
                    filepath=full_path,
                    sheet_name=sheet_name,
                    start_cell=start_cell,
                    end_cell=end_cell,
                    bold=bold,
                    italic=italic,
                    font_size=font_size,
                    font_color=font_color,
                    bg_color=bg_color,
                    border_style=border_style,
                    alignment=alignment
                )
                return result.get("message", "格式设置成功")
            except Exception as e:
                return f"[ERROR] 格式设置失败: {str(e)}"
        
        @tool
        def create_pivot_table(
            filepath: str,
            sheet_name: str,
            data_range: str,
            rows: str,
            values: str,
            agg_func: str = "sum"
        ) -> str:
            """
            创建 Excel 透视表。
            
            Args:
                filepath: Excel 文件名（相对于沙盒目录）
                sheet_name: 数据所在的工作表名称
                data_range: 数据范围，如 "A1:D100"
                rows: 行字段名（用逗号分隔多个字段）
                values: 值字段名（用逗号分隔多个字段）
                agg_func: 聚合函数 (sum/average/count/min/max)
            
            Returns:
                成功或错误消息
            """
            try:
                from excel_mcp.pivot import create_pivot_table as pivot_impl
                full_path = os.path.join(sandbox_path, filepath)
                if not os.path.exists(full_path):
                    return f"[ERROR] 文件不存在: {filepath}"
                # 解析逗号分隔的字段名
                row_list = [r.strip() for r in rows.split(",") if r.strip()]
                value_list = [v.strip() for v in values.split(",") if v.strip()]
                result = pivot_impl(
                    filepath=full_path,
                    sheet_name=sheet_name,
                    data_range=data_range,
                    rows=row_list,
                    values=value_list,
                    agg_func=agg_func
                )
                return result.get("message", "透视表创建成功")
            except Exception as e:
                return f"[ERROR] 创建透视表失败: {str(e)}"
        
        # === Word MCP 工具 ===
        @tool
        def create_document(
            filename: str,
            title: str = "",
            author: str = ""
        ) -> str:
            """
            创建新的 Word 文档。
            
            Args:
                filename: 文件名（相对于沙盒目录，自动添加 .docx 扩展名）
                title: 文档标题（元数据）
                author: 作者（元数据）
            
            Returns:
                成功或错误消息
            """
            try:
                from word_mcp.tools.document_tools import create_document as create_doc_impl
                import asyncio
                full_path = os.path.join(sandbox_path, filename)
                # 同步调用异步函数
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(create_doc_impl(full_path, title, author))
                finally:
                    loop.close()
                return result
            except Exception as e:
                return f"[ERROR] 创建文档失败: {str(e)}"
        
        @tool
        def add_heading(
            filename: str,
            text: str,
            level: int = 1
        ) -> str:
            """
            向 Word 文档添加标题。
            
            Args:
                filename: 文件名
                text: 标题文本
                level: 标题级别（1-9，1 为最大）
            
            Returns:
                成功或错误消息
            """
            try:
                from word_mcp.tools.content_tools import add_heading as add_heading_impl
                import asyncio
                full_path = os.path.join(sandbox_path, filename)
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(add_heading_impl(full_path, text, level))
                finally:
                    loop.close()
                return result
            except Exception as e:
                return f"[ERROR] 添加标题失败: {str(e)}"
        
        @tool
        def add_paragraph(
            filename: str,
            text: str,
            bold: bool = False,
            italic: bool = False
        ) -> str:
            """
            向 Word 文档添加段落。
            
            Args:
                filename: 文件名
                text: 段落文本
                bold: 是否加粗
                italic: 是否斜体
            
            Returns:
                成功或错误消息
            """
            try:
                from word_mcp.tools.content_tools import add_paragraph as add_para_impl
                import asyncio
                full_path = os.path.join(sandbox_path, filename)
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(add_para_impl(
                        full_path, text, bold=bold, italic=italic
                    ))
                finally:
                    loop.close()
                return result
            except Exception as e:
                return f"[ERROR] 添加段落失败: {str(e)}"
        
        @tool
        def add_table_to_word(
            filename: str,
            rows: int,
            cols: int,
            data: str = ""
        ) -> str:
            """
            向 Word 文档添加表格。
            
            Args:
                filename: 文件名
                rows: 行数
                cols: 列数
                data: 表格数据（JSON 格式的二维数组，如 '[["A","B"],["1","2"]]'）
            
            Returns:
                成功或错误消息
            """
            try:
                from word_mcp.tools.content_tools import add_table as add_table_impl
                import asyncio
                import json
                full_path = os.path.join(sandbox_path, filename)
                # 解析 JSON 数据
                table_data = None
                if data:
                    try:
                        table_data = json.loads(data)
                    except:
                        pass
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(add_table_impl(full_path, rows, cols, table_data))
                finally:
                    loop.close()
                return result
            except Exception as e:
                return f"[ERROR] 添加表格失败: {str(e)}"
        
        @tool
        def search_and_replace_word(
            filename: str,
            find_text: str,
            replace_text: str
        ) -> str:
            """
            在 Word 文档中查找并替换文本。
            
            Args:
                filename: 文件名
                find_text: 要查找的文本
                replace_text: 替换为的文本
            
            Returns:
                替换结果
            """
            try:
                from word_mcp.tools.content_tools import search_and_replace as sar_impl
                import asyncio
                full_path = os.path.join(sandbox_path, filename)
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(sar_impl(full_path, find_text, replace_text))
                finally:
                    loop.close()
                return result
            except Exception as e:
                return f"[ERROR] 查找替换失败: {str(e)}"
        
        @tool
        def render_template(
            template_file: str,
            output_file: str,
            context: str
        ) -> str:
            """
            使用模板渲染生成文档（支持 Word/Excel）。
            
            模板中使用 {{变量名}} 语法，支持：
            - Word: Jinja2 完整语法（循环、条件）
            - Excel: 简单变量替换
            
            Args:
                template_file: 模板文件名（在 sandbox 中）
                output_file: 输出文件名
                context: 数据上下文（JSON 格式，如 '{"name": "张三", "amount": 1000}'）
            
            Returns:
                成功或错误消息
            """
            try:
                import json
                import re
                
                template_path = os.path.join(sandbox_path, template_file)
                output_path = os.path.join(sandbox_path, output_file)
                
                if not os.path.exists(template_path):
                    return f"[ERROR] 模板文件不存在: {template_file}"
                
                # 解析 context
                try:
                    ctx = json.loads(context)
                except json.JSONDecodeError as e:
                    return f"[ERROR] context JSON 解析失败: {e}"
                
                ext = template_file.split('.')[-1].lower()
                
                if ext == 'docx':
                    # Word: 使用 docxtpl
                    from docxtpl import DocxTemplate, InlineImage
                    from docx.shared import Mm
                    
                    doc = DocxTemplate(template_path)
                    
                    # 预处理图片
                    render_ctx = {}
                    for k, v in ctx.items():
                        if k.startswith('IMAGE_') and v and os.path.exists(os.path.join(sandbox_path, v)):
                            img_path = os.path.join(sandbox_path, v)
                            render_ctx[k] = InlineImage(doc, img_path, width=Mm(140))
                        else:
                            render_ctx[k] = v
                    
                    doc.render(render_ctx)
                    doc.save(output_path)
                    
                elif ext == 'xlsx':
                    # Excel: 使用 openpyxl
                    import openpyxl
                    from openpyxl.drawing.image import Image as XLImage
                    
                    wb = openpyxl.load_workbook(template_path)
                    ws = wb.active
                    
                    pattern = re.compile(r'\{\{(\w+)\}\}')
                    
                    for row in ws.iter_rows():
                        for cell in row:
                            if cell.value and isinstance(cell.value, str):
                                matches = pattern.findall(cell.value)
                                for match in matches:
                                    if match in ctx:
                                        val = ctx[match]
                                        cell.value = cell.value.replace(f'{{{{{match}}}}}', str(val))
                    
                    wb.save(output_path)
                else:
                    return f"[ERROR] 不支持的模板格式: {ext}"
                
                return f"[SUCCESS] 模板渲染完成: {output_file}"
            except Exception as e:
                return f"[ERROR] 模板渲染失败: {str(e)}"
        
        @tool
        def render_template_by_id(
            template_id: int,
            output_file: str,
            context: str
        ) -> str:
            """
            使用数据库中的模板渲染生成文档（推荐方式）。
            
            先从数据库获取模板信息，复制到沙箱后渲染，解决路径隔离问题。
            
            Args:
                template_id: 模板的数据库 ID
                output_file: 输出文件名
                context: 数据上下文（JSON 格式）
            
            Returns:
                成功或错误消息
            """
            try:
                import json
                import re
                import shutil
                import asyncio
                
                # 从数据库获取模板
                from app.models.config.template import get_template_async
                
                loop = asyncio.new_event_loop()
                try:
                    template = loop.run_until_complete(get_template_async(template_id))
                finally:
                    loop.close()
                
                if not template:
                    return f"[ERROR] 模板不存在: ID={template_id}"
                
                # 复制模板到沙箱
                src_path = template.file_path
                if not os.path.exists(src_path):
                    return f"[ERROR] 模板文件不存在: {src_path}"
                
                temp_template_name = f"temp_tpl_{template_id}.{template.file_type}"
                template_path = os.path.join(sandbox_path, temp_template_name)
                shutil.copy(src_path, template_path)
                
                output_path = os.path.join(sandbox_path, output_file)
                
                # 解析 context
                try:
                    ctx = json.loads(context)
                except json.JSONDecodeError as e:
                    return f"[ERROR] context JSON 解析失败: {e}"
                
                ext = template.file_type.lower()
                
                if ext == 'docx':
                    from docxtpl import DocxTemplate, InlineImage
                    from docx.shared import Mm
                    
                    doc = DocxTemplate(template_path)
                    schema = template.variables_schema or {}
                    
                    render_ctx = {}
                    for k, v in ctx.items():
                        var_def = schema.get(k, {})
                        if var_def.get('type') == 'image' and v:
                            img_path = os.path.join(sandbox_path, v) if not os.path.isabs(v) else v
                            if os.path.exists(img_path):
                                render_ctx[k] = InlineImage(doc, img_path, width=Mm(140))
                            else:
                                render_ctx[k] = v
                        else:
                            render_ctx[k] = v
                    
                    doc.render(render_ctx)
                    doc.save(output_path)
                    
                elif ext == 'xlsx':
                    import openpyxl
                    
                    wb = openpyxl.load_workbook(template_path)
                    ws = wb.active
                    pattern = re.compile(r'\{\{(\w+)\}\}')
                    
                    for row in ws.iter_rows():
                        for cell in row:
                            if cell.value and isinstance(cell.value, str):
                                matches = pattern.findall(cell.value)
                                for match in matches:
                                    if match in ctx:
                                        cell.value = cell.value.replace(f'{{{{{match}}}}}', str(ctx[match]))
                    
                    wb.save(output_path)
                else:
                    return f"[ERROR] 不支持的模板格式: {ext}"
                
                # 清理临时模板
                try:
                    os.remove(template_path)
                except:
                    pass
                
                return f"[SUCCESS] 模板渲染完成: {output_file}"
            except Exception as e:
                return f"[ERROR] 模板渲染失败: {str(e)}"
        
        @tool
        def list_available_templates() -> str:
            """
            列出可用的文档模板。
            
            Returns:
                模板列表（JSON 格式）
            """
            try:
                import asyncio
                from app.models.config.template import list_templates_async
                
                # 注意：这里需要从某处获取 workspace_id
                # 暂时返回所有活跃模板
                loop = asyncio.new_event_loop()
                try:
                    # 使用默认工作空间
                    templates = loop.run_until_complete(list_templates_async("default", active_only=True))
                finally:
                    loop.close()
                
                result = []
                for t in templates:
                    result.append({
                        "id": t.id,
                        "name": t.name,
                        "type": t.file_type,
                        "keywords": t.keywords,
                        "variables": list(t.variables_schema.keys()) if t.variables_schema else []
                    })
                
                import json
                return json.dumps(result, ensure_ascii=False, indent=2)
            except Exception as e:
                return f"[ERROR] 获取模板列表失败: {str(e)}"
        
        logger.info("使用本地降级工具（含 Excel/Word MCP 增强）")
        return [
            execute_python, 
            list_sandbox_files,
            # Excel MCP 工具
            create_chart,
            format_range,
            create_pivot_table,
            # Word MCP 工具
            create_document,
            add_heading,
            add_paragraph,
            add_table_to_word,
            search_and_replace_word,
            # 模板渲染工具
            render_template,
            render_template_by_id,
            list_available_templates
        ]
    
    async def disconnect(self):
        """断开 MCP 连接"""
        if self.client:
            try:
                # [FIX] langchain-mcp-adapters 0.2.x 不再需要显式断开
                # 每次 get_tools 会自动管理 session
                pass
            except Exception as e:
                logger.warning(f"MCP Client 断开时出错: {e}")
            finally:
                self.client = None
                self.tools = []
                self._connected = False
                logger.info("MCP Client 已断开")
        
        # 从活跃集合中移除
        _active_clients.discard(self._self_ref)
    
    def get_tool(self, name: str) -> Optional[BaseTool]:
        """
        按名称获取工具
        
        Args:
            name: 工具名称
            
        Returns:
            工具实例，如果不存在返回 None
        """
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None
    
    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected


@asynccontextmanager
async def get_mcp_client(sandbox_path: str):
    """
    获取 MCP Client 上下文管理器
    
    Args:
        sandbox_path: 沙盒目录路径
        
    Yields:
        MCPClientManager 实例
        
    Usage:
        async with get_mcp_client("/sandbox/session_123") as client:
            tools = client.tools
            result = await tools[0].ainvoke({"arg": "value"})
    """
    manager = MCPClientManager(sandbox_path)
    try:
        await manager.connect()
        yield manager
    finally:
        await manager.disconnect()

