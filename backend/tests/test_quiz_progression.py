import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.experience.services.quiz_runtime_service import (
    QUIZ_NEXT_ACTION_NEXT_QUESTION,
    QUIZ_NEXT_ACTION_REWARD_READY,
    QUIZ_NEXT_ACTION_RETURN_TO_CONSULT,
    QuizRuntimeService,
)
from app.experience.services.quiz_selector import QuizSelector


class _StubQuizSelector(QuizSelector):
    def __init__(self, active_question_ids: list[str]):
        super().__init__(db=None, workspace_id="ws1")
        self._active_question_ids = list(active_question_ids)

    async def list_active_question_ids(self) -> list[str]:
        return list(self._active_question_ids)


@pytest.mark.asyncio
async def test_pick_next_question_reuses_current_question_until_answered():
    selector = _StubQuizSelector(["q1", "q2", "q3"])
    metadata = {
        "quiz_state": {
            "current_question_id": "q2",
            "remaining_question_ids": ["q1", "q3"],
            "answered_question_ids": [],
            "last_question_id": "q2",
        }
    }

    question_id, updated_metadata = await selector.pick_next_question_id(metadata)

    assert question_id == "q2"
    assert updated_metadata["quiz_state"]["current_question_id"] == "q2"
    assert updated_metadata["quiz_state"]["remaining_question_ids"] == ["q1", "q3"]


@pytest.mark.asyncio
async def test_pick_next_question_skips_answered_question_after_submit():
    selector = _StubQuizSelector(["q1", "q2", "q3"])
    metadata = {
        "quiz_state": {
            "current_question_id": "q1",
            "remaining_question_ids": ["q2", "q3"],
            "answered_question_ids": [],
            "last_question_id": "q1",
        }
    }

    answered_metadata = selector.advance_after_answer(
        metadata,
        active_question_ids=["q1", "q2", "q3"],
        answered_question_id="q1",
    )
    question_id, updated_metadata = await selector.pick_next_question_id(answered_metadata)

    assert question_id in {"q2", "q3"}
    assert question_id != "q1"
    assert updated_metadata["quiz_state"]["current_question_id"] == question_id
    assert updated_metadata["quiz_state"]["answered_question_ids"] == ["q1"]
    assert "q1" not in updated_metadata["quiz_state"]["remaining_question_ids"]


@pytest.mark.asyncio
async def test_pick_next_question_respects_excluded_question_ids():
    selector = _StubQuizSelector(["q1", "q2", "q3"])
    metadata = {
        "quiz_state": {
            "current_question_id": "q1",
            "remaining_question_ids": ["q2", "q3"],
            "answered_question_ids": [],
            "last_question_id": "q1",
        }
    }

    question_id, updated_metadata = await selector.pick_next_question_id(
        metadata,
        excluded_question_ids=["q1"],
    )

    assert question_id in {"q2", "q3"}
    assert question_id != "q1"
    assert updated_metadata["quiz_state"]["current_question_id"] == question_id


def test_determine_next_action_uses_single_question_reward_mode():
    assert (
        QuizRuntimeService.determine_next_action(
            single_correct_reward_mode=True,
            correct=True,
            reward_ready=False,
        )
        == QUIZ_NEXT_ACTION_RETURN_TO_CONSULT
    )
    assert (
        QuizRuntimeService.determine_next_action(
            single_correct_reward_mode=True,
            correct=False,
            reward_ready=False,
        )
        == QUIZ_NEXT_ACTION_NEXT_QUESTION
    )
    assert (
        QuizRuntimeService.determine_next_action(
            single_correct_reward_mode=True,
            correct=True,
            reward_ready=True,
        )
        == QUIZ_NEXT_ACTION_REWARD_READY
    )
