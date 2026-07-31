"""Reconcile vector store and DB consistency for file-level RAG data."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.db.database import get_async_db_context, get_async_db_manager
from app.models.common.context import UserContext
from app.models.common.enums import DocumentStatus
from app.models.knowledge.graph import File
from app.services.task_queue_service import TaskQueueService
from app.skills.doc_skill import DocSkill


@dataclass
class WorkspaceReport:
    workspace_id: str
    orphan_vectors: List[str]
    orphan_db_rows: List[str]


async def _load_files(workspace_id: str) -> Dict[str, File]:
    async with get_async_db_context() as session:
        stmt = select(File).where(File.workspace_id == workspace_id)
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return {str(row.id): row for row in rows}


def _extract_chroma_file_ids(raw_result: Dict[str, object]) -> Set[str]:
    metadatas = raw_result.get("metadatas") or []
    file_ids: Set[str] = set()
    for metadata in metadatas:
        if isinstance(metadata, dict):
            file_id = metadata.get("file_id")
            if file_id:
                file_ids.add(str(file_id))
    return file_ids


async def _reconcile_workspace(doc_skill: DocSkill, workspace_id: str) -> WorkspaceReport:
    db_map = await _load_files(workspace_id)
    db_active_ids = {fid for fid, row in db_map.items() if not bool(getattr(row, "is_deleted", False))}
    db_active_indexed_ids = set()
    for fid, row in db_map.items():
        status_value = row.status.value if hasattr(row.status, "value") else str(row.status)
        if (not bool(getattr(row, "is_deleted", False))) and status_value == DocumentStatus.INDEXED.value:
            db_active_indexed_ids.add(fid)

    raw = doc_skill.collection.get(
        where={"workspace_id": {"$eq": workspace_id}},
        include=["metadatas"],
    )
    chroma_file_ids = _extract_chroma_file_ids(raw)

    orphan_vectors = sorted(chroma_file_ids - db_active_ids)
    orphan_db_rows = sorted(db_active_indexed_ids - chroma_file_ids)
    return WorkspaceReport(
        workspace_id=workspace_id,
        orphan_vectors=orphan_vectors,
        orphan_db_rows=orphan_db_rows,
    )


async def _apply_orphan_vector_cleanup(doc_skill: DocSkill, workspace_id: str, file_ids: List[str]) -> List[str]:
    failures: List[str] = []
    user_context = UserContext(
        user_id="system_reconcile",
        workspace_id=workspace_id,
        role="admin",
        data_scope=1,
        allowed_tables=["*"],
    )
    for file_id in file_ids:
        try:
            await doc_skill.delete_document(file_id, user_context, raise_on_error=True)
        except Exception:
            failures.append(file_id)
    return failures


async def _apply_orphan_db_reindex(workspace_id: str, db_map: Dict[str, File], file_ids: List[str]) -> List[str]:
    failures: List[str] = []
    queue = TaskQueueService()
    for file_id in file_ids:
        row = db_map.get(file_id)
        if row is None:
            failures.append(file_id)
            continue
        try:
            await queue.enqueue_task(
                file_id=file_id,
                workspace_id=workspace_id,
                user_id=str(row.user_id or "system_reconcile"),
                payload_json={
                    "task_kind": "reindex",
                    "regenerate_description": False,
                    "visibility": row.visibility or "dept",
                    "target_dept_id": str(row.dept_id) if row.dept_id is not None else None,
                    "source": "reconcile_vector_consistency",
                },
            )
        except Exception:
            failures.append(file_id)
    return failures


async def _list_workspaces(target_workspace: Optional[str]) -> List[str]:
    if target_workspace:
        return [target_workspace]
    async with get_async_db_context() as session:
        stmt = select(File.workspace_id).distinct()
        result = await session.execute(stmt)
        return sorted({str(ws_id) for ws_id in result.scalars().all() if ws_id})


async def run(target_workspace: Optional[str], apply: bool) -> Dict[str, object]:
    doc_skill = DocSkill()
    workspace_ids = await _list_workspaces(target_workspace)
    reports: List[Dict[str, object]] = []

    for workspace_id in workspace_ids:
        report = await _reconcile_workspace(doc_skill, workspace_id)
        workspace_output: Dict[str, object] = {
            "workspace_id": workspace_id,
            "orphan_vectors": report.orphan_vectors,
            "orphan_db_rows": report.orphan_db_rows,
            "orphan_vectors_count": len(report.orphan_vectors),
            "orphan_db_rows_count": len(report.orphan_db_rows),
        }

        if apply:
            db_map = await _load_files(workspace_id)
            vector_failures = await _apply_orphan_vector_cleanup(doc_skill, workspace_id, report.orphan_vectors)
            reindex_failures = await _apply_orphan_db_reindex(workspace_id, db_map, report.orphan_db_rows)
            workspace_output["vector_cleanup_failed"] = vector_failures
            workspace_output["reindex_enqueue_failed"] = reindex_failures

        reports.append(workspace_output)

    summary = {
        "workspace_count": len(reports),
        "orphan_vectors_total": sum(int(item["orphan_vectors_count"]) for item in reports),
        "orphan_db_rows_total": sum(int(item["orphan_db_rows_count"]) for item in reports),
        "apply": apply,
    }
    return {"summary": summary, "workspaces": reports}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile vector and DB consistency.")
    parser.add_argument("--workspace-id", default=None, help="Only reconcile one workspace.")
    parser.add_argument("--apply", action="store_true", help="Apply automatic repair actions.")
    parser.add_argument("--output", default=None, help="Write JSON report to this file.")
    return parser.parse_args()


async def _main() -> None:
    args = parse_args()
    report = await run(args.workspace_id, args.apply)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        print(f"report saved: {output_path}")
    else:
        print(output)
    await get_async_db_manager().dispose()


if __name__ == "__main__":
    asyncio.run(_main())
