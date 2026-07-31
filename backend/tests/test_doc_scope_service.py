import sys

import pytest

sys.path.insert(0, ".")

from app.services.doc_scope import (  # noqa: E402
    MAX_FILE_IDS,
    MAX_FOLDER_IDS,
    normalize_doc_scope,
    resolve_effective_doc_scope,
)


def test_normalize_doc_scope_dedup_and_defaults():
    scope = normalize_doc_scope(
        {
            "folder_ids": ["f1", "f1", "f2", ""],
            "file_ids": ["a", "a", "b", None],
        },
        strict=True,
    )

    assert scope == {
        "folder_ids": ["f1", "f2"],
        "file_ids": ["a", "b"],
        "include_subfolders": True,
    }


def test_normalize_doc_scope_strict_limit_validation():
    raw = {"folder_ids": [str(i) for i in range(MAX_FOLDER_IDS + 1)]}
    with pytest.raises(ValueError) as exc:
        normalize_doc_scope(raw, strict=True)
    assert "范围过大，请缩小选择" in str(exc.value)

    raw = {"file_ids": [str(i) for i in range(MAX_FILE_IDS + 1)]}
    with pytest.raises(ValueError) as exc:
        normalize_doc_scope(raw, strict=True)
    assert "范围过大，请缩小选择" in str(exc.value)


def test_resolve_effective_doc_scope_priority():
    session_scope = {"folder_ids": ["session_folder"], "include_subfolders": True}
    step_scope = {"file_ids": ["step_file"], "include_subfolders": False}

    scope, summary = resolve_effective_doc_scope(step_scope, session_scope)
    assert scope == {
        "folder_ids": [],
        "file_ids": ["step_file"],
        "include_subfolders": False,
    }
    assert summary.source == "step"

    scope, summary = resolve_effective_doc_scope(None, session_scope)
    assert scope == {
        "folder_ids": ["session_folder"],
        "file_ids": [],
        "include_subfolders": True,
    }
    assert summary.source == "session"


def test_normalize_doc_scope_non_strict_ignores_invalid():
    scope = normalize_doc_scope({"folder_ids": 123}, strict=False)
    assert scope is None
