"""
Python 错误翻译器

将 Python 错误信息翻译为用户友好的中文提示
"""
import re


def translate_python_error(error_text: str) -> str:
    """
    将 Python 错误信息翻译为用户友好的中文提示
    
    Args:
        error_text: 原始错误信息
        
    Returns:
        用户友好的错误说明
    """
    error_lower = error_text.lower()
    
    # 常见错误模式匹配
    patterns = [
        # 列/键不存在
        (r"keyerror[:\s]*['\"]?(\w+)['\"]?", 
         lambda m: f"找不到名为 '{m.group(1)}' 的列，请检查表头是否正确"),
        
        # 文件不存在
        (r"filenotfounderror.*no such file.*['\"]?([^'\"]+)['\"]?",
         lambda m: f"找不到文件 '{m.group(1)}'，请确认文件已上传"),
        
        # 模块未安装
        (r"modulenotfounderror.*no module named ['\"]?(\w+)['\"]?",
         lambda m: f"缺少 Python 库 '{m.group(1)}'，请联系管理员安装"),
        
        # 语法错误
        (r"syntaxerror", 
         lambda m: "代码语法错误，正在尝试修复..."),
        
        # 类型错误
        (r"typeerror.*'(\w+)'.*'(\w+)'",
         lambda m: f"数据类型不匹配：期望 {m.group(1)}，实际是 {m.group(2)}"),
        
        # 索引越界
        (r"indexerror.*out of range",
         lambda m: "数据行数不足，请检查文件是否为空"),
        
        # 值错误
        (r"valueerror.*could not convert.*to (float|int)",
         lambda m: "某些数据无法转换为数字，请检查是否有非数字内容"),
        
        # 编码错误
        (r"(unicodedecode|unicodeencode)error",
         lambda m: "文件编码错误，请尝试将文件另存为 UTF-8 格式"),
        
        # 内存不足
        (r"memoryerror",
         lambda m: "数据量过大，内存不足。请尝试处理更小的数据集"),
        
        # 超时
        (r"\[error\].*超时",
         lambda m: "处理时间过长，请简化任务或减少数据量"),
    ]
    
    for pattern, handler in patterns:
        match = re.search(pattern, error_lower)
        if match:
            return handler(match)
    
    # 无法识别的错误，返回原始信息的前200字符
    return f"执行出错: {error_text[:200]}{'...' if len(error_text) > 200 else ''}"
