"""Profile inference and wake-event runtime helpers."""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime
from typing import Optional

from app.core.voice.tts import get_tts_service
from app.experience.models import ScienceSession
from app.experience.services.runtime_utils import trace_id
from app.experience.services.startup_profile_service import get_startup_profile_service
from app.experience.services.voice_config_service import VoiceConfigService

logger = logging.getLogger(__name__)


class ProfileRuntimeService:
    def __init__(self, voice_config_service: VoiceConfigService):
        self.voice_config_service = voice_config_service

    async def infer_profile(
        self,
        *,
        image_bytes: bytes,
        content_type: str | None,
        session: ScienceSession | None = None,
        enable_tts: bool = True,
    ) -> dict:
        started_at = time.perf_counter()
        session_id = session.id if session else None
        logger.info(
            "[profile_runtime] start session_id=%s image_bytes=%s content_type=%s enable_tts=%s",
            session_id,
            len(image_bytes),
            content_type or "unknown",
            enable_tts,
        )
        startup_service = get_startup_profile_service()
        profile = await startup_service.infer_profile_and_intro(
            image_bytes=image_bytes,
            content_type=content_type,
        )
        trace = str(profile.get("trace_id") or trace_id())
        logger.info(
            "[profile_runtime] startup_profile_done trace_id=%s session_id=%s fallback=%s reason=%s",
            trace,
            session_id,
            bool(profile.get("fallback", False)),
            profile.get("reason"),
        )

        age_group = str(profile.get("age_group", "unknown") or "unknown")
        age_confidence = float(profile.get("age_confidence", 0.0) or 0.0)
        gender = str(profile.get("gender", "unknown") or "unknown")
        gender_confidence = float(profile.get("gender_confidence", 0.0) or 0.0)
        normalized_tags = list(profile.get("outfit_tags") or profile.get("feature_tags") or [])
        vibe_tags = list(profile.get("vibe_tags") or [])
        persona_text = str(profile.get("persona_text", "") or "").strip()
        welcome_text = str(profile.get("welcome_text", "") or "").strip()
        welcome_emotion = str(profile.get("welcome_emotion", "") or "").strip()
        tts_speaking_style = str(profile.get("tts_speaking_style", "") or "").strip()
        voice_key = await self.voice_config_service.resolve_voice_key(
            gender=gender,
            gender_confidence=gender_confidence,
            age_group=age_group,
        )

        spoken_text = welcome_text or persona_text
        tts_audio_base64 = None
        if enable_tts and spoken_text:
            logger.info(
                "[profile_runtime] tts_start trace_id=%s session_id=%s voice_key=%s emotion=%s style=%s spoken_chars=%s",
                trace,
                session_id,
                voice_key,
                welcome_emotion or "unknown",
                tts_speaking_style or "default",
                len(spoken_text),
            )
            tts_started_at = time.perf_counter()
            try:
                tts_service = get_tts_service()
                audio_bytes = await tts_service.synthesize(
                    spoken_text,
                    voice_id=voice_key,
                    speaking_style=tts_speaking_style or None,
                )
                if audio_bytes:
                    tts_audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
                logger.info(
                    "[profile_runtime] tts_done trace_id=%s session_id=%s elapsed_ms=%.1f audio_bytes=%s",
                    trace,
                    session_id,
                    (time.perf_counter() - tts_started_at) * 1000,
                    len(audio_bytes or b""),
                )
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "[profile_runtime] tts_failed trace_id=%s session_id=%s elapsed_ms=%.1f error=%s",
                    trace,
                    session_id,
                    (time.perf_counter() - tts_started_at) * 1000,
                    exc,
                )
        else:
            logger.info(
                "[profile_runtime] tts_skipped trace_id=%s session_id=%s enable_tts=%s spoken_chars=%s",
                trace,
                session_id,
                enable_tts,
                len(spoken_text),
            )

        result = {
            "event_type": "profile_inferred",
            "trace_id": trace,
            "age_group": age_group,
            "age_confidence": max(0.0, min(1.0, age_confidence)),
            "gender": gender,
            "gender_confidence": max(0.0, min(1.0, gender_confidence)),
            "outfit_tags": normalized_tags,
            "vibe_tags": vibe_tags,
            "feature_tags": list(profile.get("feature_tags") or normalized_tags),
            "persona_text": persona_text,
            "welcome_text": welcome_text,
            "welcome_emotion": welcome_emotion,
            "tts_voice": voice_key,
            "tts_speaking_style": tts_speaking_style,
            "tts_audio_base64": tts_audio_base64,
            "fallback": bool(profile.get("fallback", False)),
            "reason": profile.get("reason"),
        }

        if session:
            metadata = dict(session.metadata_json or {})
            metadata["profile"] = {
                "age_group": result["age_group"],
                "age_confidence": result["age_confidence"],
                "gender": result["gender"],
                "gender_confidence": result["gender_confidence"],
                "outfit_tags": result["outfit_tags"],
                "vibe_tags": result["vibe_tags"],
                "feature_tags": result["feature_tags"],
                "persona_text": result["persona_text"],
                "welcome_text": result["welcome_text"],
                "welcome_emotion": result["welcome_emotion"],
                "tts_voice": result["tts_voice"],
                "tts_speaking_style": result["tts_speaking_style"],
                "inferred_at": datetime.utcnow().isoformat(),
            }
            metadata["feature_tags"] = result["feature_tags"]
            metadata["gender"] = result["gender"]
            metadata["gender_confidence"] = result["gender_confidence"]
            session.metadata_json = metadata
            await self.voice_config_service.db.flush()

        logger.info(
            "[profile_runtime] done trace_id=%s session_id=%s elapsed_ms=%.1f fallback=%s tts_audio=%s",
            trace,
            session_id,
            (time.perf_counter() - started_at) * 1000,
            result["fallback"],
            bool(result["tts_audio_base64"]),
        )
        return result

    async def apply_wake_event(
        self,
        *,
        session: ScienceSession,
        wakeword: Optional[str],
        gender: Optional[str],
        gender_confidence: Optional[float],
        feature_tags: Optional[list[str]] = None,
        feature_limit: int = 5,
    ) -> dict:
        metadata = dict(session.metadata_json or {})
        profile = metadata.get("profile")
        if not isinstance(profile, dict):
            profile = {}

        if gender is not None:
            profile["gender"] = gender
        if gender_confidence is not None:
            profile["gender_confidence"] = max(0.0, min(1.0, float(gender_confidence)))
        if feature_tags is not None:
            normalized_tags: list[str] = []
            for tag in feature_tags:
                if not isinstance(tag, str):
                    continue
                clean = tag.strip()
                if not clean or clean in normalized_tags:
                    continue
                normalized_tags.append(clean)
                if len(normalized_tags) >= max(feature_limit, 1):
                    break
            profile["feature_tags"] = normalized_tags
            metadata["feature_tags"] = normalized_tags

        profile["inferred_at"] = datetime.utcnow().isoformat()
        metadata["profile"] = profile
        metadata["wakeword"] = wakeword
        metadata["gender"] = profile.get("gender")
        metadata["gender_confidence"] = profile.get("gender_confidence")
        metadata["wake_at"] = datetime.utcnow().isoformat()
        session.metadata_json = metadata
        await self.voice_config_service.db.flush()

        return {
            "event_type": "wake",
            "session_id": session.id,
            "trace_id": trace_id(),
            "payload": metadata,
        }
