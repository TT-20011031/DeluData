"""Answer-generation runtime helpers."""

from __future__ import annotations

import base64
import logging
import re
from typing import Optional

from app.core.voice.tts import get_tts_service
from app.experience.models import ScienceSession
from app.experience.services.runtime_utils import trace_id
from app.experience.services.scope_service import WorkspaceScopeService
from app.experience.services.voice_config_service import VoiceConfigService

logger = logging.getLogger(__name__)

CONVERSATION_MEMORY_KEY = "conversation"
RECENT_TURNS_KEY = "recent_turns"
FOLLOW_UP_PATTERNS = (
    r"^(它|这个|那个|这|那)(个|种|里|是|有|会|能|可以|怎么|为什么|是不是)?",
    r"^(为什么|为啥|怎么|怎样|如何|原理|什么意思|然后呢|然后|还有呢|还有|继续|再说说|会不会|能不能|可以吗|是不是|是吗|对吗)",
)

AGE_GROUP_LABELS = {
    "child": "小朋友",
    "adult": "成年人",
    "elder": "长者",
    "unknown": "未识别",
}

GENDER_LABELS = {
    "female": "女性特征",
    "male": "男性特征",
    "unknown": "未识别",
}


class AnswerRuntimeService:
    def __init__(
        self,
        *,
        db,
        context,
        user_context,
        settings,
        llm,
        doc_skill,
        voice_config_service: VoiceConfigService,
    ):
        self.db = db
        self.context = context
        self.user_context = user_context
        self.settings = settings
        self.llm = llm
        self.doc_skill = doc_skill
        self.voice_config_service = voice_config_service

    def _get_answer_length_limits(self) -> tuple[int, int]:
        max_chars = max(int(getattr(self.settings.experience, "answer_max_chars", 50)), 20)
        min_chars = max(int(getattr(self.settings.experience, "answer_target_min_chars", 30)), 1)
        return min(min_chars, max_chars), max_chars

    def _get_memory_turn_limit(self) -> int:
        return max(int(getattr(self.settings.experience, "answer_memory_turn_limit", 4) or 4), 1)

    def _get_memory_context_chars(self) -> int:
        return max(int(getattr(self.settings.experience, "answer_memory_context_chars", 400) or 400), 120)

    def _normalize_turn_text(self, value: object, *, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        return text[:limit]

    def _get_recent_turns(self, session: ScienceSession) -> list[dict]:
        metadata = session.metadata_json if isinstance(session.metadata_json, dict) else {}
        conversation = metadata.get(CONVERSATION_MEMORY_KEY)
        if not isinstance(conversation, dict):
            return []

        turns: list[dict] = []
        for item in conversation.get(RECENT_TURNS_KEY) or []:
            if not isinstance(item, dict):
                continue
            user_text = self._normalize_turn_text(item.get("user_text"), limit=160)
            assistant_text = self._normalize_turn_text(item.get("assistant_text"), limit=240)
            resolved_query = self._normalize_turn_text(item.get("resolved_query"), limit=240)
            if not user_text and not assistant_text:
                continue
            turns.append(
                {
                    "user_text": user_text,
                    "assistant_text": assistant_text,
                    "resolved_query": resolved_query,
                }
            )
        return turns[-self._get_memory_turn_limit() :]

    def _format_recent_turns(self, recent_turns: list[dict]) -> str:
        if not recent_turns:
            return ""

        lines: list[str] = []
        for idx, turn in enumerate(recent_turns[-self._get_memory_turn_limit() :], start=1):
            user_text = self._normalize_turn_text(turn.get("user_text"), limit=80)
            assistant_text = self._normalize_turn_text(turn.get("assistant_text"), limit=120)
            if user_text:
                lines.append(f"游客{idx}: {user_text}")
            if assistant_text:
                lines.append(f"讲解员{idx}: {assistant_text}")

        return "\n".join(lines)[: self._get_memory_context_chars()]

    def _inject_recent_turns(self, prompt: str, recent_turns: list[dict]) -> str:
        history_block = self._format_recent_turns(recent_turns)
        if not history_block:
            return prompt
        return (
            "最近对话仅用于理解当前问题里的指代、延续关系和游客兴趣，"
            "不能替代可用内容中的事实依据，也不要把历史对话当成事实来源。\n"
            f"最近对话:\n{history_block}\n\n"
            f"{prompt}"
        )

    def _is_context_dependent_query(self, query: str) -> bool:
        normalized = re.sub(r"\s+", "", str(query or "").strip())
        if not normalized:
            return False
        if any(re.match(pattern, normalized) for pattern in FOLLOW_UP_PATTERNS):
            return True
        return len(normalized) <= 8 and normalized.endswith(("呢", "吗", "呀", "啊"))

    def _build_retrieval_query(self, query: str, recent_turns: list[dict]) -> str:
        normalized_query = self._normalize_turn_text(query, limit=120)
        if not normalized_query or not recent_turns or not self._is_context_dependent_query(normalized_query):
            return normalized_query

        parts: list[str] = []
        for turn in recent_turns[-2:]:
            user_text = self._normalize_turn_text(turn.get("user_text"), limit=40)
            assistant_text = self._normalize_turn_text(turn.get("assistant_text"), limit=80)
            if user_text:
                parts.append(f"上一轮问题：{user_text}")
            if assistant_text:
                parts.append(f"上一轮回答：{assistant_text}")
        parts.append(f"当前问题：{normalized_query}")
        return "；".join(parts)[:240]

    async def _remember_turn(
        self,
        *,
        session: ScienceSession,
        user_text: str,
        assistant_text: str,
        resolved_query: str,
    ) -> None:
        metadata = session.metadata_json if isinstance(session.metadata_json, dict) else {}
        updated_metadata = dict(metadata)
        conversation = updated_metadata.get(CONVERSATION_MEMORY_KEY)
        if not isinstance(conversation, dict):
            conversation = {}
        else:
            conversation = dict(conversation)

        recent_turns = self._get_recent_turns(session)
        recent_turns.append(
            {
                "user_text": self._normalize_turn_text(user_text, limit=160),
                "assistant_text": self._normalize_turn_text(assistant_text, limit=240),
                "resolved_query": self._normalize_turn_text(resolved_query, limit=240),
            }
        )
        conversation[RECENT_TURNS_KEY] = recent_turns[-self._get_memory_turn_limit() :]
        conversation["turn_count"] = max(int(conversation.get("turn_count") or 0), 0) + 1
        updated_metadata[CONVERSATION_MEMORY_KEY] = conversation
        session.metadata_json = updated_metadata
        if self.db is not None:
            await self.db.flush()

    def _normalize_answer_text(self, answer: str, *, fallback: str) -> str:
        normalized = re.sub(r"\s+", " ", str(answer or "").strip())
        if not normalized:
            normalized = fallback.strip()

        _, max_chars = self._get_answer_length_limits()
        if len(normalized) <= max_chars:
            return normalized

        clipped = normalized[:max_chars].rstrip("，,；;、 ")
        if clipped and len(clipped) < max_chars and clipped[-1] not in "。！？!?":
            clipped = f"{clipped}。"
        return clipped

    def _build_profile_summary(self, session: ScienceSession) -> str:
        metadata = session.metadata_json or {}
        if not isinstance(metadata, dict):
            return "未采集到明显游客画像"

        profile = metadata.get("profile")
        if not isinstance(profile, dict):
            return "未采集到明显游客画像"

        parts: list[str] = []
        age_group = AGE_GROUP_LABELS.get(str(profile.get("age_group") or "").strip().lower())
        gender = GENDER_LABELS.get(str(profile.get("gender") or "").strip().lower())
        if age_group and age_group != "未识别":
            parts.append(f"年龄段：{age_group}")
        if gender and gender != "未识别":
            parts.append(f"外在特征：{gender}")

        feature_tags: list[str] = []
        for item in profile.get("feature_tags") or profile.get("outfit_tags") or []:
            if not isinstance(item, str):
                continue
            clean = item.strip()
            if not clean or clean in feature_tags:
                continue
            feature_tags.append(clean)
            if len(feature_tags) >= 3:
                break
        if feature_tags:
            parts.append(f"显著特点：{'、'.join(feature_tags)}")

        persona_text = str(profile.get("persona_text") or "").strip()
        if persona_text:
            parts.append(f"讲解风格：{persona_text[:24]}")

        return "；".join(parts) or "未采集到明显游客画像"

    def _build_scope_summary(self, retrieval_scope: dict) -> str:
        mode = str(retrieval_scope.get("mode") or "workspace")
        if mode == "files":
            label = "指定资料范围"
        elif mode == "dept":
            label = "部门知识范围"
        else:
            label = "全馆知识范围"

        details: list[str] = []
        file_count = int(retrieval_scope.get("file_count") or 0)
        dept_count = int(retrieval_scope.get("dept_count") or 0)
        visibility_count = int(retrieval_scope.get("visibility_count") or 0)
        if file_count > 0:
            details.append(f"{file_count} 份资料")
        if dept_count > 0:
            details.append(f"{dept_count} 个部门")
        if visibility_count > 0:
            details.append(f"{visibility_count} 类可见范围")

        if details:
            return f"{label}（{'，'.join(details)}）"
        return label

    async def _generate_answer(self, *, prompt: str, fallback_text: str, log_label: str) -> str:
        try:
            answer = await self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": self.settings.experience.answer_system_prompt,
                    },
                    {"role": "user", "content": prompt},
                ],
                model=self.settings.llm.fast_model,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("[answer_runtime] %s generation failed: %s", log_label, exc)
            answer = fallback_text
        return self._normalize_answer_text(answer, fallback=fallback_text)

    async def build_answer_from_chunks(
        self,
        *,
        session: ScienceSession,
        query: str,
        chunks: list,
        retrieval_scope: dict,
        recent_turns: Optional[list[dict]] = None,
    ) -> tuple[str, bool]:
        recent_turns = recent_turns if recent_turns is not None else self._get_recent_turns(session)
        min_chars, max_chars = self._get_answer_length_limits()
        if not chunks:
            prompt = self.settings.experience.answer_empty_context_prompt.format(
                query=query,
                profile_summary=self._build_profile_summary(session),
                scope_summary=self._build_scope_summary(retrieval_scope),
                min_chars=min_chars,
                max_chars=max_chars,
            )
            prompt = self._inject_recent_turns(prompt, recent_turns)
            answer = await self._generate_answer(
                prompt=prompt,
                fallback_text=self.settings.experience.answer_no_context_message,
                log_label="empty_context",
            )
            return answer, True

        context_lines = []
        for idx, chunk in enumerate(chunks[:5], start=1):
            text = (chunk.content or "").strip()
            if not text:
                continue
            context_lines.append(f"{idx}. {text[:600]}")

        context_text = "\n".join(context_lines)[:3000]
        prompt = self.settings.experience.answer_prompt_template.format(
            query=query,
            context=context_text,
            min_chars=min_chars,
            max_chars=max_chars,
        )
        prompt = self._inject_recent_turns(prompt, recent_turns)
        answer = await self._generate_answer(
            prompt=prompt,
            fallback_text=(
                context_lines[0]
                if context_lines
                else self.settings.experience.answer_extract_fallback_message
            ),
            log_label="knowledge",
        )
        return answer, False

    async def handle_asr_turn(
        self,
        *,
        session: ScienceSession,
        text: str,
        enable_tts: bool,
        age_group: Optional[str],
        gender: Optional[str],
        gender_confidence: Optional[float],
    ) -> dict:
        if gender is None or gender_confidence is None:
            metadata = session.metadata_json or {}
            if isinstance(metadata, dict):
                profile = metadata.get("profile")
                if isinstance(profile, dict):
                    if age_group is None and isinstance(profile.get("age_group"), str):
                        age_group = profile.get("age_group")
                    if gender is None and isinstance(profile.get("gender"), str):
                        gender = profile.get("gender")
                    if gender_confidence is None:
                        try:
                            gender_confidence = float(profile.get("gender_confidence"))
                        except (TypeError, ValueError):
                            gender_confidence = None

        recent_turns = self._get_recent_turns(session)
        resolved_query = self._build_retrieval_query(text, recent_turns)
        doc_scope = await WorkspaceScopeService(self.db).get_scope(self.context.workspace_id)
        chunks = await self.doc_skill.query_knowledge_base(
            query=resolved_query,
            user_context=self.user_context,
            top_k=5,
            session_id=session.id,
            parent_step_id="",
            include_images=False,
            file_ids=doc_scope.get("file_ids") or None,
            visibilities=doc_scope.get("visibilities") or None,
            dept_ids=doc_scope.get("dept_ids") or None,
        )
        request_trace_id = trace_id()
        retrieval_scope = {
            "mode": doc_scope.get("mode", "workspace"),
            "file_ids": list(doc_scope.get("file_ids") or []),
            "dept_ids": list(doc_scope.get("dept_ids") or []),
            "visibilities": list(doc_scope.get("visibilities") or []),
            "file_count": len(doc_scope.get("file_ids") or []),
            "dept_count": len(doc_scope.get("dept_ids") or []),
            "visibility_count": len(doc_scope.get("visibilities") or []),
        }
        logger.info(
            "[answer_runtime] retrieval trace_id=%s session_id=%s chunk_count=%s history_turns=%s fallback_scope=%s raw_query=%s retrieval_query=%s",
            request_trace_id,
            session.id,
            len(chunks),
            len(recent_turns),
            self._build_scope_summary(retrieval_scope),
            text.strip()[:120],
            resolved_query[:180],
        )
        answer, fallback_used = await self.build_answer_from_chunks(
            session=session,
            query=text,
            chunks=chunks,
            retrieval_scope=retrieval_scope,
            recent_turns=recent_turns,
        )
        logger.info(
            "[answer_runtime] answer_ready trace_id=%s session_id=%s fallback_used=%s answer_chars=%s",
            request_trace_id,
            session.id,
            fallback_used,
            len(answer),
        )
        await self._remember_turn(
            session=session,
            user_text=text,
            assistant_text=answer,
            resolved_query=resolved_query,
        )
        voice_key = await self.voice_config_service.resolve_voice_key(
            gender=gender,
            gender_confidence=gender_confidence,
            age_group=age_group,
        )

        tts_audio_base64 = None
        if enable_tts:
            try:
                tts_service = get_tts_service()
                audio_bytes = await tts_service.synthesize(answer, voice_id=voice_key)
                if audio_bytes:
                    tts_audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
            except Exception as exc:  # pragma: no cover
                logger.warning("TTS synthesis failed: %s", exc)

        sources = []
        for chunk in chunks[:5]:
            sources.append(
                {
                    "file_id": chunk.metadata.get("file_id"),
                    "file_name": chunk.metadata.get("source_file"),
                    "score": float(getattr(chunk, "rerank_score", 0.0) or 0.0),
                }
            )

        payload = {
            "answer": answer,
            "tts_voice": voice_key,
            "tts_audio_base64": tts_audio_base64,
        }
        return {
            "answer": answer,
            "tts_voice": voice_key,
            "event_type": "voice_reply_ready",
            "session_id": session.id,
            "trace_id": request_trace_id,
            "payload": payload,
            "sources": sources,
            "retrieval_scope": retrieval_scope,
        }
