"""Pydantic schemas for experience and science-admin APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.experience.defaults import REWARD_POLICY_DEFAULTS, VOICE_PROFILE_DEFAULTS

RewardState = Literal["not_reached", "coupon_issued", "reached_no_coupon"]
QuizNextAction = Literal["next_question", "reward_ready", "return_to_consult"]
AgeGroup = Literal["child", "adult", "elder", "unknown"]
DeviceKbScopeMode = Literal["dept", "files"]


class DeviceBindRequest(BaseModel):
    device_id: str = Field(..., min_length=2, max_length=64)
    name: Optional[str] = Field(default=None, max_length=128)
    service_user_id: Optional[str] = Field(default=None, min_length=36, max_length=36)
    dept_id: Optional[int] = None
    is_active: bool = True
    kb_scope_mode: DeviceKbScopeMode = "dept"
    kb_scope_dept_ids: list[str] = Field(default_factory=list, max_length=200)
    kb_scope_file_ids: list[str] = Field(default_factory=list, max_length=200)


class DeviceResponse(BaseModel):
    device_id: str
    name: Optional[str] = None
    service_user_id: str
    dept_id: Optional[int] = None
    is_active: bool
    kb_scope_mode: DeviceKbScopeMode = "dept"
    kb_scope_dept_ids: list[str] = Field(default_factory=list)
    kb_scope_file_ids: list[str] = Field(default_factory=list)
    device_token: str
    workspace_id: str
    activation_key_last4: Optional[str] = None
    has_activation_key: bool = False


class DeviceActivationRequest(BaseModel):
    device_id: Optional[str] = Field(default=None, min_length=2, max_length=64)
    workspace_id: Optional[str] = Field(default=None, min_length=1, max_length=36)
    activation_key: str = Field(..., min_length=4, max_length=128)


class DeviceActivationResponse(BaseModel):
    activated: bool = True
    device_id: str
    device_token: str
    workspace_id: str



class DeviceActivationCodeCreateRequest(BaseModel):
    name_hint: Optional[str] = Field(default=None, max_length=128)


class DeviceActivationCodeCreateResponse(BaseModel):
    code_id: str
    name_hint: Optional[str] = None
    activation_key: str
    activation_key_last4: str
    status: str
    created_at: datetime


class DeviceActivationCodeResponse(BaseModel):
    code_id: str
    name_hint: Optional[str] = None
    activation_key_last4: str
    status: str
    created_at: datetime
    used_at: Optional[datetime] = None
    used_device_id: Optional[str] = None


class WakewordConfigRequest(BaseModel):
    wakeword: str = Field(..., min_length=1, max_length=64)
    enabled: bool = True
    sensitivity: float = Field(default=0.65, ge=0.1, le=1.0)


class WakewordConfigResponse(BaseModel):
    wakeword: str
    enabled: bool
    sensitivity: float


class VoiceProfileRequest(BaseModel):
    child_voice: str = Field(default=VOICE_PROFILE_DEFAULTS["child_voice"], max_length=64)
    adult_voice: str = Field(default=VOICE_PROFILE_DEFAULTS["adult_voice"], max_length=64)
    elder_voice: str = Field(default=VOICE_PROFILE_DEFAULTS["elder_voice"], max_length=64)
    female_voice: str = Field(default=VOICE_PROFILE_DEFAULTS["female_voice"], max_length=64)
    male_voice: str = Field(default=VOICE_PROFILE_DEFAULTS["male_voice"], max_length=64)
    default_voice: str = Field(default=VOICE_PROFILE_DEFAULTS["default_voice"], max_length=64)
    gender_confidence_threshold: float = Field(
        default=VOICE_PROFILE_DEFAULTS["gender_confidence_threshold"],
        ge=0.5,
        le=0.99,
    )


class VoiceProfileResponse(BaseModel):
    child_voice: str
    adult_voice: str
    elder_voice: str
    female_voice: str
    male_voice: str
    default_voice: str
    gender_confidence_threshold: float


class QuizQuestionCreateRequest(BaseModel):
    question_text: str = Field(..., min_length=2)
    options: list[str] = Field(..., min_length=2, max_length=6)
    answer_key: str = Field(..., min_length=1)
    explanation: Optional[str] = None
    difficulty: int = Field(default=1, ge=1, le=5)
    source_type: str = Field(default="bank", pattern="^(bank|generated)$")
    is_active: bool = True


class QuizQuestionUpdateRequest(BaseModel):
    question_text: Optional[str] = Field(default=None, min_length=2)
    options: Optional[list[str]] = Field(default=None, min_length=2, max_length=6)
    answer_key: Optional[str] = None
    explanation: Optional[str] = None
    difficulty: Optional[int] = Field(default=None, ge=1, le=5)
    is_active: Optional[bool] = None


class QuizQuestionResponse(BaseModel):
    id: str
    question_text: str
    options: list[str]
    answer_key: str
    explanation: Optional[str] = None
    difficulty: int
    source_type: str
    is_active: bool


class RewardPolicyRequest(BaseModel):
    required_correct_count: int = Field(
        default=REWARD_POLICY_DEFAULTS["required_correct_count"], ge=1, le=50
    )
    period_hours: int = Field(default=REWARD_POLICY_DEFAULTS["period_hours"], ge=1, le=720)
    max_claims_per_period: int = Field(
        default=REWARD_POLICY_DEFAULTS["max_claims_per_period"], ge=1, le=100
    )
    coupon_id: Optional[str] = None
    is_active: bool = True


class RewardPolicyResponse(BaseModel):
    required_correct_count: int
    period_hours: int
    max_claims_per_period: int
    coupon_id: Optional[str] = None
    is_active: bool


class CouponCreateRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=128)
    description: Optional[str] = None
    merchant_name: Optional[str] = Field(default=None, max_length=128)
    redeem_link: Optional[str] = Field(default=None, max_length=512)
    total_stock: int = Field(default=0, ge=0)
    expires_at: Optional[datetime] = None
    status: str = Field(default="active", pattern="^(active|inactive)$")


class CouponUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=128)
    description: Optional[str] = None
    merchant_name: Optional[str] = Field(default=None, max_length=128)
    redeem_link: Optional[str] = Field(default=None, max_length=512)
    total_stock: Optional[int] = Field(default=None, ge=0)
    expires_at: Optional[datetime] = None
    status: Optional[str] = Field(default=None, pattern="^(active|inactive)$")


class CouponResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    merchant_name: Optional[str] = None
    redeem_link: Optional[str] = None
    status: str
    total_stock: int
    issued_count: int
    redeemed_count: int
    expires_at: Optional[datetime] = None
    created_at: datetime


class CouponRedeemRequest(BaseModel):
    issuance_id: Optional[str] = None
    coupon_code: Optional[str] = None
    note: Optional[str] = None


class CouponRedeemResponse(BaseModel):
    success: bool
    message: str
    issuance_id: Optional[str] = None
    coupon_code: Optional[str] = None
    redeemed_at: Optional[datetime] = None


class CouponIssuanceResponse(BaseModel):
    id: str
    coupon_id: str
    session_id: str
    device_id: str
    coupon_code: str
    status: str
    issued_at: datetime
    expires_at: Optional[datetime] = None
    redeemed_at: Optional[datetime] = None


class ExperienceSessionStartRequest(BaseModel):
    visitor_id: Optional[str] = Field(default=None, max_length=64)


class ExperienceSessionStartResponse(BaseModel):
    session_id: str
    workspace_id: str
    device_id: str
    started_at: datetime


class ProfileInferResponse(BaseModel):
    event_type: str = "profile_inferred"
    trace_id: str
    age_group: AgeGroup = "unknown"
    age_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    gender: str = Field(pattern="^(female|male|unknown)$")
    gender_confidence: float = Field(ge=0.0, le=1.0)
    outfit_tags: list[str] = Field(default_factory=list, max_length=8)
    vibe_tags: list[str] = Field(default_factory=list, max_length=4)
    feature_tags: list[str] = Field(default_factory=list)
    persona_text: str = ""
    welcome_text: str = ""
    welcome_emotion: str = ""
    tts_voice: str = ""
    tts_speaking_style: str = ""
    tts_audio_base64: Optional[str] = None
    fallback: bool = False
    reason: Optional[str] = None


class StartupProfileResponse(BaseModel):
    age_group: AgeGroup = "unknown"
    age_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    gender: str = Field(pattern="^(female|male|unknown)$")
    gender_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    outfit_tags: list[str] = Field(default_factory=list)
    vibe_tags: list[str] = Field(default_factory=list)
    feature_tags: list[str] = Field(default_factory=list)
    persona_text: str = ""
    welcome_text: str = ""
    welcome_emotion: str = ""
    tts_speaking_style: str = ""
    trace_id: str
    fallback: bool = False
    reason: Optional[str] = None


class WakeEventRequest(BaseModel):
    session_id: str
    wakeword: Optional[str] = None
    gender: Optional[str] = Field(default=None, pattern="^(female|male|unknown)$")
    gender_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    feature_tags: Optional[list[str]] = None


class AsrTurnRequest(BaseModel):
    session_id: str
    text: str = Field(..., min_length=1)
    enable_tts: bool = True
    age_group: Optional[AgeGroup] = None
    gender: Optional[str] = Field(default=None, pattern="^(female|male|unknown)$")
    gender_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class AsrSourceItem(BaseModel):
    file_id: Optional[str] = None
    file_name: Optional[str] = None
    score: float = 0.0


class RetrievalScopeSummary(BaseModel):
    mode: str = Field(default="workspace", pattern="^(workspace|dept|files)$")
    file_ids: list[str] = Field(default_factory=list)
    dept_ids: list[str] = Field(default_factory=list)
    visibilities: list[str] = Field(default_factory=list)
    file_count: int = 0
    dept_count: int = 0
    visibility_count: int = 0


class AsrTurnResponse(BaseModel):
    answer: str
    tts_voice: str
    event_type: str = "voice_reply_ready"
    session_id: str
    trace_id: str
    payload: dict
    sources: list[AsrSourceItem] = Field(default_factory=list)
    retrieval_scope: RetrievalScopeSummary = Field(default_factory=RetrievalScopeSummary)


class KnowledgeScopeRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list, max_length=200)
    visibilities: list[str] = Field(default_factory=list, max_length=20)
    dept_ids: list[str] = Field(default_factory=list, max_length=200)


class KnowledgeScopeResponse(BaseModel):
    file_ids: list[str] = Field(default_factory=list)
    visibilities: list[str] = Field(default_factory=list)
    dept_ids: list[str] = Field(default_factory=list)


class QuizStartRequest(BaseModel):
    session_id: str
    excluded_question_ids: list[str] = Field(default_factory=list, max_length=20)


class QuizStartResponse(BaseModel):
    event_type: str = "quiz_question"
    session_id: str
    trace_id: str
    payload: dict
    question_id: str
    question_text: str
    options: list[str]


class QuizAnswerRequest(BaseModel):
    session_id: str
    question_id: str
    answer: str


class QuizAnswerResponse(BaseModel):
    event_type: str = "quiz_result"
    session_id: str
    trace_id: str
    payload: dict
    correct: bool
    correct_answer: str
    explanation: Optional[str] = None
    correct_count: int
    required_correct_count: int
    reward_ready: bool
    issuance_id: Optional[str] = None
    reward_state: RewardState = "not_reached"
    reward_message: Optional[str] = None
    reward_reason_code: Optional[str] = None
    next_action: QuizNextAction = "next_question"


class CouponQrResponse(BaseModel):
    issuance_id: str
    coupon_code: str
    status: str
    expires_at: Optional[datetime] = None
    qr_payload: str
    qr_link: Optional[str] = None


class QuizGenerateRequest(BaseModel):
    """AI 出题请求"""
    topic: str = Field(..., min_length=2, max_length=128)
    count: int = Field(default=5, ge=1, le=10)
    difficulty: int = Field(default=2, ge=1, le=5)


class QuizGenerateResponse(BaseModel):
    """AI 出题响应"""
    generated_count: int
    questions: list[QuizQuestionResponse]
