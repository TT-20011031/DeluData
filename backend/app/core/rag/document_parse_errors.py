"""
Document parse exceptions and shared constants.
"""

from __future__ import annotations


class DocumentParseError(Exception):
    """文档解析基础异常"""


class FileTooLargeError(DocumentParseError):
    """文件过大异常"""

    def __init__(self, file_size: int, max_size: int):
        self.file_size = file_size
        self.max_size = max_size
        super().__init__(
            f"文件过大 ({file_size / 1024 / 1024:.1f}MB)，"
            f"超过限制 ({max_size / 1024 / 1024:.0f}MB)"
        )


class UnsupportedFormatError(DocumentParseError):
    """不支持的文件格式"""

    def __init__(self, file_name: str, suffix: str):
        self.file_name = file_name
        self.suffix = suffix
        super().__init__(
            f"不支持的文件格式 '{suffix}'。"
            f"支持的格式：.pdf, .docx, .md, .txt"
        )


class EncryptedFileError(DocumentParseError):
    """加密文件异常"""

    def __init__(self, file_name: str):
        self.file_name = file_name
        super().__init__(
            f"文件 '{file_name}' 已加密或受保护，无法解析。"
            f"请移除密码保护后重新上传。"
        )


class LegacyFormatError(DocumentParseError):
    """旧版格式异常（如 .doc）"""

    def __init__(self, file_name: str):
        self.file_name = file_name
        super().__init__(
            f"不支持 .doc 格式文件 '{file_name}'。\n"
            f"请使用 Microsoft Word 或 WPS 将其另存为 .docx 格式后重新上传。"
        )


WATERMARK_PATTERNS = [
    "标准分享网",
    "www.bzfxw.com",
    "免费下载",
    "仅供参考",
    "试用版",
    "watermark",
]
