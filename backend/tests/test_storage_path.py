from app.core.utils.storage_path import (
    build_oss_path,
    is_oss_path,
    normalize_storage_path,
    parse_oss_path,
    resolve_storage_path,
)


def test_normalize_local_storage_path() -> None:
    assert normalize_storage_path(r".\data\uploads\demo.pdf") == "./data/uploads/demo.pdf"


def test_build_and_parse_oss_path() -> None:
    storage_path = build_oss_path("delu-agent", "workspaces/demo/documents/file.pdf")
    assert storage_path == "oss://delu-agent/workspaces/demo/documents/file.pdf"
    assert is_oss_path(storage_path) is True
    assert parse_oss_path(storage_path) == (
        "delu-agent",
        "workspaces/demo/documents/file.pdf",
    )


def test_resolve_remote_storage_path_keeps_uri() -> None:
    storage_path = "oss://delu-agent/workspaces/demo/document-images/file/001.png"
    assert resolve_storage_path(storage_path) == storage_path
