import sys
import pytest

sys.path.insert(0, ".")

from app.services.chat_context import merge_safe_extra_context


def test_merge_safe_extra_context_blocks_workspace_override():
    base = {
        "user_id": "u_admin",
        "workspace_id": "ws_admin",
        "role": "admin",
        "deep_search": False,
    }
    extra = {
        "workspace_id": "ws_pd123",
        "user_id": "u_pd123",
        "deep_search": True,
        "doc_scope": {"file_ids": ["f1"]},
    }

    merged = merge_safe_extra_context(base, extra)

    assert merged["workspace_id"] == "ws_admin"
    assert merged["user_id"] == "u_admin"
    assert merged["deep_search"] is True
    assert merged["doc_scope"] == {
        "folder_ids": [],
        "file_ids": ["f1"],
        "include_subfolders": True,
    }


def test_merge_safe_extra_context_rejects_oversized_doc_scope():
    base = {
        "user_id": "u_admin",
        "workspace_id": "ws_admin",
        "role": "admin",
    }
    extra = {
        "doc_scope": {
            "folder_ids": [str(i) for i in range(201)],
        }
    }

    with pytest.raises(ValueError):
        merge_safe_extra_context(base, extra)
