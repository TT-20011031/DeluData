"""Admin APIs for science-center experience configuration."""

from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_admin
from app.core.db.tenant_mixin import set_current_workspace
from app.core.security.auth import User
from app.experience.deps import ExperienceContext
from app.experience.defaults import VOICE_PROFILE_DEFAULTS
from app.experience.models import (
    ScienceCoupon,
    ScienceCouponIssuance,
    ScienceDevice,
    ScienceDeviceActivationCode,
    ScienceQuizQuestion,
    ScienceRewardPolicy,
    ScienceWakewordConfig,
)
from app.experience.schemas import (
    CouponCreateRequest,
    CouponIssuanceResponse,
    CouponRedeemRequest,
    CouponRedeemResponse,
    CouponResponse,
    CouponUpdateRequest,
    DeviceActivationCodeCreateRequest,
    DeviceActivationCodeCreateResponse,
    DeviceActivationCodeResponse,
    DeviceBindRequest,
    DeviceResponse,
    KnowledgeScopeRequest,
    KnowledgeScopeResponse,
    QuizGenerateRequest,
    QuizGenerateResponse,
    QuizQuestionCreateRequest,
    QuizQuestionResponse,
    QuizQuestionUpdateRequest,
    RewardPolicyRequest,
    RewardPolicyResponse,
    VoiceProfileRequest,
    VoiceProfileResponse,
    WakewordConfigRequest,
    WakewordConfigResponse,
)
from app.experience.serializers import (
    serialize_coupon,
    serialize_coupon_issuance,
    serialize_device,
    serialize_device_activation_code,
    serialize_quiz_question,
    serialize_reward_policy,
    serialize_voice_profile,
)
from app.experience.services import (
    DeviceActivationService,
    ExperienceRuntimeService,
    WorkspaceScopeService,
)
from app.models.auth.rbac import UserModel
from app.models.common.context import UserContext

router = APIRouter(prefix="/admin/science")


async def _resolve_device_service_user(
    *,
    db: AsyncSession,
    workspace_id: str,
    requested_user_id: str | None,
    fallback_user_id: str,
) -> tuple[str, int | None]:
    service_user_id = (requested_user_id or fallback_user_id).strip()
    result = await db.execute(
        select(UserModel.id, UserModel.department_id).where(
            UserModel.id == service_user_id,
            UserModel.workspace_id == workspace_id,
            UserModel.disabled == False,
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=400, detail="invalid_service_user")
    return str(row[0]), row[1]


def _admin_service(db: AsyncSession, admin: User) -> ExperienceRuntimeService:
    set_current_workspace(admin.workspace_id)
    ctx = ExperienceContext(
        device_id="admin-console",
        workspace_id=admin.workspace_id,
        service_user_id=admin.id,
        dept_id=admin.department_id,
        device_token="admin",
    )
    user_ctx = UserContext(
        user_id=admin.id,
        workspace_id=admin.workspace_id,
        role=admin.role,
        dept_id=admin.department_id,
        data_scope=admin.data_scope,
        allowed_tables=["*"] if "*" in admin.permissions else [],
    )
    return ExperienceRuntimeService(db=db, context=ctx, user_context=user_ctx)


@router.post("/devices", response_model=DeviceResponse)
async def upsert_device(
    data: DeviceBindRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    result = await db.execute(
        select(ScienceDevice).where(
            ScienceDevice.workspace_id == admin.workspace_id,
            ScienceDevice.device_id == data.device_id,
        )
    )
    device = result.scalar_one_or_none()
    service_user_id, service_dept_id = await _resolve_device_service_user(
        db=db,
        workspace_id=admin.workspace_id,
        requested_user_id=data.service_user_id,
        fallback_user_id=device.service_user_id if device and device.service_user_id else admin.id,
    )
    effective_dept_id = data.dept_id if data.dept_id is not None else service_dept_id
    if not device:
        device = ScienceDevice(
            workspace_id=admin.workspace_id,
            device_id=data.device_id,
            name=data.name,
            service_user_id=service_user_id,
            dept_id=effective_dept_id,
            kb_scope_mode=data.kb_scope_mode,
            kb_scope_dept_ids_json=data.kb_scope_dept_ids,
            kb_scope_file_ids_json=data.kb_scope_file_ids,
            is_active=data.is_active,
            device_token=secrets.token_urlsafe(32),
        )
        db.add(device)
    else:
        device.name = data.name
        device.service_user_id = service_user_id
        device.dept_id = effective_dept_id
        device.kb_scope_mode = data.kb_scope_mode
        device.kb_scope_dept_ids_json = data.kb_scope_dept_ids
        device.kb_scope_file_ids_json = data.kb_scope_file_ids
        device.is_active = data.is_active
        if not device.device_token:
            device.device_token = secrets.token_urlsafe(32)

    await db.flush()
    return serialize_device(device)


@router.get("/devices", response_model=list[DeviceResponse])
async def list_devices(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    result = await db.execute(
        select(ScienceDevice).where(ScienceDevice.workspace_id == admin.workspace_id)
    )
    devices = result.scalars().all()
    return [serialize_device(d) for d in devices]


@router.delete("/devices/{device_id}")
async def delete_device(
    device_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    result = await db.execute(
        select(ScienceDevice).where(
            ScienceDevice.workspace_id == admin.workspace_id,
            ScienceDevice.device_id == device_id,
        )
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="device_not_found")

    await db.delete(device)
    await db.flush()
    return {"success": True}


@router.post("/device-activation-codes", response_model=DeviceActivationCodeCreateResponse)
async def create_device_activation_code(
    data: DeviceActivationCodeCreateRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    activation_code, raw_code = await DeviceActivationService(db).create_activation_code(
        workspace_id=admin.workspace_id,
        issued_by=admin.id,
        service_user_id=admin.id,
        dept_id=admin.department_id,
        name_hint=data.name_hint,
    )
    return DeviceActivationCodeCreateResponse(
        code_id=activation_code.id,
        name_hint=activation_code.name_hint,
        activation_key=raw_code,
        activation_key_last4=activation_code.activation_key_last4,
        status=activation_code.status,
        created_at=activation_code.created_at,
    )


@router.get("/device-activation-codes", response_model=list[DeviceActivationCodeResponse])
async def list_device_activation_codes(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    rows = await DeviceActivationService(db).list_activation_codes(admin.workspace_id)
    return [serialize_device_activation_code(row) for row in rows]


@router.get("/knowledge-scope", response_model=KnowledgeScopeResponse)
async def get_knowledge_scope(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    scope = await WorkspaceScopeService(db).get_scope(admin.workspace_id)
    return KnowledgeScopeResponse(**scope)


@router.put("/knowledge-scope", response_model=KnowledgeScopeResponse)
async def update_knowledge_scope(
    data: KnowledgeScopeRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    scope = await WorkspaceScopeService(db).update_scope(
        workspace_id=admin.workspace_id,
        updated_by=admin.id,
        doc_scope=data.model_dump(),
    )
    return KnowledgeScopeResponse(**scope)


@router.get("/wakeword", response_model=WakewordConfigResponse)
async def get_wakeword(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    service = _admin_service(db, admin)
    config = await service.get_or_init_wakeword_config()
    return WakewordConfigResponse(
        wakeword=config.wakeword,
        enabled=config.enabled,
        sensitivity=config.sensitivity,
    )


@router.put("/wakeword", response_model=WakewordConfigResponse)
async def update_wakeword(
    data: WakewordConfigRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    service = _admin_service(db, admin)
    config = await service.get_or_init_wakeword_config()
    config.wakeword = data.wakeword
    config.enabled = data.enabled
    config.sensitivity = data.sensitivity
    config.updated_by = admin.id
    await db.flush()
    return WakewordConfigResponse(
        wakeword=config.wakeword,
        enabled=config.enabled,
        sensitivity=config.sensitivity,
    )


@router.get("/voice-profiles", response_model=VoiceProfileResponse)
async def get_voice_profile(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    service = _admin_service(db, admin)
    profile = await service.get_or_init_voice_profile()

    return serialize_voice_profile(profile)


@router.put("/voice-profiles", response_model=VoiceProfileResponse)
async def update_voice_profile(
    data: VoiceProfileRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    service = _admin_service(db, admin)
    profile = await service.get_or_init_voice_profile()

    profile.child_voice = data.child_voice or VOICE_PROFILE_DEFAULTS["child_voice"]
    profile.adult_voice = data.adult_voice or VOICE_PROFILE_DEFAULTS["adult_voice"]
    profile.elder_voice = data.elder_voice or VOICE_PROFILE_DEFAULTS["elder_voice"]
    profile.female_voice = data.female_voice or data.adult_voice
    profile.male_voice = data.male_voice or data.elder_voice
    profile.default_voice = data.default_voice or VOICE_PROFILE_DEFAULTS["default_voice"]
    profile.gender_confidence_threshold = data.gender_confidence_threshold
    profile.updated_by = admin.id
    await db.flush()

    return serialize_voice_profile(profile)


@router.post("/quiz/generate", response_model=QuizGenerateResponse)
async def generate_quiz_questions(
    data: QuizGenerateRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    service = _admin_service(db, admin)
    questions = await service.generate_quiz_questions(
        topic=data.topic,
        count=data.count,
        difficulty=data.difficulty,
    )
    return QuizGenerateResponse(
        generated_count=len(questions),
        questions=[serialize_quiz_question(question) for question in questions],
    )


@router.post("/quiz/questions", response_model=QuizQuestionResponse)
async def create_quiz_question(
    data: QuizQuestionCreateRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    question = ScienceQuizQuestion(
        workspace_id=admin.workspace_id,
        question_text=data.question_text,
        options_json=data.options,
        answer_key=data.answer_key,
        explanation=data.explanation,
        difficulty=data.difficulty,
        source_type=data.source_type,
        is_active=data.is_active,
        created_by=admin.id,
    )
    db.add(question)
    await db.flush()
    return serialize_quiz_question(question)


@router.get("/quiz/questions", response_model=list[QuizQuestionResponse])
async def list_quiz_questions(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    result = await db.execute(
        select(ScienceQuizQuestion).where(
            ScienceQuizQuestion.workspace_id == admin.workspace_id
        )
    )
    rows = result.scalars().all()
    return [serialize_quiz_question(q) for q in rows]


@router.put("/quiz/questions/{question_id}", response_model=QuizQuestionResponse)
async def update_quiz_question(
    question_id: str,
    data: QuizQuestionUpdateRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    result = await db.execute(
        select(ScienceQuizQuestion).where(
            ScienceQuizQuestion.id == question_id,
            ScienceQuizQuestion.workspace_id == admin.workspace_id,
        )
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="question_not_found")

    patch = data.model_dump(exclude_unset=True)
    if "options" in patch:
        question.options_json = patch.pop("options")
    for key, value in patch.items():
        setattr(question, key, value)

    await db.flush()
    return serialize_quiz_question(question)


@router.delete("/quiz/questions/{question_id}")
async def delete_quiz_question(
    question_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    result = await db.execute(
        select(ScienceQuizQuestion).where(
            ScienceQuizQuestion.id == question_id,
            ScienceQuizQuestion.workspace_id == admin.workspace_id,
        )
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="question_not_found")
    await db.delete(question)
    await db.flush()
    return {"success": True}


@router.get("/reward-policies", response_model=RewardPolicyResponse)
async def get_reward_policy(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    service = _admin_service(db, admin)
    policy = await service.get_or_init_reward_policy()
    return serialize_reward_policy(policy)


@router.put("/reward-policies", response_model=RewardPolicyResponse)
async def upsert_reward_policy(
    data: RewardPolicyRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    if data.coupon_id:
        coupon_result = await db.execute(
            select(ScienceCoupon.id).where(
                ScienceCoupon.id == data.coupon_id,
                ScienceCoupon.workspace_id == admin.workspace_id,
            )
        )
        if not coupon_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="invalid_coupon_id")

    service = _admin_service(db, admin)
    policy = await service.get_or_init_reward_policy()
    policy.required_correct_count = data.required_correct_count
    policy.period_hours = data.period_hours
    policy.max_claims_per_period = data.max_claims_per_period
    policy.coupon_id = data.coupon_id
    policy.is_active = data.is_active
    policy.updated_by = admin.id
    await db.flush()
    return serialize_reward_policy(policy)


@router.post("/coupons", response_model=CouponResponse)
async def create_coupon(
    data: CouponCreateRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    coupon = ScienceCoupon(
        workspace_id=admin.workspace_id,
        title=data.title,
        description=data.description,
        merchant_name=data.merchant_name,
        redeem_link=data.redeem_link,
        status=data.status,
        total_stock=data.total_stock,
        expires_at=data.expires_at,
        created_by=admin.id,
    )
    db.add(coupon)
    await db.flush()
    await db.refresh(coupon)
    return serialize_coupon(coupon)


@router.get("/coupons", response_model=list[CouponResponse])
async def list_coupons(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    result = await db.execute(
        select(ScienceCoupon).where(ScienceCoupon.workspace_id == admin.workspace_id)
    )
    coupons = result.scalars().all()
    return [serialize_coupon(c) for c in coupons]


@router.put("/coupons/{coupon_id}", response_model=CouponResponse)
async def update_coupon(
    coupon_id: str,
    data: CouponUpdateRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    result = await db.execute(
        select(ScienceCoupon).where(
            ScienceCoupon.id == coupon_id,
            ScienceCoupon.workspace_id == admin.workspace_id,
        )
    )
    coupon = result.scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=404, detail="coupon_not_found")

    patch = data.model_dump(exclude_unset=True)
    for key, value in patch.items():
        setattr(coupon, key, value)
    coupon.updated_at = datetime.utcnow()
    await db.flush()
    await db.refresh(coupon)

    return serialize_coupon(coupon)


@router.post("/coupons/redeem", response_model=CouponRedeemResponse)
async def redeem_coupon(
    data: CouponRedeemRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    service = _admin_service(db, admin)
    result = await service.redeem_coupon(
        issuance_id=data.issuance_id,
        coupon_code=data.coupon_code,
        note=data.note,
        redeemed_by=admin.id,
    )
    return CouponRedeemResponse(
        success=result.get("success", False),
        message=result.get("message", "failed"),
        issuance_id=result.get("issuance_id"),
        coupon_code=result.get("coupon_code"),
        redeemed_at=result.get("redeemed_at"),
    )


@router.get("/coupons/issuances", response_model=list[CouponIssuanceResponse])
async def list_issuances(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    set_current_workspace(admin.workspace_id)
    result = await db.execute(
        select(ScienceCouponIssuance)
        .where(ScienceCouponIssuance.workspace_id == admin.workspace_id)
        .order_by(ScienceCouponIssuance.issued_at.desc())
    )
    rows = result.scalars().all()
    return [serialize_coupon_issuance(r) for r in rows]


# ========== 部门与用户级联查询 ==========
