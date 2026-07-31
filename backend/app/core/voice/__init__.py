"""
语音服务公用模块

提供 ASR (语音识别) 和 TTS (语音合成) 的统一抽象接口。
支持多供应商适配：阿里云 ASR、火山引擎 TTS 等。

Author: DeluData Team
"""

from .config import VoiceConfig
from .asr import BaseASRService, ASRResult, get_asr_service
from .tts import BaseTTSService, TTSChunk, get_tts_service

__all__ = [
    # 配置
    "VoiceConfig",
    # ASR
    "BaseASRService",
    "ASRResult",
    "get_asr_service",
    # TTS
    "BaseTTSService",
    "TTSChunk",
    "get_tts_service",
]
