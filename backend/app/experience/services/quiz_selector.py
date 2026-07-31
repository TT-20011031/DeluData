"""Quiz selection strategy: no immediate repeat + rotating question bag."""

from __future__ import annotations

import random
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.experience.models import ScienceQuizQuestion


class QuizSelector:
    def __init__(self, db: AsyncSession, workspace_id: str):
        self.db = db
        self.workspace_id = workspace_id

    async def list_active_question_ids(self) -> list[str]:
        result = await self.db.execute(
            select(ScienceQuizQuestion.id).where(
                ScienceQuizQuestion.workspace_id == self.workspace_id,
                ScienceQuizQuestion.is_active.is_(True),
            )
        )
        return [row[0] for row in result.fetchall() if row and row[0]]

    @staticmethod
    def _sanitize_question_bag(raw_ids: Any, valid_ids: list[str]) -> list[str]:
        if not isinstance(raw_ids, list):
            return []
        valid_set = set(valid_ids)
        bag: list[str] = []
        for value in raw_ids:
            if not isinstance(value, str):
                continue
            if value in valid_set and value not in bag:
                bag.append(value)
        return bag

    @classmethod
    def _sanitize_excluded_question_ids(
        cls,
        raw_ids: Any,
        valid_ids: list[str],
    ) -> list[str]:
        return cls._sanitize_question_bag(raw_ids, valid_ids)

    @classmethod
    def advance_after_answer(
        cls,
        metadata_json: dict | None,
        *,
        active_question_ids: list[str],
        answered_question_id: str,
    ) -> dict:
        metadata = dict(metadata_json or {})
        quiz_state = metadata.get("quiz_state")
        if not isinstance(quiz_state, dict):
            quiz_state = {}

        answered_question_ids = cls._sanitize_question_bag(
            quiz_state.get("answered_question_ids"),
            active_question_ids,
        )
        if (
            answered_question_id in active_question_ids
            and answered_question_id not in answered_question_ids
        ):
            answered_question_ids.append(answered_question_id)

        remaining_question_ids = [
            question_id
            for question_id in cls._sanitize_question_bag(
                quiz_state.get("remaining_question_ids"),
                active_question_ids,
            )
            if question_id != answered_question_id
            and question_id not in answered_question_ids
        ]

        current_question_id = quiz_state.get("current_question_id")
        if (
            not isinstance(current_question_id, str)
            or current_question_id not in active_question_ids
            or current_question_id == answered_question_id
        ):
            current_question_id = None

        quiz_state["answered_question_ids"] = answered_question_ids
        quiz_state["remaining_question_ids"] = remaining_question_ids
        quiz_state["current_question_id"] = current_question_id
        quiz_state["last_answered_question_id"] = answered_question_id
        metadata["quiz_state"] = quiz_state
        return metadata

    @staticmethod
    def _build_available_question_ids(
        active_question_ids: list[str],
        answered_question_ids: list[str],
    ) -> list[str]:
        answered_question_id_set = set(answered_question_ids)
        return [
            question_id
            for question_id in active_question_ids
            if question_id not in answered_question_id_set
        ]

    async def pick_next_question_id(
        self,
        metadata_json: dict | None,
        excluded_question_ids: list[str] | None = None,
    ) -> tuple[str | None, dict]:
        active_ids = await self.list_active_question_ids()
        if not active_ids:
            return None, metadata_json or {}

        metadata = dict(metadata_json or {})
        quiz_state = metadata.get("quiz_state")
        if not isinstance(quiz_state, dict):
            quiz_state = {}

        answered_question_ids = self._sanitize_question_bag(
            quiz_state.get("answered_question_ids"),
            active_ids,
        )
        excluded_ids = self._sanitize_excluded_question_ids(excluded_question_ids, active_ids)
        excluded_question_id_set = set(excluded_ids)
        current_question_id = quiz_state.get("current_question_id")
        if (
            isinstance(current_question_id, str)
            and current_question_id in active_ids
            and current_question_id not in answered_question_ids
            and current_question_id not in excluded_question_id_set
        ):
            quiz_state["answered_question_ids"] = answered_question_ids
            quiz_state["current_question_id"] = current_question_id
            quiz_state["remaining_question_ids"] = [
                question_id
                for question_id in self._sanitize_question_bag(
                    quiz_state.get("remaining_question_ids"),
                    active_ids,
                )
                if question_id != current_question_id
                and question_id not in answered_question_ids
            ]
            metadata["quiz_state"] = quiz_state
            return current_question_id, metadata

        bag = self._sanitize_question_bag(
            quiz_state.get("remaining_question_ids"),
            active_ids,
        )
        bag = [
            question_id
            for question_id in bag
            if question_id not in answered_question_ids
            and question_id not in excluded_question_id_set
        ]

        available_question_ids = self._build_available_question_ids(
            active_ids,
            answered_question_ids,
        )
        available_question_ids = [
            question_id
            for question_id in available_question_ids
            if question_id not in excluded_question_id_set
        ]
        if not available_question_ids:
            available_question_ids = self._build_available_question_ids(
                active_ids,
                answered_question_ids,
            )
        if not available_question_ids:
            answered_question_ids = []
            available_question_ids = [
                question_id
                for question_id in active_ids
                if question_id not in excluded_question_id_set
            ]
        if not available_question_ids:
            available_question_ids = list(active_ids)

        if not bag:
            bag = list(available_question_ids)
            random.shuffle(bag)

        last_question_id = quiz_state.get("last_question_id")
        if (
            isinstance(last_question_id, str)
            and len(bag) > 1
            and bag[0] == last_question_id
        ):
            bag.append(bag.pop(0))

        question_id = bag.pop(0)
        quiz_state["remaining_question_ids"] = bag
        quiz_state["last_question_id"] = question_id
        quiz_state["current_question_id"] = question_id
        quiz_state["answered_question_ids"] = answered_question_ids
        metadata["quiz_state"] = quiz_state
        return question_id, metadata
