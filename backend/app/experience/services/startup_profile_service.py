"""Startup profile inference via VL plus text-only welcome generation."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from typing import Any

from app.config import get_settings
from app.core.llm.async_llm import get_async_llm
from app.experience.defaults import STARTUP_PROFILE_FALLBACK_PERSONA_TEXT

logger = logging.getLogger(__name__)


class StartupProfileService:
    ALLOWED_AGE_GROUPS = {"child", "adult", "elder", "unknown"}
    ALLOWED_GENDERS = {"female", "male", "unknown"}
    WELCOME_MIN_CHARS = 18
    WELCOME_MAX_CHARS = 32
    VIBE_MAX_COUNT = 3

    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = get_async_llm()

    async def infer_profile_and_intro(
        self,
        *,
        image_bytes: bytes,
        content_type: str | None,
    ) -> dict[str, Any]:
        request_trace_id = uuid.uuid4().hex
        if not image_bytes:
            logger.warning(
                "[startup_profile] empty image payload trace_id=%s content_type=%s",
                request_trace_id,
                content_type,
            )
            return self._fallback("empty_image_payload", trace_id=request_trace_id)

        started_at = time.perf_counter()
        vision_timeout = max(
            float(getattr(self.settings.experience, "profile_intro_timeout_sec", 4.0)),
            0.5,
        )
        welcome_timeout = max(
            float(getattr(self.settings.experience, "profile_welcome_timeout_sec", 2.5)),
            0.5,
        )
        vision_model = (
            self.settings.experience.profile_model
            or self.settings.vlm.model
            or self.settings.llm.fast_model
        )
        welcome_model = (
            self.settings.experience.profile_welcome_model
            or self.settings.llm.fast_model
        )
        vision_max_tokens = max(
            min(int(self.settings.experience.profile_vision_max_tokens), 800),
            128,
        )
        welcome_max_tokens = max(
            min(int(self.settings.experience.profile_welcome_max_tokens), 400),
            96,
        )
        logger.info(
            "[startup_profile] start trace_id=%s bytes=%s content_type=%s vision_timeout_sec=%.2f welcome_timeout_sec=%.2f vision_model=%s welcome_model=%s",
            request_trace_id,
            len(image_bytes),
            content_type or "unknown",
            vision_timeout,
            welcome_timeout,
            vision_model,
            welcome_model,
        )

        try:
            raw_visual_profile = await asyncio.wait_for(
                self._infer_visual_profile(
                    image_bytes=image_bytes,
                    content_type=content_type,
                    trace_id=request_trace_id,
                    model_name=vision_model,
                    max_tokens=vision_max_tokens,
                ),
                timeout=vision_timeout,
            )
        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "[startup_profile] vision_timeout trace_id=%s timeout_sec=%.2f elapsed_ms=%.1f",
                request_trace_id,
                vision_timeout,
                elapsed_ms,
            )
            return self._fallback("startup_profile_timeout", trace_id=request_trace_id)
        except Exception as exc:  # pragma: no cover
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "[startup_profile] vision_failed trace_id=%s elapsed_ms=%.1f error=%s",
                request_trace_id,
                elapsed_ms,
                exc,
            )
            return self._fallback("startup_profile_failed", trace_id=request_trace_id)

        core_profile = self._normalize_core(raw_visual_profile)
        welcome_reason: str | None = None
        try:
            raw_welcome_plan = await asyncio.wait_for(
                self._generate_welcome_plan(
                    profile=core_profile,
                    trace_id=request_trace_id,
                    model_name=welcome_model,
                    max_tokens=welcome_max_tokens,
                ),
                timeout=welcome_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[startup_profile] welcome_timeout trace_id=%s timeout_sec=%.2f",
                request_trace_id,
                welcome_timeout,
            )
            raw_welcome_plan = {}
            welcome_reason = "welcome_generation_timeout"
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "[startup_profile] welcome_failed trace_id=%s error=%s",
                request_trace_id,
                exc,
            )
            raw_welcome_plan = {}
            welcome_reason = "welcome_generation_failed"

        normalized = self._normalize(raw_visual_profile, raw_welcome_plan)
        normalized["trace_id"] = request_trace_id
        if welcome_reason:
            normalized["reason"] = welcome_reason

        total_elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "[startup_profile] normalized trace_id=%s elapsed_ms=%.1f fallback=%s age_group=%s gender=%s outfit_tags=%s vibe_tags=%s welcome_emotion=%s welcome_chars=%s",
            request_trace_id,
            total_elapsed_ms,
            normalized.get("fallback", False),
            normalized.get("age_group"),
            normalized.get("gender"),
            len(normalized.get("outfit_tags") or []),
            len(normalized.get("vibe_tags") or []),
            normalized.get("welcome_emotion"),
            len(normalized.get("welcome_text") or ""),
        )
        return normalized

    async def _infer_visual_profile(
        self,
        *,
        image_bytes: bytes,
        content_type: str | None,
        trace_id: str,
        model_name: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        mime = self._sanitize_mime(content_type)
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = self.settings.experience.profile_intro_prompt
        if "{feature_limit}" in prompt:
            prompt = prompt.replace(
                "{feature_limit}",
                str(max(int(self.settings.experience.profile_feature_max_count), 1)),
            )
        logger.info(
            "[startup_profile] vision_request trace_id=%s mime=%s image_base64_chars=%s prompt_chars=%s",
            trace_id,
            mime,
            len(image_base64),
            len(prompt),
        )

        started_at = time.perf_counter()
        raw = await self.llm.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"image": f"data:{mime};base64,{image_base64}"},
                        {"text": prompt},
                    ],
                }
            ],
            model=model_name,
            max_tokens=max_tokens,
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "[startup_profile] vision_response trace_id=%s elapsed_ms=%.1f raw_chars=%s",
            trace_id,
            elapsed_ms,
            len(raw or ""),
        )

        parsed = self._extract_json(raw, trace_id=trace_id, stage="vision")
        logger.info(
            "[startup_profile] vision_json trace_id=%s keys=%s",
            trace_id,
            sorted(parsed.keys()),
        )
        return parsed

    async def _generate_welcome_plan(
        self,
        *,
        profile: dict[str, Any],
        trace_id: str,
        model_name: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        prompt = self.settings.experience.profile_welcome_prompt
        profile_json = json.dumps(
            {
                "age_group": profile.get("age_group", "unknown"),
                "gender": profile.get("gender", "unknown"),
                "outfit_tags": profile.get("outfit_tags", []),
                "vibe_tags": profile.get("vibe_tags", []),
                "feature_tags": profile.get("feature_tags", []),
                "persona_text": profile.get("persona_text", ""),
            },
            ensure_ascii=False,
        )
        prompt = f"{prompt}\n\n游客画像:\n{profile_json}"
        logger.info(
            "[startup_profile] welcome_request trace_id=%s profile_chars=%s prompt_chars=%s",
            trace_id,
            len(profile_json),
            len(prompt),
        )

        started_at = time.perf_counter()
        request_kwargs: dict[str, Any] = {}
        if not bool(getattr(self.settings.experience, "profile_welcome_enable_thinking", False)):
            request_kwargs["extra_body"] = {"enable_thinking": False}
        raw = await self.llm.chat(
            [{"role": "user", "content": prompt}],
            model=model_name,
            max_tokens=max_tokens,
            **request_kwargs,
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "[startup_profile] welcome_response trace_id=%s elapsed_ms=%.1f raw_chars=%s",
            trace_id,
            elapsed_ms,
            len(raw or ""),
        )

        parsed = self._extract_json(raw, trace_id=trace_id, stage="welcome")
        logger.info(
            "[startup_profile] welcome_json trace_id=%s keys=%s",
            trace_id,
            sorted(parsed.keys()),
        )
        return parsed

    def _normalize(self, payload: dict[str, Any], welcome_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        core = self._normalize_core(payload)
        welcome_payload = welcome_payload or {}
        welcome_emotion = self._normalize_emotion(
            welcome_payload.get("welcome_emotion"),
            age_group=core["age_group"],
            vibe_tags=core["vibe_tags"],
        )
        welcome_text = self._normalize_welcome_text(
            str(welcome_payload.get("welcome_text", "")).strip(),
            age_group=core["age_group"],
            feature_tags=core["feature_tags"],
        )
        if not welcome_text:
            welcome_text = self._build_personalized_welcome(
                age_group=core["age_group"],
                feature_tags=core["feature_tags"],
            )

        return {
            **core,
            "welcome_text": welcome_text,
            "welcome_emotion": welcome_emotion,
            "tts_speaking_style": self._resolve_speaking_style(welcome_emotion),
            "fallback": False,
            "reason": None,
        }

    def _normalize_core(self, payload: dict[str, Any]) -> dict[str, Any]:
        age_group = str(payload.get("age_group", "unknown")).strip().lower()
        if age_group not in self.ALLOWED_AGE_GROUPS:
            age_group = "unknown"
        age_confidence = self._safe_float(payload.get("age_confidence"), default=0.0)
        age_confidence = max(0.0, min(1.0, age_confidence))

        gender = str(payload.get("gender", "unknown")).strip().lower()
        if gender not in self.ALLOWED_GENDERS:
            gender = "unknown"
        gender_confidence = self._safe_float(payload.get("gender_confidence"), default=0.0)
        gender_confidence = max(0.0, min(1.0, gender_confidence))

        feature_limit = max(int(self.settings.experience.profile_feature_max_count), 1)
        outfit_tags = self._collect_tags(
            payload.get("outfit_tags") or payload.get("outfit_traits"),
            limit=feature_limit,
        )
        vibe_tags = self._collect_tags(
            payload.get("vibe_tags") or payload.get("temperament_tags") or payload.get("persona_tags"),
            limit=self.VIBE_MAX_COUNT,
        )
        feature_tags: list[str] = []
        for item in outfit_tags + vibe_tags:
            if item in feature_tags:
                continue
            feature_tags.append(item)
            if len(feature_tags) >= feature_limit:
                break

        persona_text = self._build_persona_text(
            age_group=age_group,
            outfit_tags=outfit_tags,
            vibe_tags=vibe_tags,
        )
        if not persona_text:
            persona_text = STARTUP_PROFILE_FALLBACK_PERSONA_TEXT

        return {
            "age_group": age_group,
            "age_confidence": age_confidence,
            "gender": gender,
            "gender_confidence": gender_confidence,
            "outfit_tags": outfit_tags,
            "vibe_tags": vibe_tags,
            "feature_tags": feature_tags,
            "persona_text": persona_text,
        }

    def _collect_tags(self, raw_items: Any, *, limit: int) -> list[str]:
        tags: list[str] = []
        if not isinstance(raw_items, list):
            return tags
        for item in raw_items:
            if not isinstance(item, str):
                continue
            text = re.sub(r"\s+", "", item).strip("，,。；;、 ")
            if not text or text in tags:
                continue
            if len(text) > 24:
                continue
            tags.append(text)
            if len(tags) >= limit:
                break
        return tags

    def _extract_json(self, text: str, *, trace_id: str, stage: str) -> dict[str, Any]:
        if not text:
            logger.warning("[startup_profile] empty %s response trace_id=%s", stage, trace_id)
            return {}
        content = text.strip()
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
        if fence:
            content = fence.group(1).strip()
        else:
            left = content.find("{")
            right = content.rfind("}")
            if left >= 0 and right >= left:
                content = content[left : right + 1]

        try:
            value = json.loads(content)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            logger.warning(
                "[startup_profile] %s json parse failed trace_id=%s content_preview=%s",
                stage,
                trace_id,
                content[:240],
            )
            return {}

    @staticmethod
    def _sanitize_mime(content_type: str | None) -> str:
        if not content_type:
            return "image/jpeg"
        lowered = content_type.strip().lower()
        return lowered if lowered.startswith("image/") else "image/jpeg"

    def _normalize_welcome_text(
        self,
        text: str,
        *,
        age_group: str,
        feature_tags: list[str],
    ) -> str:
        cleaned = re.sub(r"\s+", "", text).strip("“”\"'")
        if not cleaned:
            return ""
        trailing_punctuation = ""
        if cleaned.endswith("？"):
            trailing_punctuation = "？"
        elif cleaned.endswith("！"):
            trailing_punctuation = "！"
        elif cleaned.endswith("。"):
            trailing_punctuation = "。"

        sentence_parts = [
            part.strip("，,。！？!；;、 ")
            for part in re.split(r"[。！？!?\n]", cleaned)
            if part.strip("，,。！？!；;、 ")
        ]
        if sentence_parts:
            cleaned = sentence_parts[0]

        if len(cleaned) > self.WELCOME_MAX_CHARS:
            clause_parts = [
                part.strip("，,；;、 ")
                for part in re.split(r"[，,；;、]", cleaned)
                if part.strip("，,；;、 ")
            ]
            selected_clause = next(
                (
                    part
                    for part in clause_parts
                    if self.WELCOME_MIN_CHARS <= len(part) <= self.WELCOME_MAX_CHARS
                ),
                "",
            )
            if selected_clause:
                cleaned = selected_clause
            else:
                return ""

        if len(cleaned) < self.WELCOME_MIN_CHARS or self._looks_too_generic(
            cleaned,
            feature_tags=feature_tags,
        ):
            return ""

        if trailing_punctuation and len(cleaned) < self.WELCOME_MAX_CHARS:
            cleaned = f"{cleaned}{trailing_punctuation}"

        return cleaned

    def _looks_too_generic(self, text: str, *, feature_tags: list[str]) -> bool:
        generic_markers = (
            "欢迎来到科技馆",
            "一起探索科学",
            "一起发现科学",
            "来和我一起玩科学",
            "看看今天的科学惊喜",
        )
        if not any(marker in text for marker in generic_markers):
            return False
        return not any(tag and tag in text for tag in feature_tags[:2])

    def _build_persona_text(
        self,
        *,
        age_group: str,
        outfit_tags: list[str],
        vibe_tags: list[str],
    ) -> str:
        lead_outfit = next((tag for tag in outfit_tags if len(tag) <= 8), "")
        lead_vibe = next((tag for tag in vibe_tags if len(tag) <= 4), "")

        if lead_outfit and lead_vibe:
            if age_group == "child":
                return f"穿着{lead_outfit}，活泼劲儿十足"
            if age_group == "elder":
                return f"穿着{lead_outfit}，气质{lead_vibe}亲切"
            return f"穿着{lead_outfit}，看起来{lead_vibe}十足"
        if lead_outfit:
            if age_group == "child":
                return f"穿着{lead_outfit}，像位小小探索家"
            if age_group == "elder":
                return f"穿着{lead_outfit}，像位从容观展的长者"
            return f"穿着{lead_outfit}，像位准备探索的访客"
        if lead_vibe:
            if age_group == "child":
                return f"看起来{lead_vibe}可爱，像位小小科学迷"
            if age_group == "elder":
                return f"看起来{lead_vibe}从容，像位沉稳访客"
            return f"看起来{lead_vibe}十足，准备大显身手"
        return STARTUP_PROFILE_FALLBACK_PERSONA_TEXT

    def _build_personalized_welcome(
        self,
        *,
        age_group: str,
        feature_tags: list[str],
    ) -> str:
        lead_tag = next(
            (tag for tag in feature_tags if isinstance(tag, str) and 1 < len(tag.strip()) <= 8),
            "",
        ).strip()

        if age_group == "child":
            return (
                f"{lead_tag}小朋友，想先问问题，还是来闯关答题？"
                if lead_tag
                else "小朋友，想先问问题，还是来闯关答题？"
            )
        if age_group == "elder":
            return (
                f"{lead_tag}老师，您想先听讲解，还是来答题试试？"
                if lead_tag
                else "欢迎您，想先听讲解，还是来答题试试？"
            )
        if age_group == "adult":
            return (
                f"{lead_tag}朋友，想问科学问题，还是来一题挑战？"
                if lead_tag
                else "朋友，想问科学问题，还是来一题挑战？"
            )
        return (
            f"{lead_tag}朋友，想先问问题，还是来答题挑战？"
            if lead_tag
            else self.settings.experience.profile_fallback_text
        )

    def _normalize_emotion(
        self,
        value: Any,
        *,
        age_group: str,
        vibe_tags: list[str],
    ) -> str:
        emotion_style_map = self.settings.experience.profile_emotion_style_map
        candidate = str(value or "").strip().lower()
        if candidate in emotion_style_map:
            return candidate

        joined_tags = "".join(vibe_tags)
        if any(marker in joined_tags for marker in ("活泼", "可爱", "俏皮", "童趣")):
            return "playful"
        if any(marker in joined_tags for marker in ("热情", "元气", "精神", "亮眼", "自信")):
            return "energetic"
        if any(marker in joined_tags for marker in ("好奇", "专注", "认真")):
            return "curious"
        if any(marker in joined_tags for marker in ("沉稳", "从容", "安静", "淡定")):
            return "warm" if age_group == "elder" else "calm"

        default_map = self.settings.experience.profile_default_emotion_by_age
        return str(default_map.get(age_group) or default_map.get("unknown") or "friendly")

    def _resolve_speaking_style(self, emotion: str) -> str:
        style = self.settings.experience.profile_emotion_style_map.get(emotion)
        return str(style or "").strip()

    def _fallback(self, reason: str, *, trace_id: str | None = None) -> dict[str, Any]:
        resolved_trace_id = trace_id or uuid.uuid4().hex
        welcome_emotion = self._normalize_emotion(
            None,
            age_group="unknown",
            vibe_tags=[],
        )
        logger.info(
            "[startup_profile] fallback trace_id=%s reason=%s",
            resolved_trace_id,
            reason,
        )
        return {
            "age_group": "unknown",
            "age_confidence": 0.0,
            "gender": "unknown",
            "gender_confidence": 0.0,
            "outfit_tags": [],
            "vibe_tags": [],
            "feature_tags": [],
            "persona_text": STARTUP_PROFILE_FALLBACK_PERSONA_TEXT,
            "welcome_text": self.settings.experience.profile_fallback_text,
            "welcome_emotion": welcome_emotion,
            "tts_speaking_style": self._resolve_speaking_style(welcome_emotion),
            "trace_id": resolved_trace_id,
            "fallback": True,
            "reason": reason,
        }

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


_startup_profile_service: StartupProfileService | None = None


def get_startup_profile_service() -> StartupProfileService:
    global _startup_profile_service
    if _startup_profile_service is None:
        _startup_profile_service = StartupProfileService()
    return _startup_profile_service
