"""Voice and wakeword configuration helpers."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.experience.defaults import VOICE_PROFILE_DEFAULTS, WAKEWORD_CONFIG_DEFAULTS
from app.experience.models import ScienceVoiceProfile, ScienceWakewordConfig


class VoiceConfigService:
    def __init__(self, db: AsyncSession, workspace_id: str):
        self.db = db
        self.workspace_id = workspace_id

    async def get_or_init_voice_profile(self) -> ScienceVoiceProfile:
        result = await self.db.execute(
            select(ScienceVoiceProfile).where(
                ScienceVoiceProfile.workspace_id == self.workspace_id
            )
        )
        profile = result.scalar_one_or_none()
        if profile:
            return profile

        profile = ScienceVoiceProfile(
            workspace_id=self.workspace_id,
            **VOICE_PROFILE_DEFAULTS,
        )
        self.db.add(profile)
        await self.db.flush()
        return profile

    async def resolve_voice_key(
        self,
        *,
        gender: Optional[str] = None,
        gender_confidence: Optional[float] = None,
        age_group: Optional[str] = None,
    ) -> str:
        profile = await self.get_or_init_voice_profile()

        if age_group == "child" and profile.child_voice:
            return profile.child_voice
        if age_group == "adult" and profile.adult_voice:
            return profile.adult_voice
        if age_group == "elder" and profile.elder_voice:
            return profile.elder_voice

        if gender and gender_confidence is not None:
            if gender_confidence >= profile.gender_confidence_threshold:
                if gender == "female" and profile.female_voice:
                    return profile.female_voice
                if gender == "male" and profile.male_voice:
                    return profile.male_voice

        return profile.default_voice

    async def get_or_init_wakeword_config(self) -> ScienceWakewordConfig:
        result = await self.db.execute(
            select(ScienceWakewordConfig).where(
                ScienceWakewordConfig.workspace_id == self.workspace_id
            )
        )
        config = result.scalar_one_or_none()
        if config:
            return config

        config = ScienceWakewordConfig(
            workspace_id=self.workspace_id,
            **WAKEWORD_CONFIG_DEFAULTS,
        )
        self.db.add(config)
        await self.db.flush()
        return config
