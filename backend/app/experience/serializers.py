"""Shared serializers for science experience DTOs."""

from __future__ import annotations

from app.experience.models import (
    ScienceCoupon,
    ScienceCouponIssuance,
    ScienceDevice,
    ScienceDeviceActivationCode,
    ScienceQuizQuestion,
    ScienceRewardPolicy,
    ScienceVoiceProfile,
)
from app.experience.schemas import (
    CouponIssuanceResponse,
    CouponResponse,
    DeviceActivationCodeResponse,
    DeviceResponse,
    QuizQuestionResponse,
    RewardPolicyResponse,
    VoiceProfileResponse,
)


def serialize_device(device: ScienceDevice) -> DeviceResponse:
    return DeviceResponse(
        device_id=device.device_id,
        name=device.name,
        service_user_id=device.service_user_id,
        dept_id=device.dept_id,
        is_active=device.is_active,
        kb_scope_mode=device.kb_scope_mode,
        kb_scope_dept_ids=device.kb_scope_dept_ids_json or [],
        kb_scope_file_ids=device.kb_scope_file_ids_json or [],
        device_token=device.device_token,
        workspace_id=device.workspace_id,
        activation_key_last4=device.activation_key_last4,
        has_activation_key=bool(device.activation_key_hash),
    )


def serialize_device_activation_code(
    code: ScienceDeviceActivationCode,
) -> DeviceActivationCodeResponse:
    return DeviceActivationCodeResponse(
        code_id=code.id,
        name_hint=code.name_hint,
        activation_key_last4=code.activation_key_last4,
        status=code.status,
        created_at=code.created_at,
        used_at=code.used_at,
        used_device_id=code.used_device_id,
    )


def serialize_voice_profile(profile: ScienceVoiceProfile) -> VoiceProfileResponse:
    return VoiceProfileResponse(
        child_voice=profile.child_voice,
        adult_voice=profile.adult_voice,
        elder_voice=profile.elder_voice,
        female_voice=profile.female_voice,
        male_voice=profile.male_voice,
        default_voice=profile.default_voice,
        gender_confidence_threshold=profile.gender_confidence_threshold,
    )


def serialize_quiz_question(question: ScienceQuizQuestion) -> QuizQuestionResponse:
    return QuizQuestionResponse(
        id=question.id,
        question_text=question.question_text,
        options=question.options_json,
        answer_key=question.answer_key,
        explanation=question.explanation,
        difficulty=question.difficulty,
        source_type=question.source_type,
        is_active=question.is_active,
    )


def serialize_reward_policy(policy: ScienceRewardPolicy) -> RewardPolicyResponse:
    return RewardPolicyResponse(
        required_correct_count=policy.required_correct_count,
        period_hours=policy.period_hours,
        max_claims_per_period=policy.max_claims_per_period,
        coupon_id=policy.coupon_id,
        is_active=policy.is_active,
    )


def serialize_coupon(coupon: ScienceCoupon) -> CouponResponse:
    return CouponResponse(
        id=coupon.id,
        title=coupon.title,
        description=coupon.description,
        merchant_name=coupon.merchant_name,
        redeem_link=coupon.redeem_link,
        status=coupon.status,
        total_stock=coupon.total_stock,
        issued_count=coupon.issued_count,
        redeemed_count=coupon.redeemed_count,
        expires_at=coupon.expires_at,
        created_at=coupon.created_at,
    )


def serialize_coupon_issuance(
    issuance: ScienceCouponIssuance,
) -> CouponIssuanceResponse:
    return CouponIssuanceResponse(
        id=issuance.id,
        coupon_id=issuance.coupon_id,
        session_id=issuance.session_id,
        device_id=issuance.device_id,
        coupon_code=issuance.coupon_code,
        status=issuance.status,
        issued_at=issuance.issued_at,
        expires_at=issuance.expires_at,
        redeemed_at=issuance.redeemed_at,
    )
