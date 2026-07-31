"""
文档范围（doc_scope）解析与合并工具。

职责：
1. 规范化前端/步骤传入的 doc_scope
2. 在步骤级与会话级范围之间做优先级合并
3. 生成轻量日志摘要（不记录完整 ID 列表）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


MAX_FOLDER_IDS = 200
MAX_FILE_IDS = 2000
MAX_WIKI_DOMAINS = 50
MAX_WIKI_SLUGS = 200
MAX_VISIBILITIES = 10
MAX_DEPT_IDS = 200


@dataclass(frozen=True)
class DocScopeSummary:
    """doc_scope 摘要信息，用于日志。"""

    source: str
    folder_count: int
    file_count: int
    include_subfolders: bool
    domain_count: int = 0
    wiki_slug_count: int = 0
    visibility_count: int = 0
    dept_count: int = 0


def _dedupe_ids(values: Iterable[Any]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in values:
        if item is None:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalize_id_list(
    raw: Any,
    *,
    field_name: str,
    limit: int,
    strict: bool,
) -> List[str]:
    if raw is None:
        return []

    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        if strict:
            raise ValueError(f"doc_scope.{field_name} 必须是字符串或字符串数组")
        return []

    normalized = _dedupe_ids(values)
    if len(normalized) > limit:
        if strict:
            raise ValueError(
                f"范围过大，请缩小选择（doc_scope.{field_name}: {len(normalized)} > {limit}）"
            )
        normalized = normalized[:limit]
    return normalized


def normalize_doc_scope(
    raw_scope: Any,
    *,
    strict: bool = False,
    drop_empty: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    规范化 doc_scope。

    strict=True:
      - 非法结构/超限会抛出 ValueError（用于 API 入参校验）
    strict=False:
      - 非法结构返回 None，超限自动截断（用于运行时兜底）
    """
    if raw_scope is None:
        return None

    if not isinstance(raw_scope, dict):
        if strict:
            raise ValueError("doc_scope 必须是对象")
        return None

    folder_ids = _normalize_id_list(
        raw_scope.get("folder_ids"),
        field_name="folder_ids",
        limit=MAX_FOLDER_IDS,
        strict=strict,
    )
    file_ids = _normalize_id_list(
        raw_scope.get("file_ids"),
        field_name="file_ids",
        limit=MAX_FILE_IDS,
        strict=strict,
    )

    # [M3.5] Wiki 范围（domain / slug）
    domains = _normalize_id_list(
        raw_scope.get("domains"),
        field_name="domains",
        limit=MAX_WIKI_DOMAINS,
        strict=strict,
    )
    wiki_slugs = _normalize_id_list(
        raw_scope.get("wiki_slugs"),
        field_name="wiki_slugs",
        limit=MAX_WIKI_SLUGS,
        strict=strict,
    )
    visibilities = _normalize_id_list(
        raw_scope.get("visibilities"),
        field_name="visibilities",
        limit=MAX_VISIBILITIES,
        strict=strict,
    )
    dept_ids = _normalize_id_list(
        raw_scope.get("dept_ids"),
        field_name="dept_ids",
        limit=MAX_DEPT_IDS,
        strict=strict,
    )
    scope = str(raw_scope.get("scope") or "").strip()
    department_id = raw_scope.get("department_id")
    if department_id is not None:
        dept_ids = _dedupe_ids([*dept_ids, department_id])
    business_domain = str(raw_scope.get("business_domain") or "").strip()
    document_type = str(raw_scope.get("document_type") or "").strip()
    confidentiality_level = str(raw_scope.get("confidentiality_level") or "").strip()

    include_subfolders_raw = raw_scope.get("include_subfolders")
    include_subfolders = (
        True
        if include_subfolders_raw is None
        else bool(include_subfolders_raw)
    )

    has_metadata_scope = any(
        [scope, dept_ids, visibilities, department_id is not None, business_domain, document_type, confidentiality_level]
    )

    if drop_empty and not folder_ids and not file_ids and not domains and not wiki_slugs and not has_metadata_scope:
        return None

    result = {
        "folder_ids": folder_ids,
        "file_ids": file_ids,
        "include_subfolders": include_subfolders,
        "domains": domains,
        "wiki_slugs": wiki_slugs,
        "visibilities": visibilities,
        "dept_ids": dept_ids,
    }
    if scope:
        result["scope"] = scope
    if department_id is not None:
        result["department_id"] = department_id
    if business_domain:
        result["business_domain"] = business_domain
    if document_type:
        result["document_type"] = document_type
    if confidentiality_level:
        result["confidentiality_level"] = confidentiality_level
    return result


def summarize_doc_scope(scope: Optional[Dict[str, Any]], source: str = "none") -> DocScopeSummary:
    if not scope:
        return DocScopeSummary(
            source=source,
            folder_count=0,
            file_count=0,
            include_subfolders=True,
        )
    return DocScopeSummary(
        source=source,
        folder_count=len(scope.get("folder_ids") or []),
        file_count=len(scope.get("file_ids") or []),
        include_subfolders=bool(scope.get("include_subfolders", True)),
        domain_count=len(scope.get("domains") or []),
        wiki_slug_count=len(scope.get("wiki_slugs") or []),
        visibility_count=len(scope.get("visibilities") or []),
        dept_count=len(scope.get("dept_ids") or []),
    )


def resolve_effective_doc_scope(
    step_scope: Any,
    session_scope: Any,
) -> Tuple[Optional[Dict[str, Any]], DocScopeSummary]:
    """
    计算最终生效范围，优先级：步骤级 > 会话级。
    """
    normalized_step = normalize_doc_scope(step_scope, strict=False, drop_empty=True)
    if normalized_step:
        summary = summarize_doc_scope(normalized_step, source="step")
        return normalized_step, summary

    normalized_session = normalize_doc_scope(session_scope, strict=False, drop_empty=True)
    if normalized_session:
        summary = summarize_doc_scope(normalized_session, source="session")
        return normalized_session, summary

    return None, summarize_doc_scope(None, source="none")
