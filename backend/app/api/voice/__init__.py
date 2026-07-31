"""
语音 API 子模块

提供公用的语音服务 API 路由：
- ASR 语音转文字
- TTS 语音合成（待扩展）
"""

from .router import router

__all__ = ["router"]
