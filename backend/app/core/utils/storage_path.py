"""Storage path helpers for cross-platform compatibility."""

from __future__ import annotations

import os
from urllib.parse import urlsplit


def normalize_storage_path(path: str) -> str:
    """Normalize path for persistence in DB (POSIX-style separators)."""
    normalized = (path or "").strip()
    if not normalized:
        return ""
    if normalized.startswith("oss://"):
        bucket, key = parse_oss_path(normalized)
        return build_oss_path(bucket, key)
    return normalized.replace("\\", "/")


def is_oss_path(path: str) -> bool:
    return normalize_storage_path(path).startswith("oss://")


def parse_oss_path(path: str) -> tuple[str, str]:
    normalized = (path or "").strip().replace("\\", "/")
    parsed = urlsplit(normalized)
    if parsed.scheme != "oss" or not parsed.netloc:
        raise ValueError(f"invalid oss path: {path}")
    bucket = parsed.netloc.strip()
    key = parsed.path.lstrip("/")
    return bucket, key


def build_oss_path(bucket: str, key: str) -> str:
    clean_bucket = (bucket or "").strip()
    clean_key = normalize_storage_key(key)
    if not clean_bucket or not clean_key:
        raise ValueError("bucket and key are required for oss path")
    return f"oss://{clean_bucket}/{clean_key}"


def normalize_storage_key(key: str) -> str:
    return (key or "").strip().replace("\\", "/").lstrip("/")


def resolve_storage_path(path: str) -> str:
    """Resolve persisted path into current runtime OS path format."""
    normalized = normalize_storage_path(path)
    if not normalized:
        return ""
    if normalized.startswith("oss://"):
        return normalized
    return os.path.normpath(normalized)
