"""清理 Chroma 中的 image_summary 记录（支持 dry-run/备份/workspace 过滤）"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import chromadb


def _build_where(workspace_id: str | None) -> Dict[str, Any]:
    type_filter = {"type": {"$eq": "image_summary"}}
    if workspace_id:
        return {"$and": [type_filter, {"workspace_id": {"$eq": workspace_id}}]}
    return type_filter


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清理 Chroma image_summary 记录")
    parser.add_argument("--workspace-id", default="", help="仅清理指定 workspace_id")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不删除")
    parser.add_argument("--batch-size", type=int, default=500, help="删除批次大小")
    parser.add_argument("--backup-json", default="", help="删除前导出备份 JSON 文件路径")
    return parser.parse_args()


def _write_backup(path: Path, where: Dict[str, Any], results: Dict[str, Any]) -> None:
    payload = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "where": where,
        "count": len(results.get("ids", []) or []),
        "items": [],
    }
    ids = results.get("ids", []) or []
    metadatas = results.get("metadatas", []) or []
    documents = results.get("documents", []) or []
    for idx, chunk_id in enumerate(ids):
        payload["items"].append(
            {
                "id": chunk_id,
                "metadata": metadatas[idx] if idx < len(metadatas) else {},
                "document": documents[idx] if idx < len(documents) else "",
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = _parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from app.config import get_settings

    settings = get_settings()
    persist_dir = Path(settings.chroma.persist_dir).resolve()
    client = chromadb.PersistentClient(path=str(persist_dir))
    try:
        collection = client.get_collection("tenant_docs")
    except Exception as exc:
        print(f"[cleanup] tenant_docs collection not found: {exc}")
        return 0

    workspace_id = args.workspace_id.strip() or None
    where = _build_where(workspace_id)
    results = collection.get(where=where, include=["metadatas", "documents"])
    ids = results.get("ids", []) or []
    total = len(ids)

    print(f"[cleanup] persist_dir={persist_dir}")
    print(f"[cleanup] where={where}")
    print(f"[cleanup] matched={total}")

    if args.backup_json and total > 0:
        backup_path = Path(args.backup_json).expanduser().resolve()
        _write_backup(backup_path, where, results)
        print(f"[cleanup] backup written: {backup_path}")

    if args.dry_run:
        print("[cleanup] dry-run enabled, no deletion executed")
        return 0

    if total == 0:
        print("[cleanup] nothing to delete")
        return 0

    batch_size = max(1, int(args.batch_size))
    deleted = 0
    for i in range(0, total, batch_size):
        batch = ids[i : i + batch_size]
        collection.delete(ids=batch)
        deleted += len(batch)
        print(f"[cleanup] deleted {deleted}/{total}")

    verify = collection.get(where=where, include=["metadatas"])
    remaining = len(verify.get("ids", []) or [])
    print(f"[cleanup] remaining={remaining}")
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
