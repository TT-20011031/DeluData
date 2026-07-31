"""
语音服务 API 路由

提供公用的语音识别和语音合成接口，可被多个前端项目调用。

遵循设计原则：
- 严谨设计 (Design Rigor)：使用 Pydantic 模型校验 + 鉴权检查
- 全异步 I/O (Async First)：所有接口使用 async/await
- 错误标准化：返回统一格式的错误响应
- 安全前置 (Security Left)：所有接口需要登录认证

Author: DeluData Team
"""

import logging
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel, Field

from app.core.voice.asr import get_asr_service, ASRResult, ASRErrorCode
from app.core.security.auth import User
from app.api.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["Voice"])


# ========== 请求/响应模型 ==========

class TranscribeResponse(BaseModel):
    """语音识别响应"""
    code: int = Field(description="错误码，0 表示成功")
    text: str = Field(default="", description="识别出的文本")
    msg: str = Field(default="", description="错误信息（用户友好）")
    error_detail: str = Field(default="", description="错误详情（调试用）")


class TokenResponse(BaseModel):
    """ASR Token 响应"""
    code: int = Field(description="错误码，0 表示成功")
    token: str = Field(default="", description="ASR Token")
    appkey: str = Field(default="", description="ASR AppKey")
    msg: str = Field(default="", description="错误信息")


class VoiceListItem(BaseModel):
    """音色列表项"""
    id: str = Field(description="音色 ID")
    name: str = Field(description="音色名称")
    description: str = Field(default="", description="音色描述")


class VoiceListResponse(BaseModel):
    """音色列表响应"""
    code: int = Field(default=0, description="错误码")
    voices: list[VoiceListItem] = Field(default_factory=list)


# ========== ASR 接口 ==========

@router.post("/transcribe", response_model=TranscribeResponse, summary="语音转文字")
async def transcribe_audio(
    audio: UploadFile = File(..., description="音频文件 (WebM/WAV/MP3)"),
    current_user: User = Depends(get_current_user)  # 鉴权检查
):
    """
    上传音频文件并转换为文字
    
    需要登录认证，防止未授权用户消耗云服务资源。
    
    支持格式: WebM, WAV, MP3, OGG
    采样率: 16kHz (会自动转换)
    最大大小: 10MB
    """
    if not audio:
        raise HTTPException(status_code=400, detail="请上传音频文件")
    
    audio_content = await audio.read()
    content_type = audio.content_type or ''
    
    logger.info(f"[Voice API] 收到: {audio.filename}, {len(audio_content)} bytes, {content_type}")
    
    # 检查大小
    if len(audio_content) == 0:
        return TranscribeResponse(
            code=ASRErrorCode.EMPTY_AUDIO,
            text="",
            msg="音频文件为空"
        )
    
    if len(audio_content) > 10 * 1024 * 1024:
        return TranscribeResponse(
            code=ASRErrorCode.AUDIO_TOO_LARGE,
            text="",
            msg="音频文件过大，最大支持 10MB"
        )
    
    try:
        asr_service = get_asr_service()
        result = await asr_service.transcribe_file(audio_content, content_type)
        
        if result.success:
            return TranscribeResponse(
                code=0,
                text=result.text,
                msg=""
            )
        else:
            return TranscribeResponse(
                code=result.error_code,
                text="",
                msg=result.user_message,
                error_detail=result.error_message
            )
            
    except Exception as e:
        logger.error(f"[Voice API] 错误: {e}", exc_info=True)
        return TranscribeResponse(
            code=ASRErrorCode.UNKNOWN_ERROR,
            text="",
            msg="语音识别失败",
            error_detail=str(e)
        )


@router.get("/asr/token", response_model=TokenResponse, summary="获取 ASR Token")
async def get_asr_token(
    current_user: User = Depends(get_current_user)  # 鉴权检查
):
    """
    获取阿里云 ASR Token
    
    需要登录认证，防止 Token 泄露。
    用于前端直接调用阿里云 WebSocket API（实时语音识别）
    """
    try:
        asr_service = get_asr_service()
        token = await asr_service.get_token()
        appkey = asr_service.get_appkey()
        
        if token:
            return TokenResponse(
                code=0,
                token=token,
                appkey=appkey or "",
                msg=""
            )
        else:
            return TokenResponse(
                code=ASRErrorCode.AUTH_ERROR,
                token="",
                appkey="",
                msg="Token 获取失败"
            )
            
    except Exception as e:
        logger.error(f"[Voice API] Token 获取失败: {e}")
        return TokenResponse(
            code=ASRErrorCode.UNKNOWN_ERROR,
            token="",
            appkey="",
            msg=str(e)
        )


# ========== TTS 接口（可扩展）==========

@router.get("/tts/voices", response_model=VoiceListResponse, summary="获取可用音色列表")
async def get_available_voices(
    current_user: User = Depends(get_current_user)  # 鉴权检查
):
    """
    获取可用的 TTS 音色列表
    
    需要登录认证。
    返回音色 ID、名称和描述
    """
    from app.core.voice.tts import get_tts_service
    
    try:
        tts_service = get_tts_service()
        voices = tts_service.get_available_voices()
        
        return VoiceListResponse(
            code=0,
            voices=[VoiceListItem(**v) for v in voices]
        )
    except Exception as e:
        logger.error(f"[Voice API] 获取音色列表失败: {e}")
        return VoiceListResponse(code=1, voices=[])
