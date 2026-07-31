"""
文件处理工具模块

提供通用的文件操作函数，遵循 DRY 原则
"""
import asyncio
import aiofiles


def detect_encoding(file_path: str, sample_size: int = 10000) -> str:
    """
    [同步] 检测文件编码
    
    使用 chardet 库检测文件编码，用于正确读取不同编码的文本文件
    
    Args:
        file_path: 文件路径
        sample_size: 采样字节数（默认 10KB）
        
    Returns:
        检测到的编码名称，失败时返回 'utf-8'
    """
    try:
        import chardet
        with open(file_path, 'rb') as f:
            raw = f.read(sample_size)
            result = chardet.detect(raw)
            return result.get('encoding', 'utf-8') or 'utf-8'
    except Exception:
        return 'utf-8'


async def detect_encoding_async(file_path: str, sample_size: int = 10000) -> str:
    """
    [v2.3] 异步检测文件编码
    
    使用 asyncio.to_thread 避免阻塞事件循环
    """
    return await asyncio.to_thread(detect_encoding, file_path, sample_size)


async def read_file_async(file_path: str, limit: int = -1) -> str:
    """
    [v2.3] 异步读取文本文件（自动检测编码）
    
    Args:
        file_path: 文件路径
        limit: 读取字符数限制，-1 表示读取全部
        
    Returns:
        文件内容字符串
    """
    encoding = await detect_encoding_async(file_path)
    async with aiofiles.open(file_path, 'r', encoding=encoding, errors='ignore') as f:
        if limit > 0:
            return await f.read(limit)
        return await f.read()


async def write_file_async(file_path: str, content: str, encoding: str = 'utf-8'):
    """
    [v2.3] 异步写入文本文件
    
    Args:
        file_path: 文件路径
        content: 要写入的内容
        encoding: 文件编码（默认 UTF-8）
    """
    async with aiofiles.open(file_path, 'w', encoding=encoding) as f:
        await f.write(content)

