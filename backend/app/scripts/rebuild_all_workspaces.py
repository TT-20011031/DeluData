"""Full reindex for all active (non-deleted) files across workspaces."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.db.database import get_async_db_context, get_async_db_manager
from app.core.utils.storage_path import resolve_storage_path
from app.models.common.context import UserContext
from app.models.knowledge.graph import File
from app.skills.doc_skill import DocSkill


@dataclass
class RebuildFile:
    file_id: str
    workspace_id: str
    user_id: str
    dept_id: Optional[int]
    visibility: str
    storage_path: str


def _load_checkpoint(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {"completed": [], "failed": {}, "attempts": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("completed", [])
    data.setdefault("failed", {})
    data.setdefault("attempts", {})
    return data


def _save_checkpoint(path: Path, checkpoint: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)


async def _load_target_files(workspace_id: Optional[str]) -> List[RebuildFile]:
    async with get_async_db_context() as session:
        stmt = select(File).where(File.is_deleted.is_(False))
        if workspace_id:
            stmt = stmt.where(File.workspace_id == workspace_id)
        stmt = stmt.order_by(File.workspace_id.asc(), File.created_at.asc())
        result = await session.execute(stmt)
        rows = result.scalars().all()

    files: List[RebuildFile] = []
    for row in rows:
        files.append(
            RebuildFile(
                file_id=str(row.id),
                workspace_id=str(row.workspace_id),
                user_id=str(row.user_id or "system_rebuild"),
                dept_id=row.dept_id,
                visibility=str(row.visibility or "dept"),
                storage_path=str(row.storage_path or ""),
            )
        )
    return files


async def _load_target_workspace_ids(workspace_id: Optional[str]) -> List[str]:
    files = await _load_target_files(workspace_id)
    workspace_ids = []
    seen = set()
    for item in files:
        if item.workspace_id in seen:
            continue
        seen.add(item.workspace_id)
        workspace_ids.append(item.workspace_id)
    return workspace_ids


async def _reindex_file(doc_skill: DocSkill, record: RebuildFile) -> Dict[str, object]:
    resolved_path = resolve_storage_path(record.storage_path)
    if not resolved_path or not os.path.exists(resolved_path):
        return {"success": False, "error": "source_file_missing"}

    user_context = UserContext(
        user_id=record.user_id,
        workspace_id=record.workspace_id,
        role="admin",
        dept_id=record.dept_id,
        data_scope=1,
        allowed_tables=["*"],
    )
    result = await doc_skill.ingest_document(
        file_path=resolved_path,
        user_context=user_context,
        file_id=record.file_id,
        visibility=record.visibility or "dept",
        target_dept_id=str(record.dept_id) if record.dept_id is not None else None,
        notify_bm25=False,
    )
    return result


async def run(
    *,
    workspace_id: Optional[str],
    checkpoint_path: Path,
    max_retries: int,
    bm25_backfill_only: bool = False,
) -> Dict[str, object]:
    doc_skill = DocSkill()
    if hasattr(doc_skill.retriever, "invalidate_all_bm25_cache"):
        doc_skill.retriever.invalidate_all_bm25_cache()

    if bm25_backfill_only:
        workspace_ids = await _load_target_workspace_ids(workspace_id)
        summary = {
            "workspace_count": len(workspace_ids),
            "completed": 0,
            "failed": 0,
            "failed_workspaces": [],
            "success": True,
        }
        for ws_id in workspace_ids:
            print(f"[bm25] workspace={ws_id}")
            try:
                ok = False
                if hasattr(doc_skill.retriever, "rebuild_bm25_index_now"):
                    ok = await doc_skill.retriever.rebuild_bm25_index_now(ws_id, force=True)
                if ok:
                    summary["completed"] += 1
                    print(f"[bm25-ok] {ws_id}")
                else:
                    summary["failed"] += 1
                    summary["failed_workspaces"].append(ws_id)
                    print(f"[bm25-fail] {ws_id}")
            except Exception as exc:
                summary["failed"] += 1
                summary["failed_workspaces"].append(ws_id)
                print(f"[bm25-fail] {ws_id} error={exc}")
        summary["success"] = summary["failed"] == 0
        return summary

    checkpoint = _load_checkpoint(checkpoint_path)
    completed = set(checkpoint.get("completed", []))
    failed: Dict[str, str] = dict(checkpoint.get("failed", {}))
    attempts: Dict[str, int] = {
        str(file_id): int(value) for file_id, value in dict(checkpoint.get("attempts", {})).items()
    }

    files = await _load_target_files(workspace_id)
    grouped: Dict[str, List[RebuildFile]] = defaultdict(list)
    for item in files:
        grouped[item.workspace_id].append(item)

    summary = {
        "total": len(files),
        "completed": 0,
        "failed": 0,
        "workspace_count": len(grouped),
        "bm25_failed_workspaces": [],
        "bm25_failed_count": 0,
        "success": False,
    }

    for ws_id, ws_files in grouped.items():
        print(f"[workspace] {ws_id} files={len(ws_files)}")
        for record in ws_files:
            if record.file_id in completed:
                continue

            attempts[record.file_id] = attempts.get(record.file_id, 0) + 1
            try:
                result = await _reindex_file(doc_skill, record)
            except Exception as exc:
                result = {"success": False, "error": str(exc)}

            if result.get("success"):
                completed.add(record.file_id)
                failed.pop(record.file_id, None)
                print(f"[ok] {record.file_id}")
            else:
                error = str(result.get("error", "reindex_failed"))
                if attempts[record.file_id] >= max_retries:
                    failed[record.file_id] = error
                    print(f"[fail] {record.file_id} error={error}")
                else:
                    print(f"[retry] {record.file_id} attempt={attempts[record.file_id]} error={error}")

            checkpoint["completed"] = sorted(completed)
            checkpoint["failed"] = failed
            checkpoint["attempts"] = attempts
            _save_checkpoint(checkpoint_path, checkpoint)

        doc_skill.retriever.invalidate_bm25_cache(ws_id)
        bm25_ok = True
        if hasattr(doc_skill.retriever, "rebuild_bm25_index_now"):
            bm25_ok = await doc_skill.retriever.rebuild_bm25_index_now(ws_id, force=True)
        if not bm25_ok:
            summary["bm25_failed_workspaces"].append(ws_id)
            print(f"[bm25-fail] {ws_id}")

    summary["completed"] = len(completed)
    summary["failed"] = len(failed)
    summary["bm25_failed_count"] = len(summary["bm25_failed_workspaces"])
    summary["success"] = summary["failed"] == 0 and summary["bm25_failed_count"] == 0
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild vectors for all workspaces.")
    parser.add_argument("--workspace-id", default=None, help="Only rebuild one workspace.")
    parser.add_argument("--checkpoint", default="data/rebuild_checkpoint.json", help="Checkpoint JSON path.")
    parser.add_argument("--max-retries", type=int, default=2, help="Max attempts per file.")
    parser.add_argument(
        "--bm25-backfill-only",
        action="store_true",
        help="Only backfill persisted BM25 indexes from existing vectors without re-ingesting files.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = parse_args()
    summary = await run(
        workspace_id=args.workspace_id,
        checkpoint_path=Path(args.checkpoint),
        max_retries=max(1, int(args.max_retries)),
        bm25_backfill_only=bool(args.bm25_backfill_only),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    await get_async_db_manager().dispose()


if __name__ == "__main__":
    asyncio.run(_main())
