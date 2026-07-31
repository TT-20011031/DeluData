"""
TTS 服务基类

定义语音合成服务的抽象接口，遵循严谨设计原则 (Design Rigor)。
支持多供应商适配：火山引擎、阿里云、Azure 等。

特性支持：
- 音色切换 (voice_id)：支持不同场景使用不同音色
- 语速调整 (speed)：支持语速快慢调节
- 情感风格 (speaking_style)：支持情感标记
- 流式输出：实时返回音频块
"""

from abc import ABC, abstractmethod
from enum import IntEnum
from typing import Optional, AsyncIterator
from pydantic import BaseModel, Field


class TTSErrorCode(IntEnum):
    """TTS 错误码（标准化错误处理）"""
    
    SUCCESS = 0              # 成功
    EMPTY_TEXT = 1          # 文本为空
    TEXT_TOO_LONG = 2       # 文本过长
    NETWORK_ERROR = 3       # 网络错误
    AUTH_ERROR = 4          # 认证失败
    SERVICE_ERROR = 5       # 服务端错误
    CONFIG_ERROR = 6        # 配置错误
    VOICE_NOT_FOUND = 7     # 音色不存在
    RATE_LIMIT = 8          # 限流
    CONNECTION_CLOSED = 9   # 连接关闭
    UNKNOWN_ERROR = 99      # 未知错误


# 错误码对应的前端友好提示
TTS_ERROR_MESSAGES = {
    TTSErrorCode.SUCCESS: "",
    TTSErrorCode.EMPTY_TEXT: "没有内容可以播放",
    TTSErrorCode.TEXT_TOO_LONG: "文本太长了",
    TTSErrorCode.NETWORK_ERROR: "网络连接不稳定",
    TTSErrorCode.AUTH_ERROR: "语音服务暂时不可用",
    TTSErrorCode.SERVICE_ERROR: "语音合成服务繁忙",
    TTSErrorCode.CONFIG_ERROR: "语音服务配置错误",
    TTSErrorCode.VOICE_NOT_FOUND: "音色不存在",
    TTSErrorCode.RATE_LIMIT: "请求太频繁",
    TTSErrorCode.CONNECTION_CLOSED: "连接已断开",
    TTSErrorCode.UNKNOWN_ERROR: "语音合成失败",
}


class TTSChunk(BaseModel):
    """
    TTS 音频块
    
    用于流式输出的音频数据块。
    """
    
    audio_data: bytes = Field(description="音频二进制数据")
    format: str = Field(default="pcm", description="音频格式 (pcm/wav/mp3)")
    sample_rate: int = Field(default=24000, description="采样率")
    is_final: bool = Field(default=False, description="是否为最后一块")
    text: Optional[str] = Field(default=None, description="对应的文本片段")
    error_code: TTSErrorCode = Field(
        default=TTSErrorCode.SUCCESS, 
        description="错误码"
    )
    error_message: str = Field(default="", description="错误详情")
    
    @property
    def is_error(self) -> bool:
        """是否为错误响应"""
        return self.error_code != TTSErrorCode.SUCCESS
    
    @property
    def user_message(self) -> str:
        """获取用户友好的错误提示"""
        return TTS_ERROR_MESSAGES.get(self.error_code, "语音合成失败")
    
    @classmethod
    def data(
        cls, 
        audio_data: bytes, 
        text: Optional[str] = None,
        is_final: bool = False,
        sample_rate: int = 24000
    ) -> "TTSChunk":
        """创建音频数据块"""
        return cls(
            audio_data=audio_data, 
            text=text, 
            is_final=is_final,
            sample_rate=sample_rate
        )
    
    @classmethod
    def error(
        cls, 
        error_code: TTSErrorCode, 
        error_message: str = ""
    ) -> "TTSChunk":
        """创建错误块"""
        return cls(
            audio_data=b"",
            is_final=True,
            error_code=error_code,
            error_message=error_message
        )
    
    @classmethod
    def end(cls) -> "TTSChunk":
        """创建结束标记块"""
        return cls(audio_data=b"", is_final=True)


class BaseTTSService(ABC):
    """
    TTS 服务抽象基类
    
    遵循设计原则：
    - 严谨设计：抽象基类 + 适配器模式
    - 全异步 I/O：所有方法使用 async/await
    - 配置外置：配置通过构造函数注入
    
    核心能力：
    - 音色切换：支持不同场景（博物馆解说、儿童语音、怪物语音等）
    - 语速调整：支持 0.5x - 2.0x 语速
    - 流式输出：实时返回音频块，降低首字延迟
    """
    
    @abstractmethod
    async def synthesize(
        self, 
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        speaking_style: Optional[str] = None,
        **kwargs
    ) -> bytes:
        """
        一次性语音合成
        
        Args:
            text: 待合成文本
            voice_id: 音色 ID（如 "ghost", "guide", "child"）
            speed: 语速 (0.5 - 2.0)
            speaking_style: 情感风格（如 "happy", "sad"）
            
        Returns:
            完整音频数据
        """
        pass
    
    @abstractmethod
    def synthesize_streaming(
        self, 
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        speaking_style: Optional[str] = None,
        **kwargs
    ) -> AsyncIterator[TTSChunk]:
        """
        流式语音合成
        
        Args:
            text: 待合成文本
            voice_id: 音色 ID
            speed: 语速 (0.5 - 2.0)
            speaking_style: 情感风格
            
        Yields:
            TTSChunk: 音频数据块
        """
        pass
    
    def get_available_voices(self) -> list[dict]:
        """
        获取可用音色列表
        
        Returns:
            [{"id": "voice_id", "name": "音色名称", "description": "描述"}, ...]
        """
        return []
    
    def resolve_voice_id(self, voice_key: str) -> str:
        """
        解析音色 key 为实际的音色 ID
        
        子类可覆写此方法实现音色映射。
        例如：resolve_voice_id("ghost") -> "zh_male_shaonianzixin_moon_bigtts"
        
        Args:
            voice_key: 音色 key（如 "ghost", "guide"）
            
        Returns:
            实际的音色 ID
        """
        return voice_key
