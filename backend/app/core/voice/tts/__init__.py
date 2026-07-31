"""
TTS 子模块

提供语音合成服务的抽象接口和具体实现。
"""

from .base import BaseTTSService, TTSChunk, TTSErrorCode
from .volcengine_tts import (
    VolcengineTTSService, 
    TTSConnection, 
    TTSConnectionPool,
    get_tts_connection_pool
)

# 默认 TTS 服务
_default_tts_service = None


def get_tts_service() -> BaseTTSService:
    """
    获取 TTS 服务单例
    
    使用火山引擎 TTS 作为默认实现
    """
    global _default_tts_service
    if _default_tts_service is None:
        _default_tts_service = VolcengineTTSService()
    return _default_tts_service


def set_tts_service(service: BaseTTSService) -> None:
    """设置 TTS 服务（用于依赖注入或测试）"""
    global _default_tts_service
    _default_tts_service = service


__all__ = [
    "BaseTTSService",
    "TTSChunk",
    "TTSErrorCode",
    "VolcengineTTSService",
    "TTSConnection",
    "TTSConnectionPool",
    "get_tts_service",
    "set_tts_service",
    "get_tts_connection_pool",
]
