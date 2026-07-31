"""
DeluData 智能问数系统 - 安全路径工具

提供路径穿越保护和安全路径拼接功能，避免安全漏洞。
"""
from pathlib import Path
from typing import Union
import logging

logger = logging.getLogger(__name__)


class PathSecurityError(Exception):
    """路径安全异常 - 检测到路径穿越攻击"""
    pass


def safe_resolve_path(
    base_path: Union[str, Path],
    relative_path: Union[str, Path],
    *,
    strict: bool = True
) -> Path:
    """
    安全地解析相对路径，防止路径穿越攻击
    
    Args:
        base_path: 基础目录（沙盒根目录）
        relative_path: 需要解析的相对路径
        strict: 是否要求路径存在（默认 True）
    
    Returns:
        解析后的绝对路径
    
    Raises:
        PathSecurityError: 检测到路径穿越（如 ../../../etc/passwd）
    
    示例:
        >>> sandbox_root = Path("/data/sandbox/session_123")
        >>> image_url = "images/pic.png"
        >>> full_path = safe_resolve_path(sandbox_root, image_url)
        >>> str(full_path)
        '/data/sandbox/session_123/images/pic.png'
    """
    base = Path(base_path).resolve()
    
    # 确保相对路径不以 / 开头（避免被解释为绝对路径）
    rel = str(relative_path).lstrip("/\\")
    
    # 使用 pathlib 安全拼接
    full_path = (base / rel).resolve()
    
    # 路径穿越检测：解析后的路径必须在 base 目录下
    try:
        full_path.relative_to(base)
    except ValueError:
        logger.warning(
            f"[SECURITY] 路径穿越检测触发: base={base}, relative={relative_path}, resolved={full_path}"
        )
        raise PathSecurityError(
            f"路径穿越检测：'{relative_path}' 解析后超出沙盒边界"
        )
    
    # 严格模式下检查路径是否存在
    if strict and not full_path.exists():
        raise FileNotFoundError(f"文件不存在: {full_path}")
    
    return full_path


def safe_join_path(
    base_path: Union[str, Path],
    *parts: Union[str, Path]
) -> Path:
    """
    安全地拼接多个路径片段，不要求路径存在
    
    相比 safe_resolve_path，此函数更适合构造路径（如创建文件前）
    
    Args:
        base_path: 基础目录
        *parts: 路径片段
    
    Returns:
        拼接后的完整路径（未 resolve，保持相对结构）
    
    Raises:
        PathSecurityError: 拼接结果超出 base_path
    """
    base = Path(base_path).resolve()
    
    # 逐个拼接并清理
    result = base
    for part in parts:
        # 清理开头的斜杠
        cleaned = str(part).lstrip("/\\")
        result = result / cleaned
    
    resolved = result.resolve()
    
    # 路径穿越检测
    try:
        resolved.relative_to(base)
    except ValueError:
        raise PathSecurityError(
            f"路径穿越检测：拼接结果 '{resolved}' 超出基础目录 '{base}'"
        )
    
    return resolved


def get_relative_sandbox_path(
    full_path: Union[str, Path],
    sandbox_base: Union[str, Path]
) -> str:
    """
    从绝对路径提取相对于沙盒的路径
    
    用于将内部绝对路径转换为可安全传输的相对路径
    
    Args:
        full_path: 文件的绝对路径
        sandbox_base: 沙盒根目录
    
    Returns:
        相对路径字符串（如 "images/pic.png"）
    
    Raises:
        PathSecurityError: 路径不在沙盒范围内
    """
    full = Path(full_path).resolve()
    base = Path(sandbox_base).resolve()
    
    try:
        relative = full.relative_to(base)
        # 始终使用正斜杠，保持跨平台一致性
        return str(relative).replace("\\", "/")
    except ValueError:
        raise PathSecurityError(
            f"路径 '{full_path}' 不在沙盒 '{sandbox_base}' 范围内"
        )
