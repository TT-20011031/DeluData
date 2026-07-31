"""Utilities for building safe chat runtime context."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.services.doc_scope import normalize_doc_scope


logger = logging.getLogger(__name__)

# Security-critical identity and permission fields must never be overridden by client payload.
_PROTECTED_CONTEXT_KEYS = {
    "user_id",
    "workspace_id",
    "username",
    "role",
    "roles",
    "permissions",
    "dept_id",
    "data_scope",
    "allowed_tables",
    "allowed_datasources",
}


def merge_safe_extra_context(
    base_context: Dict[str, Any],
    extra_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Merge client extra context into server context without overriding protected fields.

    `base_context` should always come from authenticated server-side user context.
    """
    merged = dict(base_context)
    if not extra_context:
        return merged

    for key, value in extra_context.items():
        if key in _PROTECTED_CONTEXT_KEYS:
            logger.warning(
                "[ChatContext] blocked override for protected key '%s' from extra_context",
                key,
            )
            continue
        if key == "doc_scope":
            # 严格校验会话级 doc_scope，防止超大请求体和脏数据进入执行链路。
            normalized = normalize_doc_scope(value, strict=True, drop_empty=True)
            if normalized is None:
                merged.pop("doc_scope", None)
            else:
                merged["doc_scope"] = normalized
            continue
        merged[key] = value
    return merged
