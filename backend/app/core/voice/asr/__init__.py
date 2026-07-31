"""
ASR 子模块

提供语音识别服务的抽象接口和具体实现。
"""

from .base import BaseASRService, ASRResult, ASRErrorCode
from .aliyun_asr import AliyunASRService

# 默认 ASR 服务类型
_default_asr_service = None


def get_asr_service() -> BaseASRService:
    """
    获取 ASR 服务单例
    
    使用阿里云 ASR 作为默认实现
    """
    global _default_asr_service
    if _default_asr_service is None:
        _default_asr_service = AliyunASRService()
    return _default_asr_service


def set_asr_service(service: BaseASRService) -> None:
    """设置 ASR 服务（用于依赖注入或测试）"""
    global _default_asr_service
    _default_asr_service = service


__all__ = [
    "BaseASRService",
    "ASRResult",
    "ASRErrorCode",
    "AliyunASRService",
    "get_asr_service",
    "set_asr_service",
]
