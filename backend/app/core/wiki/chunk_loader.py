"""从 ChromaDB 拉取某个 file_id 的全部切片，用于 Wiki 编译时的原文证据。

设计原则：
- 不耦合 HybridRetriever 的查询逻辑（避免 BM25 等不必要开销）
- 仅按 file_id + workspace_id 过滤
- 输出按 chunk_index 排序的切片列表
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import chromadb

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class WikiChunk:
    """供 Wiki 编译使用的精简切片视图。"""

    chunk_id: str
    chunk_index: int
    content: str
    page_numbers: list[int]
    header_path: str
    summary: str
    source_file: str

    def short_preview(self, max_chars: int = 240) -> str:
        text = self.content or ""
        return text[:max_chars] + ("…" if len(text) > max_chars else "")


_DEFAULT_COLLECTION = "tenant_docs"


class WikiChunkLoader:
    """ChromaDB 切片加载器（懒加载客户端）。"""

    def __init__(
        self,
        chroma_client: Optional[chromadb.ClientAPI] = None,
        collection_name: str = _DEFAULT_COLLECTION,
    ):
        self._client = chroma_client
        self._collection: Optional[chromadb.Collection] = None
        self.collection_name = collection_name

    @property
    def client(self) -> chromadb.ClientAPI:
        if self._client is None:
            settings = get_settings()
            persist_path = Path(settings.chroma.persist_dir).resolve()
            persist_path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(persist_path))
        return self._client

    @property
    def collection(self) -> chromadb.Collection:
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "User uploaded documents for RAG"},
            )
        return self._collection

    async def load_chunks_for_file(
        self,
        workspace_id: str,
        file_id: str,
    ) -> list[WikiChunk]:
        """加载某文件在某租户下的全部切片，按 chunk_index 升序。"""

        def _sync_load() -> dict[str, Any]:
            return self.collection.get(
                where={
                    "$and": [
                        {"workspace_id": {"$eq": workspace_id}},
                        {"file_id": {"$eq": file_id}},
                    ]
                },
                include=["documents", "metadatas"],
            )

        try:
            raw = await asyncio.to_thread(_sync_load)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[WikiChunkLoader] 加载失败 workspace=%s file=%s err=%s",
                workspace_id,
                file_id,
                exc,
            )
            return []

        ids: list[str] = list(raw.get("ids") or [])
        documents: list[str] = list(raw.get("documents") or [])
        metadatas: list[dict[str, Any]] = list(raw.get("metadatas") or [])

        if not ids:
            return []

        chunks: list[WikiChunk] = []
        for idx, chunk_id in enumerate(ids):
            meta = metadatas[idx] if idx < len(metadatas) else {}
            content = documents[idx] if idx < len(documents) else ""
            page_numbers = meta.get("page_numbers")
            if isinstance(page_numbers, str):
                page_numbers = [
                    int(p) for p in page_numbers.split(",") if p.strip().isdigit()
                ]
            elif isinstance(page_numbers, (list, tuple)):
                page_numbers = [int(p) for p in page_numbers if str(p).isdigit()]
            else:
                page_numbers = []

            chunks.append(
                WikiChunk(
                    chunk_id=str(chunk_id),
                    chunk_index=int(meta.get("chunk_index") or 0),
                    content=str(content or ""),
                    page_numbers=page_numbers,
                    header_path=str(meta.get("header_path") or ""),
                    summary=str(meta.get("summary") or ""),
                    source_file=str(meta.get("source_file") or ""),
                )
            )

        chunks.sort(key=lambda c: c.chunk_index)
        return chunks

    async def load_chunks_by_ids(
        self,
        workspace_id: str,
        chunk_ids: list[str],
    ) -> list[WikiChunk]:
        """按 chunk_id 批量加载切片，用于 Wiki 详情页溯源展示。"""
        clean_ids = [str(cid).strip() for cid in chunk_ids if str(cid).strip()]
        if not clean_ids:
            return []

        def _sync_load() -> dict[str, Any]:
            return self.collection.get(
                ids=clean_ids,
                include=["documents", "metadatas"],
            )

        try:
            raw = await asyncio.to_thread(_sync_load)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[WikiChunkLoader] 按 ID 加载失败 workspace=%s ids=%d err=%s",
                workspace_id,
                len(clean_ids),
                exc,
            )
            return []

        ids: list[str] = list(raw.get("ids") or [])
        documents: list[str] = list(raw.get("documents") or [])
        metadatas: list[dict[str, Any]] = list(raw.get("metadatas") or [])
        by_id: dict[str, WikiChunk] = {}

        for idx, chunk_id in enumerate(ids):
            meta = metadatas[idx] if idx < len(metadatas) else {}
            if str(meta.get("workspace_id") or "") != str(workspace_id):
                continue
            content = documents[idx] if idx < len(documents) else ""
            page_numbers = meta.get("page_numbers")
            if isinstance(page_numbers, str):
                page_numbers = [
                    int(p) for p in page_numbers.replace(",", " ").split() if p.strip().isdigit()
                ]
            elif isinstance(page_numbers, (list, tuple)):
                page_numbers = [int(p) for p in page_numbers if str(p).isdigit()]
            else:
                page_numbers = []

            by_id[str(chunk_id)] = WikiChunk(
                chunk_id=str(chunk_id),
                chunk_index=int(meta.get("chunk_index") or 0),
                content=str(content or ""),
                page_numbers=page_numbers,
                header_path=str(meta.get("header_path") or ""),
                summary=str(meta.get("summary") or ""),
                source_file=str(meta.get("source_file") or ""),
            )

        return [by_id[cid] for cid in clean_ids if cid in by_id]


_loader: Optional[WikiChunkLoader] = None


def get_wiki_chunk_loader() -> WikiChunkLoader:
    """获取单例 WikiChunkLoader。"""
    global _loader
    if _loader is None:
        _loader = WikiChunkLoader()
    return _loader
