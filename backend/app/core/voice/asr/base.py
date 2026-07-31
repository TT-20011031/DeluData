"""
ASR 服务基类

定义语音识别服务的抽象接口，遵循严谨设计原则 (Design Rigor)。
支持多供应商适配：阿里云、讯飞、Azure 等。

错误处理标准化：定义统一错误码，便于前端根据错误类型显示友好提示。
"""

from abc import ABC, abstractmethod
from enum import IntEnum
from typing import Optional
from pydantic import BaseModel, Field


class ASRErrorCode(IntEnum):
    """ASR 错误码（标准化错误处理）"""
    
    SUCCESS = 0              # 成功
    EMPTY_AUDIO = 1         # 音频为空
    AUDIO_TOO_LARGE = 2     # 音频文件过大
    FORMAT_ERROR = 3        # 格式错误
    NETWORK_ERROR = 4       # 网络错误
    AUTH_ERROR = 5          # 认证失败（欠费、Token 过期等）
    SERVICE_ERROR = 6       # 服务端错误
    CONFIG_ERROR = 7        # 配置错误
    RATE_LIMIT = 8          # 限流
    UNKNOWN_ERROR = 99      # 未知错误


# 错误码对应的前端友好提示
ASR_ERROR_MESSAGES = {
    ASRErrorCode.SUCCESS: "",
    ASRErrorCode.EMPTY_AUDIO: "没有检测到声音，请再说一次",
    ASRErrorCode.AUDIO_TOO_LARGE: "录音时间太长了",
    ASRErrorCode.FORMAT_ERROR: "音频格式不支持",
    ASRErrorCode.NETWORK_ERROR: "网络连接不稳定，请稍后再试",
    ASRErrorCode.AUTH_ERROR: "语音识别服务额度到期或认证失败，请联系管理员处理",
    ASRErrorCode.SERVICE_ERROR: "语音识别服务繁忙",
    ASRErrorCode.CONFIG_ERROR: "语音服务配置错误",
    ASRErrorCode.RATE_LIMIT: "请求太频繁，请稍后再试",
    ASRErrorCode.UNKNOWN_ERROR: "识别失败，请再说一次",
}


class ASRResult(BaseModel):
    """
    ASR 识别结果
    
    遵循 Schema Validation 原则，使用 Pydantic 严格校验。
    """
    
    success: bool = Field(description="是否识别成功")
    text: str = Field(default="", description="识别出的文本")
    error_code: ASRErrorCode = Field(
        default=ASRErrorCode.SUCCESS, 
        description="错误码"
    )
    error_message: str = Field(default="", description="错误详情（调试用）")
    
    @property
    def user_message(self) -> str:
        """获取用户友好的错误提示"""
        return ASR_ERROR_MESSAGES.get(self.error_code, "识别失败")
    
    @classmethod
    def ok(cls, text: str) -> "ASRResult":
        """创建成功结果"""
        return cls(success=True, text=text, error_code=ASRErrorCode.SUCCESS)
    
    @classmethod
    def fail(
        cls, 
        error_code: ASRErrorCode, 
        error_message: str = ""
    ) -> "ASRResult":
        """创建失败结果"""
        return cls(
            success=False, 
            text="", 
            error_code=error_code,
            error_message=error_message
        )


class BaseASRService(ABC):
    """
    ASR 服务抽象基类
    
    遵循设计原则：
    - 严谨设计：抽象基类 + 适配器模式
    - 全异步 I/O：所有方法使用 async/await
    - 配置外置：配置通过构造函数注入
    """
    
    @abstractmethod
    async def transcribe(
        self, 
        audio_content: bytes, 
        audio_format: str = "wav",
        sample_rate: int = 16000
    ) -> ASRResult:
        """
        语音转文字
        
        Args:
            audio_content: 音频二进制数据
            audio_format: 音频格式 (wav/mp3/pcm/webm)
            sample_rate: 采样率
            
        Returns:
            ASRResult: 识别结果
        """
        pass
    
    @abstractmethod
    async def transcribe_file(
        self, 
        audio_content: bytes, 
        content_type: str = ""
    ) -> ASRResult:
        """
        处理音频文件并转文字（自动处理格式转换）
        
        Args:
            audio_content: 音频二进制数据
            content_type: MIME 类型 (如 audio/webm)
            
        Returns:
            ASRResult: 识别结果
        """
        pass
    
    async def get_token(self) -> Optional[str]:
        """
        获取 Token（用于前端直连 WebSocket）
        
        默认返回 None，子类可覆写
        """
        return None
    
    def get_appkey(self) -> Optional[str]:
        """
        获取 AppKey（用于前端直连）
        
        默认返回 None，子类可覆写
        """
        return None
