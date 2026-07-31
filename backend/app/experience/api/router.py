"""Guest-facing experience APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.core.voice.asr import get_asr_service
from app.experience.deps import (
    ExperienceContext,
    get_experience_context,
    get_experience_user_context,
)
from app.experience.schemas import (
    AsrTurnRequest,
    AsrTurnResponse,
    CouponQrResponse,
    DeviceActivationRequest,
    DeviceActivationResponse,
    ExperienceSessionStartRequest,
    ExperienceSessionStartResponse,
    ProfileInferResponse,
    QuizAnswerRequest,
    QuizAnswerResponse,
    QuizStartRequest,
    QuizStartResponse,
    WakeEventRequest,
)
from app.experience.services import DeviceActivationService, ExperienceRuntimeService
from app.models.auth.workspace import WorkspaceModel
from app.models.common.context import UserContext

router = APIRouter(prefix="/experience")


def _svc(
    db: AsyncSession,
    ctx: ExperienceContext,
    user_ctx: UserContext,
) -> ExperienceRuntimeService:
    return ExperienceRuntimeService(db=db, context=ctx, user_context=user_ctx)


@router.post("/device/activate", response_model=DeviceActivationResponse)
async def activate_device(
    data: DeviceActivationRequest,
    db: AsyncSession = Depends(get_async_db),
):
    device = await DeviceActivationService(db).activate_device(
        device_id=data.device_id,
        workspace_id=data.workspace_id,
        activation_key=data.activation_key,
    )
    ws_result = await db.execute(
        select(
            WorkspaceModel.is_active,
            WorkspaceModel.kiosk_enabled,
        ).where(WorkspaceModel.id == device.workspace_id)
    )
    ws_row = ws_result.first()
    if ws_row is None or not bool(ws_row[0]):
        raise HTTPException(status_code=403, detail="workspace_disabled")
    if not bool(ws_row[1]):
        raise HTTPException(status_code=403, detail="kiosk_disabled")

    return DeviceActivationResponse(
        activated=True,
        device_id=device.device_id,
        device_token=device.device_token,
        workspace_id=device.workspace_id,
    )


@router.post("/session/start", response_model=ExperienceSessionStartResponse)
async def start_session(
    data: ExperienceSessionStartRequest,
    db: AsyncSession = Depends(get_async_db),
    ctx: ExperienceContext = Depends(get_experience_context),
    user_ctx: UserContext = Depends(get_experience_user_context),
):
    session = await _svc(db, ctx, user_ctx).start_session(data.visitor_id)
    return ExperienceSessionStartResponse(
        session_id=session.id,
        workspace_id=ctx.workspace_id,
        device_id=ctx.device_id,
        started_at=session.started_at,
    )


@router.post("/profile/infer", response_model=ProfileInferResponse)
async def profile_infer(
    session_id: str | None = Form(None),
    enable_tts: bool = Form(False),
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
    ctx: ExperienceContext = Depends(get_experience_context),
    user_ctx: UserContext = Depends(get_experience_user_context),
):
    payload = await image.read()
    result = await _svc(db, ctx, user_ctx).handle_profile_infer(
        session_id=session_id,
        image_bytes=payload,
        content_type=image.content_type or "",
        enable_tts=enable_tts,
    )
    return ProfileInferResponse(**result)


@router.post("/wake")
async def wake_event(
    data: WakeEventRequest,
    db: AsyncSession = Depends(get_async_db),
    ctx: ExperienceContext = Depends(get_experience_context),
    user_ctx: UserContext = Depends(get_experience_user_context),
):
    return await _svc(db, ctx, user_ctx).apply_wake_event(
        session_id=data.session_id,
        wakeword=data.wakeword,
        gender=data.gender,
        gender_confidence=data.gender_confidence,
        feature_tags=data.feature_tags,
    )


@router.post("/asr-turn", response_model=AsrTurnResponse)
async def asr_turn(
    data: AsrTurnRequest,
    db: AsyncSession = Depends(get_async_db),
    ctx: ExperienceContext = Depends(get_experience_context),
    user_ctx: UserContext = Depends(get_experience_user_context),
):
    result = await _svc(db, ctx, user_ctx).handle_asr_turn(
        session_id=data.session_id,
        text=data.text,
        enable_tts=data.enable_tts,
        age_group=data.age_group,
        gender=data.gender,
        gender_confidence=data.gender_confidence,
    )
    return AsrTurnResponse(**result)


@router.post("/speech/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    _: ExperienceContext = Depends(get_experience_context),
    __: UserContext = Depends(get_experience_user_context),
):
    asr_service = get_asr_service()
    payload = await audio.read()
    result = await asr_service.transcribe_file(payload, audio.content_type or "")
    return {
        "code": 0 if result.success else result.error_code,
        "text": result.text if result.success else "",
        "msg": "" if result.success else result.user_message,
        "error_detail": "" if result.success else result.error_message,
    }


@router.post("/quiz/start", response_model=QuizStartResponse)
async def quiz_start(
    data: QuizStartRequest,
    db: AsyncSession = Depends(get_async_db),
    ctx: ExperienceContext = Depends(get_experience_context),
    user_ctx: UserContext = Depends(get_experience_user_context),
):
    result = await _svc(db, ctx, user_ctx).start_quiz(
        data.session_id,
        excluded_question_ids=data.excluded_question_ids,
    )
    return QuizStartResponse(**result)


@router.post("/quiz/answer", response_model=QuizAnswerResponse)
async def quiz_answer(
    data: QuizAnswerRequest,
    db: AsyncSession = Depends(get_async_db),
    ctx: ExperienceContext = Depends(get_experience_context),
    user_ctx: UserContext = Depends(get_experience_user_context),
):
    result = await _svc(db, ctx, user_ctx).submit_quiz_answer(
        session_id=data.session_id,
        question_id=data.question_id,
        answer=data.answer,
    )
    return QuizAnswerResponse(**result)


@router.get("/coupon/qrcode/{issuance_id}", response_model=CouponQrResponse)
async def coupon_qrcode(
    issuance_id: str,
    db: AsyncSession = Depends(get_async_db),
    ctx: ExperienceContext = Depends(get_experience_context),
    user_ctx: UserContext = Depends(get_experience_user_context),
):
    result = await _svc(db, ctx, user_ctx).get_coupon_qr(issuance_id)
    return CouponQrResponse(**result)
