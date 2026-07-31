"""Reward, coupon issuance, and redemption helpers."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select

from app.experience.defaults import (
    COUPON_CODE_PREFIX,
    REWARD_MESSAGE_COUPON_UNAVAILABLE,
    REWARD_POLICY_DEFAULTS,
)
from app.experience.models import (
    ScienceCoupon,
    ScienceCouponIssuance,
    ScienceCouponRedemption,
    ScienceQuizAttempt,
    ScienceRewardPolicy,
    ScienceSession,
)

REWARD_STATE_NOT_REACHED = "not_reached"
REWARD_STATE_COUPON_ISSUED = "coupon_issued"
REWARD_STATE_REACHED_NO_COUPON = "reached_no_coupon"
REWARD_REASON_POLICY_INACTIVE = "reward_policy_inactive"
REWARD_REASON_COUPON_UNAVAILABLE = "coupon_unavailable"


class RewardRuntimeService:
    def __init__(self, *, db, context):
        self.db = db
        self.context = context

    async def get_or_init_reward_policy(self) -> ScienceRewardPolicy:
        result = await self.db.execute(
            select(ScienceRewardPolicy).where(
                ScienceRewardPolicy.workspace_id == self.context.workspace_id
            )
        )
        policy = result.scalar_one_or_none()
        if policy:
            return policy

        policy = ScienceRewardPolicy(
            workspace_id=self.context.workspace_id,
            **REWARD_POLICY_DEFAULTS,
        )
        self.db.add(policy)
        await self.db.flush()
        return policy

    async def count_correct_answers(self, session_id: str) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(ScienceQuizAttempt)
            .where(
                ScienceQuizAttempt.workspace_id == self.context.workspace_id,
                ScienceQuizAttempt.session_id == session_id,
                ScienceQuizAttempt.is_correct.is_(True),
            )
        )
        return int(result.scalar() or 0)

    async def count_issuances(self, session_id: str) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(ScienceCouponIssuance)
            .where(
                ScienceCouponIssuance.workspace_id == self.context.workspace_id,
                ScienceCouponIssuance.session_id == session_id,
            )
        )
        return int(result.scalar() or 0)

    async def choose_coupon(self, policy: ScienceRewardPolicy) -> Optional[ScienceCoupon]:
        now = datetime.utcnow()
        stmt = select(ScienceCoupon).where(
            ScienceCoupon.workspace_id == self.context.workspace_id,
            ScienceCoupon.status == "active",
        )
        if policy.coupon_id:
            stmt = stmt.where(ScienceCoupon.id == policy.coupon_id)
        stmt = stmt.order_by(ScienceCoupon.created_at.desc())
        result = await self.db.execute(stmt)
        coupons = list(result.scalars().all())
        for coupon in coupons:
            if coupon.expires_at and coupon.expires_at < now:
                continue
            if coupon.total_stock > 0 and coupon.issued_count >= coupon.total_stock:
                continue
            return coupon
        return None

    async def issue_coupon_if_eligible(
        self,
        *,
        session_id: str,
        policy: ScienceRewardPolicy,
        required_correct_count: Optional[int] = None,
    ) -> Optional[ScienceCouponIssuance]:
        await self.db.execute(
            select(ScienceSession.id)
            .where(
                ScienceSession.id == session_id,
                ScienceSession.workspace_id == self.context.workspace_id,
            )
            .with_for_update()
        )

        if not policy.is_active:
            return None

        if policy.max_claims_per_period > 0:
            period_start = datetime.utcnow() - timedelta(hours=policy.period_hours)
            claims_result = await self.db.execute(
                select(func.count())
                .select_from(ScienceCouponIssuance)
                .where(
                    ScienceCouponIssuance.workspace_id == self.context.workspace_id,
                    ScienceCouponIssuance.device_id == self.context.device_id,
                    ScienceCouponIssuance.issued_at >= period_start,
                )
            )
            if int(claims_result.scalar() or 0) >= policy.max_claims_per_period:
                return None

        correct_count = await self.count_correct_answers(session_id)
        issued_count = await self.count_issuances(session_id)
        threshold = max(required_correct_count or policy.required_correct_count, 1)
        should_have = correct_count // threshold
        if should_have <= issued_count:
            return None

        coupon = await self.choose_coupon(policy)
        if not coupon:
            return None

        issuance = ScienceCouponIssuance(
            workspace_id=self.context.workspace_id,
            coupon_id=coupon.id,
            session_id=session_id,
            device_id=self.context.device_id,
            coupon_code=f"{COUPON_CODE_PREFIX}-{secrets.token_hex(4).upper()}",
            status="issued",
            expires_at=datetime.utcnow() + timedelta(hours=policy.period_hours),
        )
        coupon.issued_count = (coupon.issued_count or 0) + 1
        self.db.add(issuance)
        await self.db.flush()
        return issuance

    async def resolve_reward_state(
        self,
        *,
        session_id: str,
        policy: ScienceRewardPolicy,
        correct_count: int,
        issuance: Optional[ScienceCouponIssuance],
        required_correct_count: Optional[int] = None,
    ) -> tuple[str, Optional[str], Optional[str]]:
        if issuance:
            return REWARD_STATE_COUPON_ISSUED, None, None

        if not policy.is_active:
            return REWARD_STATE_NOT_REACHED, REWARD_REASON_POLICY_INACTIVE, None

        threshold = max(required_correct_count or policy.required_correct_count, 1)
        issued_count = await self.count_issuances(session_id)
        should_have = correct_count // threshold
        if should_have > issued_count:
            return (
                REWARD_STATE_REACHED_NO_COUPON,
                REWARD_REASON_COUPON_UNAVAILABLE,
                REWARD_MESSAGE_COUPON_UNAVAILABLE,
            )

        return REWARD_STATE_NOT_REACHED, None, None

    async def get_coupon_qr(self, issuance_id: str) -> dict:
        result = await self.db.execute(
            select(ScienceCouponIssuance).where(
                ScienceCouponIssuance.id == issuance_id,
                ScienceCouponIssuance.workspace_id == self.context.workspace_id,
            )
        )
        issuance = result.scalar_one_or_none()
        if not issuance:
            raise HTTPException(status_code=404, detail="issuance_not_found")

        coupon_result = await self.db.execute(
            select(ScienceCoupon).where(
                ScienceCoupon.id == issuance.coupon_id,
                ScienceCoupon.workspace_id == self.context.workspace_id,
            )
        )
        coupon = coupon_result.scalar_one_or_none()

        payload = {
            "issuance_id": issuance.id,
            "coupon_code": issuance.coupon_code,
            "workspace_id": self.context.workspace_id,
        }
        qr_link = (coupon.redeem_link or "").strip() if coupon and coupon.redeem_link else None
        return {
            "issuance_id": issuance.id,
            "coupon_code": issuance.coupon_code,
            "status": issuance.status,
            "expires_at": issuance.expires_at,
            "qr_payload": qr_link or json.dumps(payload, ensure_ascii=False),
            "qr_link": qr_link,
        }

    async def redeem_coupon(
        self,
        *,
        issuance_id: Optional[str] = None,
        coupon_code: Optional[str] = None,
        note: Optional[str] = None,
        redeemed_by: Optional[str] = None,
    ) -> dict:
        if not issuance_id and not coupon_code:
            return {"success": False, "message": "issuance_id_or_coupon_code_required"}

        stmt = select(ScienceCouponIssuance).where(
            ScienceCouponIssuance.workspace_id == self.context.workspace_id
        ).with_for_update()
        if issuance_id:
            stmt = stmt.where(ScienceCouponIssuance.id == issuance_id)
        else:
            stmt = stmt.where(ScienceCouponIssuance.coupon_code == coupon_code)

        result = await self.db.execute(stmt)
        issuance = result.scalar_one_or_none()
        if not issuance:
            return {"success": False, "message": "coupon_not_found"}

        if issuance.status == "redeemed":
            return {
                "success": True,
                "message": "already_redeemed",
                "issuance_id": issuance.id,
                "coupon_code": issuance.coupon_code,
                "redeemed_at": issuance.redeemed_at,
            }

        if issuance.expires_at and issuance.expires_at < datetime.utcnow():
            issuance.status = "expired"
            return {"success": False, "message": "coupon_expired"}

        issuance.status = "redeemed"
        issuance.redeemed_at = datetime.utcnow()

        self.db.add(
            ScienceCouponRedemption(
                workspace_id=self.context.workspace_id,
                issuance_id=issuance.id,
                redeemed_by=redeemed_by,
                note=note,
            )
        )

        coupon_result = await self.db.execute(
            select(ScienceCoupon).where(
                ScienceCoupon.id == issuance.coupon_id,
                ScienceCoupon.workspace_id == self.context.workspace_id,
            )
        )
        coupon = coupon_result.scalar_one_or_none()
        if coupon:
            coupon.redeemed_count = (coupon.redeemed_count or 0) + 1

        return {
            "success": True,
            "message": "redeemed",
            "issuance_id": issuance.id,
            "coupon_code": issuance.coupon_code,
            "redeemed_at": issuance.redeemed_at,
        }
