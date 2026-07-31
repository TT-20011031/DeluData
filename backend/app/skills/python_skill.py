"""
DeluData 智能问数系统 - PythonSkill 代码解释器技能

职责：
1. 在安全沙箱中执行 Python 代码
2. 支持 DataFrame 上下文注入
3. 禁止危险操作
"""
from typing import Dict, Any, List, Optional, Set
import re
import ast
import traceback
from io import StringIO
import sys
import pandas as pd
import numpy as np

from app.skills.base import BaseSkill
from app.models.common.execution import ExecutionResult


class CodeSecurityError(Exception):
    """代码安全检查错误"""
    pass


class PythonSkill(BaseSkill):
    """
    Python 代码解释器技能
    
    在受限环境中执行 Python 代码，用于数据清洗、计算和分析
    """
    
    # 禁止导入的模块
    FORBIDDEN_MODULES: Set[str] = {
        'os', 'sys', 'subprocess', 'shutil', 'socket',
        'requests', 'urllib', 'http', 'ftplib', 'smtplib',
        'pickle', 'marshal', 'shelve',
        'ctypes', 'multiprocessing', 'threading',
        '__builtin__', 'builtins'
    }
    
    # 禁止的函数/属性
    FORBIDDEN_NAMES: Set[str] = {
        'eval', 'exec', 'compile', 'open', 'input',
        '__import__', 'getattr', 'setattr', 'delattr',
        'globals', 'locals', 'vars',
        '__class__', '__bases__', '__subclasses__', '__mro__'
    }
    
    # 允许的内置函数
    SAFE_BUILTINS: Dict[str, Any] = {
        'abs': abs,
        'all': all,
        'any': any,
        'bool': bool,
        'dict': dict,
        'enumerate': enumerate,
        'filter': filter,
        'float': float,
        'format': format,
        'frozenset': frozenset,
        'int': int,
        'isinstance': isinstance,
        'len': len,
        'list': list,
        'map': map,
        'max': max,
        'min': min,
        'pow': pow,
        'print': print,
        'range': range,
        'reversed': reversed,
        'round': round,
        'set': set,
        'slice': slice,
        'sorted': sorted,
        'str': str,
        'sum': sum,
        'tuple': tuple,
        'type': type,
        'zip': zip,
        'True': True,
        'False': False,
        'None': None,
    }
    
    def __init__(self, timeout: int = 30):
        """
        初始化 PythonSkill
        
        Args:
            timeout: 执行超时时间（秒）
        """
        super().__init__(name="PythonSkill")
        self.timeout = timeout
    
    async def execute(
        self,
        code: str,
        dataframes: Optional[Dict[str, pd.DataFrame]] = None
    ) -> ExecutionResult:
        """
        执行 Python 代码
        
        Args:
            code: Python 代码
            dataframes: 要注入的 DataFrame 字典
            
        Returns:
            执行结果
        """
        return await self.exec_python(code, dataframes or {})
    
    async def exec_python(
        self,
        code: str,
        dataframes: Dict[str, pd.DataFrame]
    ) -> ExecutionResult:
        """
        在安全沙箱中执行 Python 代码
        
        Args:
            code: Python 代码
            dataframes: DataFrame 上下文
            
        Returns:
            执行结果
        """
        self.log_execution("执行代码", f"代码长度: {len(code)}, DataFrame: {list(dataframes.keys())}")
        
        logs = []
        
        try:
            # 1. 安全检查
            self._validate_code(code)
            logs.append("[安全检查] 通过")
            
            # 2. 构建执行环境
            exec_globals = self._build_safe_globals()
            exec_locals = self._build_exec_locals(dataframes)
            
            # 3. 捕获输出
            old_stdout = sys.stdout
            sys.stdout = StringIO()
            
            try:
                # 4. 执行代码
                exec(code, exec_globals, exec_locals)
                
                # 获取输出
                output = sys.stdout.getvalue()
                logs.append(f"[输出]\n{output}" if output else "[无输出]")
                
            finally:
                sys.stdout = old_stdout
            
            # 5. 提取结果
            result = exec_locals.get('result', None)
            new_dfs = self._extract_dataframes(exec_locals, dataframes.keys())
            
            self.log_execution("执行成功", f"新 DataFrame: {list(new_dfs.keys())}")
            
            return ExecutionResult(
                success=True,
                output=result,
                dataframes=new_dfs,
                logs=logs
            )
            
        except CodeSecurityError as e:
            self.log_execution("安全检查失败", str(e))
            return ExecutionResult(
                success=False,
                error=f"代码安全检查失败: {e}",
                logs=logs
            )
            
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            self.log_error(e, "exec_python")
            return ExecutionResult(
                success=False,
                error=error_msg,
                logs=logs
            )
    
    def _validate_code(self, code: str):
        """
        验证代码安全性
        
        Args:
            code: Python 代码
            
        Raises:
            CodeSecurityError: 检测到不安全代码
        """
        # 检查危险模块导入
        import_pattern = r'(?:from|import)\s+(\w+)'
        imports = re.findall(import_pattern, code)
        
        for module in imports:
            if module in self.FORBIDDEN_MODULES:
                raise CodeSecurityError(f"禁止导入模块: {module}")
        
        # AST 分析
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise CodeSecurityError(f"语法错误: {e}")
        
        for node in ast.walk(tree):
            # 检查函数调用
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in self.FORBIDDEN_NAMES:
                        raise CodeSecurityError(f"禁止调用: {node.func.id}")
            
            # 检查属性访问 (禁止 __xxx__)
            if isinstance(node, ast.Attribute):
                if node.attr.startswith('__') and node.attr.endswith('__'):
                    if node.attr not in ('__len__', '__str__', '__repr__'):
                        raise CodeSecurityError(f"禁止访问: {node.attr}")
            
            # 检查名称
            if isinstance(node, ast.Name):
                if node.id in self.FORBIDDEN_NAMES:
                    raise CodeSecurityError(f"禁止使用: {node.id}")
    
    def _build_safe_globals(self) -> Dict[str, Any]:
        """构建安全的全局命名空间"""
        return {
            '__builtins__': self.SAFE_BUILTINS,
            'pd': pd,
            'np': np,
            'DataFrame': pd.DataFrame,
            'Series': pd.Series,
        }
    
    def _build_exec_locals(
        self, 
        dataframes: Dict[str, pd.DataFrame]
    ) -> Dict[str, Any]:
        """
        构建执行的本地命名空间
        
        注入 DataFrame 到执行环境
        """
        locals_dict = {}
        
        for key, df in dataframes.items():
            if isinstance(df, pd.DataFrame):
                locals_dict[key] = df.copy()
            else:
                # 尝试转换为 DataFrame
                try:
                    locals_dict[key] = pd.DataFrame(df)
                except:
                    locals_dict[key] = df
        
        return locals_dict
    
    def _extract_dataframes(
        self,
        exec_locals: Dict[str, Any],
        original_keys: set
    ) -> Dict[str, pd.DataFrame]:
        """
        从执行结果中提取新的 DataFrame
        
        Args:
            exec_locals: 执行后的本地变量
            original_keys: 原始注入的 key
            
        Returns:
            新的或修改过的 DataFrame
        """
        new_dfs = {}
        
        for key, value in exec_locals.items():
            if isinstance(value, pd.DataFrame):
                # 包含原有的（可能被修改）和新创建的
                new_dfs[key] = value
        
        return new_dfs


# LangGraph 节点函数
async def python_worker_node(state, config: Dict):
    """
    LangGraph PythonWorker 节点
    
    Args:
        state: 当前状态
        config: 配置
        
    Returns:
        更新后的状态
    """
    from app.agents.state import AgentState
    from app.api.events import emit_step_update, emit_thinking_log
    from app.core.llm.llm import get_llm_client
    
    session_id = config.get("configurable", {}).get("session_id", "default")
    
    await emit_step_update(
        session_id,
        step_id="python_worker",
        status="running",
        label="正在生成 Python 代码..."
    )
    
    # 获取当前任务
    plan = state.get("plan", [])
    current_task = None
    for task in plan:
        if task.get("status") == "pending" and task.get("type") == "python":
            current_task = task
            break
    
    if not current_task:
        return state
    
    task_desc = current_task.get("description", "")
    memory_dfs = state.get("memory_dfs", {})
    
    # 使用 LLM 生成代码
    llm = get_llm_client()
    
    df_info = []
    for key, df in memory_dfs.items():
        if isinstance(df, pd.DataFrame):
            df_info.append(f"- {key}: 列={df.columns.tolist()}, 形状={df.shape}")
    
    prompt = f"""可用的 DataFrame:
{chr(10).join(df_info) if df_info else '无'}

任务: {task_desc}

请生成 Python 代码完成任务。
注意：
1. 可直接使用上述 DataFrame 变量名
2. 将最终结果赋值给 `result` 变量
3. 如果创建新的 DataFrame，请用有意义的变量名
4. 只返回代码，不要解释

```python
"""
    
    try:
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="你是 Python 数据分析专家。只返回代码，不要解释。",
            temperature=0.3
        )
        
        # 提取代码
        code = response.strip()
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        code = code.strip()
        
        await emit_thinking_log(session_id, f"[PythonWorker] 生成代码:\n{code}")
        
        # 执行代码
        await emit_step_update(
            session_id,
            step_id="python_worker",
            status="running",
            label="正在执行 Python 代码..."
        )
        
        skill = PythonSkill()
        result = await skill.exec_python(code, memory_dfs)
        
        # 更新状态
        new_state = dict(state)
        
        # 更新任务状态
        new_plan = []
        for task in plan:
            if task.get("id") == current_task.get("id"):
                task = dict(task)
                task["status"] = "completed" if result.success else "failed"
                task["result"] = {
                    "success": result.success,
                    "output": str(result.output) if result.output else None,
                    "error": result.error
                }
            new_plan.append(task)
        new_state["plan"] = new_plan
        
        # 更新 Blackboard
        if result.success and result.dataframes:
            new_memory_dfs = dict(memory_dfs)
            new_memory_dfs.update(result.dataframes)
            new_state["memory_dfs"] = new_memory_dfs
        
        if not result.success:
            new_state["error_trace"] = result.error
        
        await emit_step_update(
            session_id,
            step_id="python_worker",
            status="completed" if result.success else "failed",
            label="代码执行完成" if result.success else f"执行失败: {result.error[:50]}"
        )
        
        return new_state
        
    except Exception as e:
        await emit_thinking_log(session_id, f"[PythonWorker] 错误: {e}")
        new_state = dict(state)
        new_state["error_trace"] = str(e)
        return new_state
