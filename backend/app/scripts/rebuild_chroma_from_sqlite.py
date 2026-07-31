"""Rebuild a Chroma tenant_docs store from a broken Chroma SQLite metadata DB.

This is intended for cases where the Rust/HNSW index crashes on count/query but
the SQLite metadata can still be read directly. It re-embeds document chunks and
writes them into a fresh Chroma persist directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import chromadb
from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.config import get_settings
from app.core.db.database import get_async_db_context, get_async_db_manager
from app.core.llm.async_embedding import get_async_embedding
from app.models.knowledge.graph import File


COLLECTION_NAME = "tenant_docs"
DOCUMENT_KEY = "chroma:document"


def _metadata_value(row: sqlite3.Row) -> Any:
    if row["string_value"] is not None:
        return row["string_value"]
    if row["int_value"] is not None:
        return int(row["int_value"])
    if row["float_value"] is not None:
        return float(row["float_value"])
    if row["bool_value"] is not None:
        return bool(row["bool_value"])
    return None


async def _active_file_ids(workspace_id: Optional[str]) -> set[str]:
    async with get_async_db_context() as session:
        stmt = select(File.id).where(File.is_deleted.is_(False))
        if workspace_id:
            stmt = stmt.where(File.workspace_id == workspace_id)
        result = await session.execute(stmt)
        return {str(value) for value in result.scalars().all() if value}


def _iter_sqlite_chunks(
    sqlite_path: Path,
    *,
    active_file_ids: set[str],
    workspace_id: Optional[str],
) -> Iterable[Dict[str, Any]]:
    con = sqlite3.connect(str(sqlite_path))
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        rows = cur.execute(
            """
            SELECT
                e.id AS internal_id,
                e.embedding_id,
                m.key,
                m.string_value,
                m.int_value,
                m.float_value,
                m.bool_value
            FROM embeddings e
            JOIN embedding_metadata m ON m.id = e.id
            ORDER BY e.id ASC
            """
        )

        current_internal_id: Optional[int] = None
        current_embedding_id = ""
        metadata: Dict[str, Any] = {}

        def flush() -> Optional[Dict[str, Any]]:
            if current_internal_id is None:
                return None
            document = metadata.pop(DOCUMENT_KEY, None)
            file_id = str(metadata.get("file_id") or "")
            chunk_workspace_id = str(metadata.get("workspace_id") or "")
            if not document or not file_id:
                return None
            if active_file_ids and file_id not in active_file_ids:
                return None
            if workspace_id and chunk_workspace_id != workspace_id:
                return None
            clean_metadata = {
                str(key): value
                for key, value in metadata.items()
                if value is not None
            }
            return {
                "id": current_embedding_id,
                "document": str(document),
                "metadata": clean_metadata,
            }

        for row in rows:
            row_internal_id = int(row["internal_id"])
            if current_internal_id is not None and row_internal_id != current_internal_id:
                item = flush()
                if item is not None:
                    yield item
                metadata = {}

            current_internal_id = row_internal_id
            current_embedding_id = str(row["embedding_id"])
            metadata[str(row["key"])] = _metadata_value(row)

        item = flush()
        if item is not None:
            yield item
    finally:
        con.close()


def _batched(items: Sequence[Dict[str, Any]], batch_size: int) -> Iterable[List[Dict[str, Any]]]:
    for start in range(0, len(items), batch_size):
        yield list(items[start : start + batch_size])


async def rebuild(
    *,
    source_sqlite: Path,
    target_dir: Path,
    workspace_id: Optional[str],
    batch_size: int,
    reset_target: bool,
) -> Dict[str, Any]:
    if not source_sqlite.exists():
        raise FileNotFoundError(f"source sqlite not found: {source_sqlite}")

    if target_dir.exists() and reset_target:
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    active_ids = await _active_file_ids(workspace_id)
    chunks = list(
        _iter_sqlite_chunks(
            source_sqlite,
            active_file_ids=active_ids,
            workspace_id=workspace_id,
        )
    )
    if not chunks:
        raise RuntimeError("no chunks found to rebuild")

    settings = get_settings()
    client = chromadb.PersistentClient(path=str(target_dir))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "User uploaded documents for RAG"},
    )
    embedder = get_async_embedding()

    inserted = 0
    for batch_index, batch in enumerate(_batched(chunks, max(1, batch_size)), start=1):
        documents = [item["document"] for item in batch]
        embeddings = await embedder.embed_texts(
            documents,
            model=settings.rag.embedding_model,
            dimensions=settings.rag.embedding_dimensions,
        )
        collection.add(
            ids=[item["id"] for item in batch],
            documents=documents,
            metadatas=[item["metadata"] for item in batch],
            embeddings=embeddings,
        )
        inserted += len(batch)
        print(
            json.dumps(
                {
                    "event": "batch_inserted",
                    "batch": batch_index,
                    "inserted": inserted,
                    "total": len(chunks),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    probe_embedding = await embedder.embed_single(
        "健康检查",
        model=settings.rag.embedding_model,
        dimensions=settings.rag.embedding_dimensions,
    )
    probe = collection.query(
        query_embeddings=[probe_embedding],
        n_results=min(1, inserted),
        include=["documents", "metadatas", "distances"],
    )
    return {
        "success": True,
        "target_dir": str(target_dir),
        "inserted": inserted,
        "active_file_count": len(active_ids),
        "count": collection.count(),
        "probe_ids": probe.get("ids", [[]])[0],
    }


def parse_args() -> argparse.Namespace:
    default_source = Path(get_settings().chroma.persist_dir).resolve() / "chroma.sqlite3"
    parser = argparse.ArgumentParser(description="Rebuild Chroma from SQLite metadata.")
    parser.add_argument("--source-sqlite", default=str(default_source))
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--keep-target", action="store_true")
    return parser.parse_args()


async def _main() -> None:
    args = parse_args()
    summary = await rebuild(
        source_sqlite=Path(args.source_sqlite),
        target_dir=Path(args.target_dir),
        workspace_id=args.workspace_id or None,
        batch_size=max(1, int(args.batch_size)),
        reset_target=not bool(args.keep_target),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    await get_async_db_manager().dispose()


if __name__ == "__main__":
    asyncio.run(_main())
