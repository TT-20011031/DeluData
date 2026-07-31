"""Small shared helpers for experience runtime services."""

from __future__ import annotations

import uuid


def trace_id() -> str:
    return uuid.uuid4().hex


def normalize_answer(value: str) -> str:
    return (value or "").strip().lower()
