"""
数据上下文构建工具

统一将 memory_dfs 中的数据转换为 LLM 可读的上下文字符串
支持: DataFrame, List, Dict, str

特性:
- Token 保护: max_chars 参数防止撑爆 Context Window
- 自动截断提示: 数据被截断时追加 "...(已隐藏)"
"""
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def build_data_context(
    memory_dfs: Optional[Dict[str, Any]],
    max_rows: int = 10,
    max_chars: int = 2000,
    context_title: str = "可用数据（来自前序任务）"
) -> str:
    """
    统一数据上下文构建
    
    Args:
        memory_dfs: 内存数据字典，可包含 DataFrame, list, dict, str
        max_rows: DataFrame 最大预览行数
        max_chars: 总字符数上限 (Token 保护)
        context_title: 上下文标题
        
    Returns:
        格式化的 LLM 可读字符串
    """
    if not memory_dfs:
        return ""
    
    # 延迟导入 pandas，避免循环依赖
    try:
        import pandas as pd
        has_pandas = True
    except ImportError:
        has_pandas = False
    
    result_parts = [f"\n\n## {context_title}\n"]
    current_chars = len(result_parts[0])
    
    for key, value in memory_dfs.items():
        if current_chars >= max_chars:
            result_parts.append("\n...(数据过多，已隐藏部分内容)")
            break
        
        part = ""
        
        # DataFrame 处理
        if has_pandas and isinstance(value, pd.DataFrame):
            columns_str = ", ".join(value.columns.tolist())
            preview = value.head(max_rows).to_string()
            part = f"\n### {key}\n列: {columns_str}\n行数: {len(value)}\n预览:\n```\n{preview}\n```\n"
        
        # List 处理
        elif isinstance(value, list) and len(value) > 0:
            # 只预览前几项
            preview_items = value[:5]
            try:
                preview = json.dumps(preview_items, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                preview = str(preview_items)
            part = f"\n### {key}\n类型: list, 长度: {len(value)}\n预览:\n```json\n{preview}\n```\n"
        
        # Dict 处理
        elif isinstance(value, dict):
            try:
                preview = json.dumps(value, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                preview = str(value)
            # 限制预览长度
            if len(preview) > 500:
                preview = preview[:500] + "\n..."
            part = f"\n### {key}\n类型: dict\n预览:\n```json\n{preview}\n```\n"
        
        # 字符串处理
        elif isinstance(value, str):
            preview = value[:500] + ("..." if len(value) > 500 else "")
            part = f"\n### {key}\n类型: text\n内容:\n```\n{preview}\n```\n"
        
        # 其他类型
        else:
            preview = str(value)[:200]
            part = f"\n### {key}\n类型: {type(value).__name__}\n值: {preview}\n"
        
        # 检查是否超出限制
        if current_chars + len(part) > max_chars:
            # 截断当前部分
            remaining = max_chars - current_chars - 50  # 预留截断提示
            if remaining > 100:
                part = part[:remaining] + "\n...(已隐藏)\n"
            else:
                part = "\n...(数据过多，已隐藏)\n"
        
        result_parts.append(part)
        current_chars += len(part)
    
    # 添加使用提示
    if len(result_parts) > 1:
        result_parts.append("\n**重要**: 生成内容时，必须使用上述数据，不要编造数据！\n")
    
    return "".join(result_parts)


def estimate_token_count(text: str) -> int:
    """
    粗略估算 Token 数量
    
    中文约 1.5 字符/Token，英文约 4 字符/Token
    这里使用保守估计
    """
    # 简单估算：中文字符较多时按 1.5，否则按 4
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    total_chars = len(text)
    
    if chinese_chars > total_chars * 0.3:
        # 中文为主
        return int(total_chars / 1.5)
    else:
        # 英文为主
        return int(total_chars / 4)
