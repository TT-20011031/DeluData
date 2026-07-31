import sys

sys.path.insert(0, ".")

from app.supervisor.nodes.executor import _apply_doc_scope_to_params  # noqa: E402


def test_executor_uses_session_scope_when_step_scope_missing():
    params, info = _apply_doc_scope_to_params(
        step_params={},
        user_context={"doc_scope": {"folder_ids": ["f1"], "include_subfolders": True}},
    )

    assert params["doc_scope"] == {
        "folder_ids": ["f1"],
        "file_ids": [],
        "include_subfolders": True,
    }
    assert info["scope_source"] == "session"


def test_executor_prefers_step_scope_over_session_scope():
    params, info = _apply_doc_scope_to_params(
        step_params={"doc_scope": {"file_ids": ["step_file"], "include_subfolders": False}},
        user_context={"doc_scope": {"folder_ids": ["session_folder"], "include_subfolders": True}},
    )

    assert params["doc_scope"] == {
        "folder_ids": [],
        "file_ids": ["step_file"],
        "include_subfolders": False,
    }
    assert info["scope_source"] == "step"


def test_executor_removes_invalid_or_empty_scope():
    params, info = _apply_doc_scope_to_params(
        step_params={"doc_scope": {"folder_ids": []}},
        user_context={},
    )
    assert "doc_scope" not in params
    assert info["scope_source"] == "none"
