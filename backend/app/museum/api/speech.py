"""Museum speech APIs with tenant-auth protection."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.deps import get_user_context
from app.models.common.context import UserContext
from app.museum.services.asr_service import get_asr_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/speech", tags=["Museum Speech"])


class TranscribeResponse(BaseModel):
    code: int
    text: str
    msg: str


class TokenResponse(BaseModel):
    code: int
    token: str = ""
    appkey: str = ""
    msg: str = ""


@router.post("/transcribe", response_model=TranscribeResponse, summary="Transcribe audio")
async def transcribe_audio(
    audio: UploadFile = File(..., description="audio file"),
    _: UserContext = Depends(get_user_context),
):
    if not audio:
        raise HTTPException(status_code=400, detail="audio_required")

    audio_content = await audio.read()
    content_type = audio.content_type or ""

    if len(audio_content) == 0:
        return TranscribeResponse(code=1, text="", msg="empty_audio")
    if len(audio_content) > 10 * 1024 * 1024:
        return TranscribeResponse(code=1, text="", msg="audio_too_large")

    try:
        asr_service = get_asr_service()
        result = await asr_service.transcribe_audio_file(audio_content, content_type)
        if result["success"]:
            return TranscribeResponse(code=0, text=result["text"], msg="")
        return TranscribeResponse(code=2, text="", msg=result["error"])
    except Exception as exc:
        logger.error("[Museum Speech] transcribe failed: %s", exc, exc_info=True)
        return TranscribeResponse(code=3, text="", msg=str(exc))


@router.get("/token", response_model=TokenResponse, summary="Get ASR token")
async def get_asr_token(_: UserContext = Depends(get_user_context)):
    try:
        asr_service = get_asr_service()
        token = asr_service.get_token()
        return TokenResponse(code=0, token=token, appkey=asr_service.appkey, msg="")
    except Exception as exc:
        logger.error("[Museum Speech] token failed: %s", exc)
        return TokenResponse(code=1, token="", appkey="", msg=str(exc))

