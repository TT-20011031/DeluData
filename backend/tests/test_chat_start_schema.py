import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.chat.schemas import StartSessionRequest


def test_start_session_request_accepts_skill_id():
    payload = StartSessionRequest.model_validate(
        {
            "message": "执行",
            "session_id": "s-1",
            "skill_id": "skill-1",
        }
    )

    assert payload.skill_id == "skill-1"


def test_start_session_request_rejects_unknown_top_level_fields():
    with pytest.raises(ValidationError):
        StartSessionRequest.model_validate(
            {
                "message": "执行",
                "session_id": "s-1",
                "unexpected_field": "boom",
            }
        )
