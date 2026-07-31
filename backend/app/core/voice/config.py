"""
语音服务配置

遵循配置外置原则 (No Hardcoding)：
- 所有配置通过环境变量读取
- 支持依赖注入，方便不同项目使用不同配置
"""

import os
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


REALTIME_DIALOGUE_APP_KEY = "4R29PBjTFRiv2bNUd5p_2IzGWx56Kpyf"


class TTSApiType(str, Enum):
    """TTS API 类型枚举"""
    REALTIME_DIALOGUE = "realtime_dialogue"  # 实时对话 API
    BIDIRECTION_TTS = "bidirection_tts"       # 普通 TTS API


def _parse_float_env(*names: str, default: float) -> float:
    """Read the first non-empty env var and parse it as float."""
    for name in names:
        raw = os.getenv(name)
        if raw is None:
            continue
        value = raw.strip()
        if not value:
            continue
        try:
            return float(value)
        except ValueError:
            return default
    return default


class ASRConfig(BaseModel):
    """ASR 服务配置"""
    
    # 阿里云 ASR
    aliyun_access_key_id: str = Field(
        default_factory=lambda: os.getenv("ALIYUN_ACCESS_KEY_ID", "")
    )
    aliyun_access_key_secret: str = Field(
        default_factory=lambda: os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
    )
    aliyun_appkey: str = Field(
        default_factory=lambda: os.getenv("ALIYUN_ASR_APPKEY", "")
    )
    
    # 默认采样率
    sample_rate: int = 16000
    
    # ffmpeg 路径 (用于音频格式转换)
    ffmpeg_path: str = Field(
        default_factory=lambda: os.getenv("FFMPEG_PATH", "ffmpeg")
    )


class TTSConfig(BaseModel):
    """TTS 服务配置"""
    
    # ========== API 类型配置 ==========
    api_type: str = Field(
        default_factory=lambda: os.getenv("MUSEUM_TTS_API_TYPE", "realtime_dialogue")
    )
    
    # ========== 实时对话 API 配置 ==========
    realtime_dialogue_url: str = Field(
        default_factory=lambda: os.getenv(
            "MUSEUM_VOLCANO_REALTIME_URL",
            "wss://openspeech.bytedance.com/api/v1/realtime_dialogue"
        )
    )
    realtime_resource_id: str = Field(
        default_factory=lambda: os.getenv(
            "MUSEUM_VOLCANO_REALTIME_RESOURCE_ID",
            "volc.speech.dialog"
        )
    )
    
    # ========== 普通 TTS API 配置 ==========
    bidirection_tts_url: str = Field(
        default_factory=lambda: os.getenv(
            "MUSEUM_VOLCANO_TTS_URL",
            "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
        )
    )
    bidirection_resource_id: str = Field(
        default_factory=lambda: os.getenv(
            "MUSEUM_VOLCANO_RESOURCE_ID",
            "seed-tts-1.0"
        )
    )
    
    # ========== 动态属性（根据 API 类型选择） ==========
    @property
    def volcengine_ws_url(self) -> str:
        """根据 API 类型返回 WebSocket URL"""
        if self.api_type == TTSApiType.BIDIRECTION_TTS.value:
            return self.bidirection_tts_url
        return self.realtime_dialogue_url
    
    @property
    def volcengine_resource_id(self) -> str:
        """根据 API 类型返回 Resource ID"""
        if self.api_type == TTSApiType.BIDIRECTION_TTS.value:
            return self.bidirection_resource_id
        return self.realtime_resource_id
    
    # ========== 通用配置 ==========
    # 豆包 / 火山实时语音 TTS
    @property
    def volcengine_app_key(self) -> str:
        """
        Realtime Dialogue 握手所需的固定 App Key。
        文档固定值为 PlgvMymc7f3tQnJ6，允许通过环境变量覆盖。
        """
        return (
            os.getenv("MUSEUM_VOLCANO_REALTIME_APP_KEY")
            or os.getenv("VOLCENGINE_REALTIME_APP_KEY")
            or os.getenv("VOLCENGINE_TTS_APP_KEY")
            or REALTIME_DIALOGUE_APP_KEY
        )

    @property
    def volcengine_access_key(self) -> str:
        """火山引擎 realtime dialogue Access Key。"""
        return os.getenv("MUSEUM_VOLCANO_ACCESS_KEY") or os.getenv("VOLCENGINE_ACCESS_KEY") or ""

    @property
    def volcengine_app_id(self) -> str:
        """火山引擎 App ID"""
        val = os.getenv("MUSEUM_VOLCANO_APP_ID") or os.getenv("VOLCENGINE_APP_ID")
        return val or "3541448686"

    @property
    def volcengine_token(self) -> str:
        """
        兼容旧版 TTS / 其他链路的 Token 配置。
        """
        return (
            os.getenv("VOLCENGINE_TTS_TOKEN")
            or os.getenv("MUSEUM_VOLCANO_APP_KEY")
            or os.getenv("VOLCENGINE_TOKEN")
            or ""
        )

    volcengine_cluster: str = Field(
        default_factory=lambda: os.getenv("VOLCENGINE_TTS_CLUSTER", "volcano_tts")
    )
    volcengine_uid: str = Field(
        default_factory=lambda: os.getenv("VOLCENGINE_TTS_UID", "default_uid")
    )
    
    # 默认音色配置
    default_voice_id: str = Field(
        default_factory=lambda: os.getenv("TTS_DEFAULT_VOICE_ID", "zh_female_vv_jupiter_bigtts")
    )
    
    # 音色映射表：场景 -> 音色 ID（根据 API 类型动态选择）
    # 实时对话 API 音色映射
    voice_map_realtime: dict = Field(default_factory=lambda: {
        "guide": "zh_female_vv_jupiter_bigtts",
        "child": "zh_female_xiaohe_jupiter_bigtts",
        "ghost": "zh_male_xiaotian_jupiter_bigtts",
        "robot": "zh_male_yunzhou_jupiter_bigtts",
        "female_gentle": "zh_female_vv_jupiter_bigtts",
        "male_broadcast": "zh_male_yunzhou_jupiter_bigtts",
    })
    
    # 普通 TTS API 音色映射
    voice_map_bidirection: dict = Field(default_factory=lambda: {
        "guide": "zh_female_peiqi_mars_bigtts",
        "child": "zh_female_peiqi_mars_bigtts",
        "ghost": "zh_female_peiqi_mars_bigtts",
        "robot": "zh_female_peiqi_mars_bigtts",
        "female_gentle": "zh_female_peiqi_mars_bigtts",
        "male_broadcast": "zh_female_peiqi_mars_bigtts",
    })
    
    # 兼容旧配置（已弃用，使用 get_voice_map 替代）
    voice_map: dict = Field(default_factory=lambda: {})
    
    # 默认语速 (0.5 - 2.0)
    default_speed: float = 1.0
    
    # 默认采样率
    sample_rate: int = 24000

    # 输出音频格式：kiosk 与馆内前端都稳定支持 pcm_s16le
    output_format: str = Field(
        default_factory=lambda: (
            os.getenv("MUSEUM_VOLCANO_TTS_AUDIO_FORMAT")
            or os.getenv("VOLCENGINE_TTS_AUDIO_FORMAT")
            or "pcm_s16le"
        )
    )

    # WebSocket 收包超时（秒）
    timeout: float = Field(
        default_factory=lambda: _parse_float_env(
            "MUSEUM_VOLCANO_TTS_TIMEOUT_SECONDS",
            "VOLCENGINE_TTS_TIMEOUT_SECONDS",
            default=30.0,
        )
    )
    
    def is_bidirection_tts(self) -> bool:
        """检查是否使用普通 TTS API"""
        return self.api_type == TTSApiType.BIDIRECTION_TTS.value
    
    def is_realtime_dialogue(self) -> bool:
        """检查是否使用实时对话 API"""
        return self.api_type == TTSApiType.REALTIME_DIALOGUE.value
    
    def get_voice_map(self) -> dict:
        """根据 API 类型返回音色映射表"""
        if self.is_bidirection_tts():
            return self.voice_map_bidirection
        return self.voice_map_realtime


class VoiceConfig(BaseModel):
    """语音服务总配置"""
    
    asr: ASRConfig = Field(default_factory=ASRConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)


# 全局配置单例
_voice_config: Optional[VoiceConfig] = None


def get_voice_config() -> VoiceConfig:
    """获取语音配置单例"""
    global _voice_config
    if _voice_config is None:
        _voice_config = VoiceConfig()
    return _voice_config


def set_voice_config(config: VoiceConfig) -> None:
    """设置语音配置（用于依赖注入）"""
    global _voice_config
    _voice_config = config
