"""Quiz generation and challenge runtime helpers."""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select

from app.experience.defaults import (
    QUIZ_DIFFICULTY_LABELS,
    QUIZ_GENERATION_DEFAULT_QUESTION,
    QUIZ_GENERATION_EXTRA_REQUIREMENTS_TEMPLATE,
)
from app.experience.models import ScienceQuizAttempt, ScienceQuizQuestion, ScienceSession
from app.experience.services.quiz_selector import QuizSelector
from app.experience.services.reward_runtime_service import (
    REWARD_STATE_NOT_REACHED,
    RewardRuntimeService,
)
from app.experience.services.runtime_utils import normalize_answer, trace_id

logger = logging.getLogger(__name__)

QUIZ_NEXT_ACTION_NEXT_QUESTION = "next_question"
QUIZ_NEXT_ACTION_REWARD_READY = "reward_ready"
QUIZ_NEXT_ACTION_RETURN_TO_CONSULT = "return_to_consult"


class QuizRuntimeService:
    def __init__(
        self,
        *,
        db,
        context,
        settings,
        llm,
        reward_service: RewardRuntimeService,
    ):
        self.db = db
        self.context = context
        self.settings = settings
        self.llm = llm
        self.reward_service = reward_service

    @staticmethod
    def determine_next_action(
        *,
        single_correct_reward_mode: bool,
        correct: bool,
        reward_ready: bool,
    ) -> str:
        if reward_ready:
            return QUIZ_NEXT_ACTION_REWARD_READY
        if single_correct_reward_mode and correct:
            return QUIZ_NEXT_ACTION_RETURN_TO_CONSULT
        return QUIZ_NEXT_ACTION_NEXT_QUESTION

    async def pick_quiz_question(
        self,
        session: ScienceSession,
        excluded_question_ids: Optional[list[str]] = None,
    ) -> Optional[ScienceQuizQuestion]:
        selector = QuizSelector(self.db, self.context.workspace_id)
        question_id, updated_metadata = await selector.pick_next_question_id(
            session.metadata_json,
            excluded_question_ids=excluded_question_ids,
        )
        if not question_id:
            return None

        session.metadata_json = updated_metadata
        await self.db.flush()

        result = await self.db.execute(
            select(ScienceQuizQuestion).where(
                ScienceQuizQuestion.workspace_id == self.context.workspace_id,
                ScienceQuizQuestion.id == question_id,
                ScienceQuizQuestion.is_active.is_(True),
            )
        )
        question = result.scalar_one_or_none()
        if question:
            return question

        fallback_result = await self.db.execute(
            select(ScienceQuizQuestion)
            .where(
                ScienceQuizQuestion.workspace_id == self.context.workspace_id,
                ScienceQuizQuestion.is_active.is_(True),
            )
            .order_by(func.rand())
            .limit(1)
        )
        return fallback_result.scalar_one_or_none()

    def build_quiz_generation_prompt(self, *, topic: str, difficulty: int) -> str:
        difficulty_label = QUIZ_DIFFICULTY_LABELS.get(difficulty, QUIZ_DIFFICULTY_LABELS[3])
        return (
            f"{self.settings.experience.quiz_generation_prompt}\n"
            + QUIZ_GENERATION_EXTRA_REQUIREMENTS_TEMPLATE.format(
                topic=topic,
                difficulty=difficulty,
                difficulty_label=difficulty_label,
            )
        )

    def normalize_generated_quiz_payload(self, generated: object) -> dict[str, object]:
        if not isinstance(generated, dict):
            return QUIZ_GENERATION_DEFAULT_QUESTION

        question_text = str(generated.get("question_text") or "").strip()
        if not question_text:
            question_text = str(QUIZ_GENERATION_DEFAULT_QUESTION["question_text"])

        raw_options = generated.get("options")
        options = []
        if isinstance(raw_options, list):
            options = [str(item).strip() for item in raw_options if str(item).strip()]
        if len(options) < 2 or len(options) > 6:
            options = list(QUIZ_GENERATION_DEFAULT_QUESTION["options"])

        valid_answer_keys = [chr(ord("A") + idx) for idx in range(len(options))]
        answer_key = str(generated.get("answer_key") or "").strip().upper()[:1]
        if answer_key not in valid_answer_keys:
            answer_key = str(QUIZ_GENERATION_DEFAULT_QUESTION["answer_key"])

        explanation = str(generated.get("explanation") or "").strip()
        if not explanation:
            explanation = str(QUIZ_GENERATION_DEFAULT_QUESTION["explanation"])

        return {
            "question_text": question_text,
            "options": options,
            "answer_key": answer_key,
            "explanation": explanation,
        }

    async def generate_quiz_questions(
        self,
        *,
        topic: str,
        count: int,
        difficulty: int,
    ) -> list[ScienceQuizQuestion]:
        questions: list[ScienceQuizQuestion] = []
        for _ in range(count):
            question = await self.generate_quiz_question(
                topic=topic,
                difficulty=difficulty,
            )
            questions.append(question)
        return questions

    async def generate_quiz_question(
        self,
        *,
        topic: str | None = None,
        difficulty: int = 1,
    ) -> ScienceQuizQuestion:
        generated = QUIZ_GENERATION_DEFAULT_QUESTION
        try:
            prompt = self.build_quiz_generation_prompt(
                topic=topic or "科技馆常识",
                difficulty=difficulty,
            )
            response = await self.llm.chat(
                [{"role": "user", "content": prompt}],
                model=self.settings.llm.fast_model,
            )
            normalized_response = response.strip()
            if normalized_response.startswith("```"):
                normalized_response = "\n".join(
                    line
                    for line in normalized_response.splitlines()
                    if not line.strip().startswith("```")
                ).strip()
            generated = self.normalize_generated_quiz_payload(
                json.loads(normalized_response),
            )
        except Exception:
            generated = QUIZ_GENERATION_DEFAULT_QUESTION

        question = ScienceQuizQuestion(
            workspace_id=self.context.workspace_id,
            question_text=generated.get(
                "question_text",
                QUIZ_GENERATION_DEFAULT_QUESTION["question_text"],
            ),
            options_json=generated.get(
                "options",
                QUIZ_GENERATION_DEFAULT_QUESTION["options"],
            ),
            answer_key=generated.get(
                "answer_key",
                QUIZ_GENERATION_DEFAULT_QUESTION["answer_key"],
            ),
            explanation=generated.get(
                "explanation",
                QUIZ_GENERATION_DEFAULT_QUESTION["explanation"],
            ),
            difficulty=difficulty,
            source_type="generated",
            is_active=True,
            created_by="system",
        )
        self.db.add(question)
        await self.db.flush()
        return question

    async def start_quiz(
        self,
        *,
        session: ScienceSession,
        excluded_question_ids: Optional[list[str]] = None,
    ) -> dict:
        await self.db.execute(
            select(ScienceSession.id)
            .where(
                ScienceSession.id == session.id,
                ScienceSession.workspace_id == self.context.workspace_id,
            )
            .with_for_update()
        )

        question = await self.pick_quiz_question(
            session,
            excluded_question_ids=excluded_question_ids,
        )
        if not question:
            raise HTTPException(status_code=409, detail="quiz_question_bank_empty")

        return {
            "event_type": "quiz_question",
            "session_id": session.id,
            "trace_id": trace_id(),
            "payload": {
                "question_id": question.id,
                "question_text": question.question_text,
                "options": question.options_json,
            },
            "question_id": question.id,
            "question_text": question.question_text,
            "options": question.options_json,
        }

    async def submit_quiz_answer(
        self,
        *,
        session: ScienceSession,
        question_id: str,
        answer: str,
    ) -> dict:
        q_result = await self.db.execute(
            select(ScienceQuizQuestion).where(
                ScienceQuizQuestion.id == question_id,
                ScienceQuizQuestion.workspace_id == self.context.workspace_id,
            )
        )
        question = q_result.scalar_one_or_none()
        if not question:
            raise HTTPException(status_code=404, detail="question_not_found")

        correct = normalize_answer(answer) == normalize_answer(question.answer_key)
        attempt = ScienceQuizAttempt(
            workspace_id=self.context.workspace_id,
            session_id=session.id,
            device_id=self.context.device_id,
            question_id=question_id,
            user_answer=answer,
            is_correct=correct,
        )
        self.db.add(attempt)
        await self.db.flush()

        request_trace_id = trace_id()
        selector = QuizSelector(self.db, self.context.workspace_id)
        active_question_ids = await selector.list_active_question_ids()
        session.metadata_json = selector.advance_after_answer(
            session.metadata_json,
            active_question_ids=active_question_ids,
            answered_question_id=question_id,
        )
        await self.db.flush()

        policy = await self.reward_service.get_or_init_reward_policy()
        correct_count = await self.reward_service.count_correct_answers(session.id)
        single_correct_reward_mode = bool(
            getattr(self.settings.experience, "quiz_single_correct_reward_mode", False)
        )
        effective_required_correct_count = (
            1 if single_correct_reward_mode else max(policy.required_correct_count, 1)
        )
        issuance = None
        reward_state = REWARD_STATE_NOT_REACHED
        reward_reason_code = None
        reward_message = None
        if correct:
            issuance = await self.reward_service.issue_coupon_if_eligible(
                session_id=session.id,
                policy=policy,
                required_correct_count=effective_required_correct_count,
            )
        reward_state, reward_reason_code, reward_message = (
            await self.reward_service.resolve_reward_state(
                session_id=session.id,
                policy=policy,
                correct_count=correct_count,
                issuance=issuance,
                required_correct_count=effective_required_correct_count,
            )
        )

        reward_ready = issuance is not None
        next_action = self.determine_next_action(
            single_correct_reward_mode=single_correct_reward_mode,
            correct=correct,
            reward_ready=reward_ready,
        )
        if next_action == QUIZ_NEXT_ACTION_RETURN_TO_CONSULT and not reward_message:
            reward_message = self.settings.experience.quiz_single_correct_return_message

        logger.info(
            "[quiz_runtime] answer_result trace_id=%s session_id=%s question_id=%s correct=%s next_action=%s reward_state=%s quiz_state=%s",
            request_trace_id,
            session.id,
            question_id,
            correct,
            next_action,
            reward_state,
            (session.metadata_json or {}).get("quiz_state"),
        )
        return {
            "event_type": "quiz_result",
            "session_id": session.id,
            "trace_id": request_trace_id,
            "payload": {
                "correct": correct,
                "correct_answer": question.answer_key,
                "explanation": question.explanation,
                "correct_count": correct_count,
                "required_correct_count": effective_required_correct_count,
                "reward_ready": reward_ready,
                "issuance_id": issuance.id if issuance else None,
                "reward_state": reward_state,
                "reward_message": reward_message,
                "reward_reason_code": reward_reason_code,
                "next_action": next_action,
            },
            "correct": correct,
            "correct_answer": question.answer_key,
            "explanation": question.explanation,
            "correct_count": correct_count,
            "required_correct_count": effective_required_correct_count,
            "reward_ready": reward_ready,
            "issuance_id": issuance.id if issuance else None,
            "reward_state": reward_state,
            "reward_message": reward_message,
            "reward_reason_code": reward_reason_code,
            "next_action": next_action,
        }
