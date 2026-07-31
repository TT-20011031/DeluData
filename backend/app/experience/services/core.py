"""Core orchestration service for guide/quiz/reward flows."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.llm.async_llm import get_async_llm
from app.experience.deps import ExperienceContext
from app.experience.models import ScienceSession
from app.models.common.context import UserContext
from app.skills.doc_skill import get_doc_skill

from .answer_runtime_service import AnswerRuntimeService
from .profile_runtime_service import ProfileRuntimeService
from .quiz_runtime_service import QuizRuntimeService
from .reward_runtime_service import RewardRuntimeService
from .voice_config_service import VoiceConfigService


class ExperienceRuntimeService:
    """Service facade for experience endpoints."""

    def __init__(
        self,
        db: AsyncSession,
        context: ExperienceContext,
        user_context: UserContext,
    ):
        self.db = db
        self.context = context
        self.user_context = user_context
        self.doc_skill = get_doc_skill()
        self.llm = get_async_llm()
        self.settings = get_settings()

        self.voice_config_service = VoiceConfigService(db, context.workspace_id)
        self.profile_runtime_service = ProfileRuntimeService(self.voice_config_service)
        self.answer_runtime_service = AnswerRuntimeService(
            db=db,
            context=context,
            user_context=user_context,
            settings=self.settings,
            llm=self.llm,
            doc_skill=self.doc_skill,
            voice_config_service=self.voice_config_service,
        )
        self.reward_runtime_service = RewardRuntimeService(db=db, context=context)
        self.quiz_runtime_service = QuizRuntimeService(
            db=db,
            context=context,
            settings=self.settings,
            llm=self.llm,
            reward_service=self.reward_runtime_service,
        )

    async def start_session(self, visitor_id: Optional[str] = None) -> ScienceSession:
        session = ScienceSession(
            workspace_id=self.context.workspace_id,
            device_id=self.context.device_id,
            visitor_id=visitor_id,
            status="active",
            metadata_json={},
        )
        self.db.add(session)
        await self.db.flush()
        await self.db.refresh(session)
        return session

    async def get_session_or_404(self, session_id: str) -> ScienceSession:
        result = await self.db.execute(
            select(ScienceSession).where(
                ScienceSession.id == session_id,
                ScienceSession.workspace_id == self.context.workspace_id,
                ScienceSession.device_id == self.context.device_id,
            )
        )
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="session_not_found")
        return session

    async def touch_session(self, session: ScienceSession) -> None:
        session.last_seen_at = datetime.utcnow()
        await self.db.flush()

    async def get_or_init_voice_profile(self):
        return await self.voice_config_service.get_or_init_voice_profile()

    async def handle_profile_infer(
        self,
        *,
        image_bytes: bytes,
        content_type: str | None,
        session_id: Optional[str] = None,
        enable_tts: bool = True,
    ) -> dict:
        session = None
        if session_id:
            session = await self.get_session_or_404(session_id)
            await self.touch_session(session)
        return await self.profile_runtime_service.infer_profile(
            image_bytes=image_bytes,
            content_type=content_type,
            session=session,
            enable_tts=enable_tts,
        )

    async def apply_wake_event(
        self,
        session_id: str,
        wakeword: Optional[str],
        gender: Optional[str],
        gender_confidence: Optional[float],
        feature_tags: Optional[list[str]] = None,
    ) -> dict:
        session = await self.get_session_or_404(session_id)
        await self.touch_session(session)
        return await self.profile_runtime_service.apply_wake_event(
            session=session,
            wakeword=wakeword,
            gender=gender,
            gender_confidence=gender_confidence,
            feature_tags=feature_tags,
            feature_limit=max(int(self.settings.experience.profile_feature_max_count), 1),
        )

    async def handle_asr_turn(
        self,
        session_id: str,
        text: str,
        enable_tts: bool,
        age_group: Optional[str],
        gender: Optional[str],
        gender_confidence: Optional[float],
    ) -> dict:
        session = await self.get_session_or_404(session_id)
        await self.touch_session(session)
        return await self.answer_runtime_service.handle_asr_turn(
            session=session,
            text=text,
            enable_tts=enable_tts,
            age_group=age_group,
            gender=gender,
            gender_confidence=gender_confidence,
        )

    async def generate_quiz_questions(
        self,
        *,
        topic: str,
        count: int,
        difficulty: int,
    ):
        return await self.quiz_runtime_service.generate_quiz_questions(
            topic=topic,
            count=count,
            difficulty=difficulty,
        )

    async def start_quiz(
        self,
        session_id: str,
        excluded_question_ids: Optional[list[str]] = None,
    ) -> dict:
        session = await self.get_session_or_404(session_id)
        await self.touch_session(session)
        return await self.quiz_runtime_service.start_quiz(
            session=session,
            excluded_question_ids=excluded_question_ids,
        )

    async def get_or_init_reward_policy(self):
        return await self.reward_runtime_service.get_or_init_reward_policy()

    async def submit_quiz_answer(
        self, session_id: str, question_id: str, answer: str
    ) -> dict:
        session = await self.get_session_or_404(session_id)
        await self.touch_session(session)
        return await self.quiz_runtime_service.submit_quiz_answer(
            session=session,
            question_id=question_id,
            answer=answer,
        )

    async def get_coupon_qr(self, issuance_id: str) -> dict:
        return await self.reward_runtime_service.get_coupon_qr(issuance_id)

    async def redeem_coupon(
        self,
        *,
        issuance_id: Optional[str] = None,
        coupon_code: Optional[str] = None,
        note: Optional[str] = None,
        redeemed_by: Optional[str] = None,
    ) -> dict:
        return await self.reward_runtime_service.redeem_coupon(
            issuance_id=issuance_id,
            coupon_code=coupon_code,
            note=note,
            redeemed_by=redeemed_by,
        )

    async def get_or_init_wakeword_config(self):
        return await self.voice_config_service.get_or_init_wakeword_config()
