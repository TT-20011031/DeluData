"""ORM models for science-center guest experience."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.database import Base
from app.core.db.tenant_mixin import TenantMixin
from app.experience.defaults import (
    REWARD_POLICY_DEFAULTS,
    VOICE_PROFILE_DEFAULTS,
    WAKEWORD_CONFIG_DEFAULTS,
)


def _uuid() -> str:
    return str(uuid.uuid4())


class ScienceDevice(Base, TenantMixin):
    __tablename__ = "science_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    device_token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    activation_key_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    activation_key_last4: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    activation_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    service_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sys_users.id"), nullable=False
    )
    dept_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sys_departments.id"), nullable=True
    )
    kb_scope_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="dept")
    kb_scope_dept_ids_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    kb_scope_file_ids_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "device_id", name="uq_science_device_workspace"),
    )


class ScienceDeviceActivationCode(Base, TenantMixin):
    __tablename__ = "science_device_activation_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name_hint: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    activation_key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    activation_key_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    service_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sys_users.id"), nullable=False
    )
    dept_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sys_departments.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    used_device_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_science_activation_code_workspace_status", "workspace_id", "status"),
    )


class ScienceSession(Base, TenantMixin):
    __tablename__ = "science_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    visitor_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_science_session_workspace_status", "workspace_id", "status"),
    )


class ScienceWakewordConfig(Base, TenantMixin):
    __tablename__ = "science_wakeword_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wakeword: Mapped[str] = mapped_column(
        String(64), nullable=False, default=WAKEWORD_CONFIG_DEFAULTS["wakeword"]
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=WAKEWORD_CONFIG_DEFAULTS["enabled"]
    )
    sensitivity: Mapped[float] = mapped_column(
        nullable=False, default=WAKEWORD_CONFIG_DEFAULTS["sensitivity"]
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_science_wakeword_workspace"),
    )


class ScienceVoiceProfile(Base, TenantMixin):
    __tablename__ = "science_voice_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    child_voice: Mapped[str] = mapped_column(
        String(64), nullable=False, default=VOICE_PROFILE_DEFAULTS["child_voice"]
    )
    adult_voice: Mapped[str] = mapped_column(
        String(64), nullable=False, default=VOICE_PROFILE_DEFAULTS["adult_voice"]
    )
    elder_voice: Mapped[str] = mapped_column(
        String(64), nullable=False, default=VOICE_PROFILE_DEFAULTS["elder_voice"]
    )
    female_voice: Mapped[str] = mapped_column(
        String(64), nullable=False, default=VOICE_PROFILE_DEFAULTS["female_voice"]
    )
    male_voice: Mapped[str] = mapped_column(
        String(64), nullable=False, default=VOICE_PROFILE_DEFAULTS["male_voice"]
    )
    default_voice: Mapped[str] = mapped_column(
        String(64), nullable=False, default=VOICE_PROFILE_DEFAULTS["default_voice"]
    )
    gender_confidence_threshold: Mapped[float] = mapped_column(
        nullable=False, default=VOICE_PROFILE_DEFAULTS["gender_confidence_threshold"]
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_science_voice_profile_workspace"),
    )


class ScienceWorkspaceSetting(Base, TenantMixin):
    __tablename__ = "science_workspace_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_scope_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_science_workspace_settings_workspace"),
    )


class ScienceQuizQuestion(Base, TenantMixin):
    __tablename__ = "science_quiz_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[list] = mapped_column(JSON, nullable=False)
    answer_key: Mapped[str] = mapped_column(String(255), nullable=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="bank")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        Index("idx_science_quiz_workspace_active", "workspace_id", "is_active"),
    )


class ScienceQuizAttempt(Base, TenantMixin):
    __tablename__ = "science_quiz_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_answer: Mapped[str] = mapped_column(String(255), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        Index("idx_science_attempt_workspace_session", "workspace_id", "session_id"),
    )


class ScienceRewardPolicy(Base, TenantMixin):
    __tablename__ = "science_reward_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    required_correct_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=REWARD_POLICY_DEFAULTS["required_correct_count"]
    )
    period_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=REWARD_POLICY_DEFAULTS["period_hours"]
    )
    max_claims_per_period: Mapped[int] = mapped_column(
        Integer, nullable=False, default=REWARD_POLICY_DEFAULTS["max_claims_per_period"]
    )
    coupon_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_science_reward_policy_workspace"),
    )


class ScienceCoupon(Base, TenantMixin):
    __tablename__ = "science_coupons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    merchant_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    redeem_link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    total_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    redeemed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        Index("idx_science_coupon_workspace_status", "workspace_id", "status"),
    )


class ScienceCouponIssuance(Base, TenantMixin):
    __tablename__ = "science_coupon_issuances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    coupon_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    coupon_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued")
    issued_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    redeemed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_science_issuance_workspace_session", "workspace_id", "session_id"),
    )


class ScienceCouponRedemption(Base, TenantMixin):
    __tablename__ = "science_coupon_redemptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    issuance_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    redeemed_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        Index("idx_science_redeem_workspace", "workspace_id"),
        Index("idx_science_redeem_issuance_redeemed", "issuance_id", "redeemed_at"),
        UniqueConstraint("issuance_id", name="uq_science_redeem_issuance"),
    )
