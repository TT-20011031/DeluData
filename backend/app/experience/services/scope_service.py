"""Knowledge retrieval scope resolver for science workspace and device narrowing."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.experience.models import ScienceWorkspaceSetting


def _normalize_str_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in out:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


def normalize_doc_scope(raw_scope: Any) -> dict:
    raw = raw_scope if isinstance(raw_scope, dict) else {}
    return {
        "file_ids": _normalize_str_list(raw.get("file_ids"), limit=200),
        "visibilities": _normalize_str_list(raw.get("visibilities"), limit=20),
        "dept_ids": _normalize_str_list(raw.get("dept_ids"), limit=200),
    }


def normalize_device_scope(raw_scope: Any) -> dict:
    raw = raw_scope if isinstance(raw_scope, dict) else {}
    settings = get_settings()
    mode = str(raw.get("mode", "dept")).strip().lower()
    if mode not in {"dept", "files"}:
        mode = "dept"

    dept_ids = _normalize_str_list(
        raw.get("dept_ids"),
        limit=max(int(getattr(settings.experience, "kiosk_scope_max_dept_ids", 200)), 1),
    )
    file_ids = _normalize_str_list(
        raw.get("file_ids"),
        limit=max(int(getattr(settings.experience, "kiosk_scope_max_file_ids", 200)), 1),
    )

    if mode == "dept":
        file_ids = []
    else:
        dept_ids = []

    return {
        "mode": mode,
        "dept_ids": dept_ids,
        "file_ids": file_ids,
    }


def _narrow_list(base_items: list[str], narrowed_items: list[str]) -> list[str]:
    if not narrowed_items:
        return list(base_items)
    if not base_items:
        return list(narrowed_items)
    base_lookup = set(base_items)
    return [item for item in narrowed_items if item in base_lookup]


def build_effective_doc_scope(workspace_scope: Any, device_scope: Any) -> dict:
    workspace = normalize_doc_scope(workspace_scope)
    device = normalize_device_scope(device_scope)

    effective = {
        "mode": "workspace",
        "file_ids": list(workspace["file_ids"]),
        "visibilities": list(workspace["visibilities"]),
        "dept_ids": list(workspace["dept_ids"]),
    }

    if device["mode"] == "dept" and device["dept_ids"]:
        effective["mode"] = "dept"
        effective["dept_ids"] = _narrow_list(workspace["dept_ids"], device["dept_ids"])
    elif device["mode"] == "files" and device["file_ids"]:
        effective["mode"] = "files"
        effective["file_ids"] = _narrow_list(workspace["file_ids"], device["file_ids"])

    return effective


def summarize_effective_doc_scope(scope: Any) -> dict:
    raw = scope if isinstance(scope, dict) else {}
    mode = str(raw.get("mode", "workspace")).strip().lower()
    if mode not in {"workspace", "dept", "files"}:
        mode = "workspace"
    effective = {
        "mode": mode,
        "file_ids": _normalize_str_list(raw.get("file_ids"), limit=200),
        "dept_ids": _normalize_str_list(raw.get("dept_ids"), limit=200),
        "visibilities": _normalize_str_list(raw.get("visibilities"), limit=20),
    }
    return {
        "mode": effective["mode"],
        "file_ids": effective["file_ids"],
        "dept_ids": effective["dept_ids"],
        "visibilities": effective["visibilities"],
        "file_count": len(effective["file_ids"]),
        "dept_count": len(effective["dept_ids"]),
        "visibility_count": len(effective["visibilities"]),
    }


class WorkspaceScopeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_scope(self, workspace_id: str) -> dict:
        result = await self.db.execute(
            select(ScienceWorkspaceSetting).where(
                ScienceWorkspaceSetting.workspace_id == workspace_id
            )
        )
        row = result.scalar_one_or_none()
        return normalize_doc_scope((row.doc_scope_json if row else None) or {})

    async def update_scope(
        self,
        *,
        workspace_id: str,
        updated_by: str,
        doc_scope: dict,
    ) -> dict:
        result = await self.db.execute(
            select(ScienceWorkspaceSetting).where(
                ScienceWorkspaceSetting.workspace_id == workspace_id
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            row = ScienceWorkspaceSetting(workspace_id=workspace_id)
            self.db.add(row)

        normalized = normalize_doc_scope(doc_scope)
        row.doc_scope_json = normalized
        row.updated_by = updated_by
        await self.db.flush()
        return normalized
