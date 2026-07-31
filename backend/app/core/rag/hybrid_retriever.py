"""
混合检索引擎 (Hybrid Retriever)

结合 Dense (向量语义) 和 Sparse (BM25 关键词) 双路检索，
通过合并去重提供更全面的候选文档召回。
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Set

logger = logging.getLogger(__name__)

import chromadb
import bm25s
from cachetools import LRUCache, TTLCache

from app.config import get_settings
from app.core.security.data_scope import (
    build_chroma_permission_filter,
    can_access_metadata,
    normalize_visibility,
    resolve_data_scope,
    resolve_scope_dept_ids,
)
from app.core.db.database import get_async_db_context
from app.models.common.context import UserContext
from app.models.common.execution import DocumentChunk
from app.models.knowledge.graph import File
from app.core.llm.async_embedding import get_async_embedding
from app.core.rag.base_retriever import BaseRetriever
from app.core.rag.tokenization import tokenize_mixed_text, tokenize_corpus


BM25_STATE_READY = "ready"
BM25_STATE_DIRTY = "dirty"
BM25_STATE_BUILDING = "building"
BM25_STATE_UNAVAILABLE = "unavailable"

BM25_ERROR_EMPTY_WORKSPACE = "empty_workspace"
BM25_ERROR_EMPTY_CORPUS_TOKENS = "empty_corpus_tokens"
BM25_ERROR_STALE_BUILDING = "stale_building_recovered"

_BM25_BUILD_LOCKS: Dict[str, asyncio.Lock] = {}
_BM25_META_LOCKS: Dict[str, asyncio.Lock] = {}
_BM25_DEBOUNCE_TASKS: Dict[str, asyncio.Task] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_utc_iso(value: Optional[str]) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_path_component(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "").strip())
    return normalized[:80] or hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()


@dataclass
class BM25LifecycleMeta:
    workspace_id: str
    index_version: str
    state: str = BM25_STATE_UNAVAILABLE
    data_version: int = 0
    built_data_version: int = 0
    ready_build_id: Optional[str] = None
    sparse_disabled: bool = False
    last_build_started_version: int = 0
    last_build_completed_version: int = 0
    last_build_started_at: Optional[str] = None
    last_build_completed_at: Optional[str] = None
    last_error: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def empty(cls, workspace_id: str, index_version: str) -> "BM25LifecycleMeta":
        return cls(
            workspace_id=workspace_id,
            index_version=index_version,
            updated_at=_utc_now_iso(),
        )

    @classmethod
    def from_dict(
        cls,
        workspace_id: str,
        index_version: str,
        data: Optional[Dict[str, Any]],
    ) -> "BM25LifecycleMeta":
        payload = dict(data or {})
        return cls(
            workspace_id=str(payload.get("workspace_id") or workspace_id),
            index_version=str(payload.get("index_version") or index_version),
            state=str(payload.get("state") or BM25_STATE_UNAVAILABLE),
            data_version=max(0, int(payload.get("data_version") or 0)),
            built_data_version=max(0, int(payload.get("built_data_version") or 0)),
            ready_build_id=str(payload.get("ready_build_id") or "") or None,
            sparse_disabled=bool(payload.get("sparse_disabled", False)),
            last_build_started_version=max(0, int(payload.get("last_build_started_version") or 0)),
            last_build_completed_version=max(0, int(payload.get("last_build_completed_version") or 0)),
            last_build_started_at=str(payload.get("last_build_started_at") or "") or None,
            last_build_completed_at=str(payload.get("last_build_completed_at") or "") or None,
            last_error=str(payload.get("last_error") or "") or None,
            updated_at=str(payload.get("updated_at") or "") or None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "index_version": self.index_version,
            "state": self.state,
            "data_version": int(self.data_version),
            "built_data_version": int(self.built_data_version),
            "ready_build_id": self.ready_build_id,
            "sparse_disabled": bool(self.sparse_disabled),
            "last_build_started_version": int(self.last_build_started_version),
            "last_build_completed_version": int(self.last_build_completed_version),
            "last_build_started_at": self.last_build_started_at,
            "last_build_completed_at": self.last_build_completed_at,
            "last_error": self.last_error,
            "updated_at": self.updated_at or _utc_now_iso(),
        }


@dataclass
class RetrievalCandidate:
    """检索候选项"""
    chunk_id: str
    content: str
    score: float  # Dense 或 Sparse 分数
    source: str  # "dense" 或 "sparse"
    metadata: Dict[str, Any]


@dataclass
class SparseSearchResult:
    candidates: List[RetrievalCandidate]
    sparse_degenerate: bool = False


class HybridRetriever(BaseRetriever):
    """
    混合检索器
    
    核心功能：
    1. Dense 路线：ChromaDB 向量语义搜索
    2. Sparse 路线：BM25s 关键词精确匹配
    3. 双路合并去重，返回 Top-N 候选
    
    设计思路：
    - "宁滥勿缺"：第一层召回尽量广泛
    - 去重合并：相同 chunk_id 只保留最高分
    - 为 Reranker 提供足够的候选
    """
    
    def __init__(self, chroma_client: Optional[chromadb.ClientAPI] = None):
        settings = get_settings()
        self.rag_settings = settings.rag
        
        # ChromaDB 客户端
        if chroma_client is None:
            persist_path = Path(settings.chroma.persist_dir).resolve()
            persist_path.mkdir(parents=True, exist_ok=True)
            chroma_client = chromadb.PersistentClient(path=str(persist_path))
        
        self.chroma_client = chroma_client
        self._collection = None
        self._embedding_client = None
        self._bm25_storage_root = Path(settings.chroma.persist_dir).resolve() / "bm25_indexes"
        self._bm25_storage_root.mkdir(parents=True, exist_ok=True)
        
        # BM25 索引 LRU 缓存 (限制最多10个 workspace，避免内存爆炸)
        self._bm25_cache: LRUCache = LRUCache(maxsize=10)
        soft_deleted_cache_maxsize = max(
            1,
            int(self._rag_flag("soft_deleted_cache_maxsize", 100)),
        )
        soft_deleted_cache_ttl = max(
            1,
            int(self._rag_flag("soft_deleted_cache_ttl_seconds", 30)),
        )
        self._soft_deleted_cache: TTLCache = TTLCache(
            maxsize=soft_deleted_cache_maxsize,
            ttl=soft_deleted_cache_ttl,
        )
        
        # [Issue5修复] 部门树缓存 (Key: dept_id, Value: list of child dept_ids)
        # TTL 5分钟，避免频繁查询数据库
        self._dept_tree_cache: LRUCache = LRUCache(maxsize=100)

    def _rag_flag(self, name: str, default: Any) -> Any:
        return getattr(self.rag_settings, name, default)

    def _bm25_index_version(self) -> str:
        return str(self._rag_flag("bm25_index_version", "v1"))

    def _bm25_workspace_key(self, workspace_id: str) -> str:
        return f"{workspace_id}:{self._bm25_index_version()}"

    def _get_bm25_build_lock(self, workspace_id: str) -> asyncio.Lock:
        key = self._bm25_workspace_key(workspace_id)
        lock = _BM25_BUILD_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _BM25_BUILD_LOCKS[key] = lock
        return lock

    def _get_bm25_meta_lock(self, workspace_id: str) -> asyncio.Lock:
        key = self._bm25_workspace_key(workspace_id)
        lock = _BM25_META_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _BM25_META_LOCKS[key] = lock
        return lock

    def _get_bm25_workspace_dir(self, workspace_id: str) -> Path:
        workspace_hash = hashlib.sha1(str(workspace_id).encode("utf-8")).hexdigest()[:12]
        workspace_key = f"{_safe_path_component(workspace_id)}_{workspace_hash}"
        version_key = _safe_path_component(self._bm25_index_version())
        return self._bm25_storage_root / version_key / workspace_key

    def _get_bm25_meta_path(self, workspace_id: str) -> Path:
        return self._get_bm25_workspace_dir(workspace_id) / "meta.json"

    def _get_bm25_build_dir(self, workspace_id: str, build_id: str) -> Path:
        return self._get_bm25_workspace_dir(workspace_id) / "builds" / build_id

    def _make_bm25_build_id(self, data_version: int) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return f"dv{max(0, int(data_version))}_{timestamp}"

    def _read_bm25_meta_unlocked(self, workspace_id: str) -> BM25LifecycleMeta:
        meta_path = self._get_bm25_meta_path(workspace_id)
        if not meta_path.exists():
            return BM25LifecycleMeta.empty(workspace_id, self._bm25_index_version())

        try:
            with meta_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            return BM25LifecycleMeta.from_dict(workspace_id, self._bm25_index_version(), payload)
        except Exception as exc:
            logger.warning("[HybridRetriever] failed to read BM25 meta for workspace=%s: %s", workspace_id, exc)
            meta = BM25LifecycleMeta.empty(workspace_id, self._bm25_index_version())
            meta.last_error = f"meta_read_failed:{exc}"
            return meta

    def _write_bm25_meta_unlocked(self, meta: BM25LifecycleMeta) -> None:
        meta.updated_at = _utc_now_iso()
        meta_path = self._get_bm25_meta_path(meta.workspace_id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = meta_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(meta.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, meta_path)

    async def _read_bm25_meta(self, workspace_id: str) -> BM25LifecycleMeta:
        lock = self._get_bm25_meta_lock(workspace_id)
        async with lock:
            return self._read_bm25_meta_unlocked(workspace_id)

    def _get_bm25_active_cache_key(self, workspace_id: str, meta: BM25LifecycleMeta) -> Optional[str]:
        if not meta.ready_build_id:
            return None
        return (
            f"{workspace_id}:{meta.index_version}:"
            f"{meta.ready_build_id}:{int(meta.built_data_version)}"
        )

    def _bm25_build_stale_seconds(self) -> float:
        return max(0.0, float(self._rag_flag("bm25_build_stale_seconds", 300.0)))

    def _bm25_has_ready_build(self, workspace_id: str, meta: BM25LifecycleMeta) -> bool:
        if not meta.ready_build_id or int(meta.built_data_version) <= 0:
            return False
        return self._get_bm25_build_dir(workspace_id, meta.ready_build_id).exists()

    def _bm25_has_servable_ready_build(self, workspace_id: str, meta: BM25LifecycleMeta) -> bool:
        return (not meta.sparse_disabled) and self._bm25_has_ready_build(workspace_id, meta)

    def _is_stale_building_meta(self, meta: BM25LifecycleMeta) -> bool:
        if meta.state != BM25_STATE_BUILDING:
            return False
        stale_after = self._bm25_build_stale_seconds()
        if stale_after <= 0:
            return False
        started_at = _parse_utc_iso(meta.last_build_started_at)
        if started_at is None:
            return True
        age_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
        return age_seconds >= stale_after

    def _bm25_meta_can_serve(self, workspace_id: str, meta: BM25LifecycleMeta) -> bool:
        if not self._bm25_has_servable_ready_build(workspace_id, meta):
            return False
        return meta.state in {
            BM25_STATE_READY,
            BM25_STATE_DIRTY,
            BM25_STATE_BUILDING,
        }

    def _fetch_workspace_documents_sync(self, workspace_id: str) -> List[Dict[str, Any]]:
        all_docs = self.collection.get(
            where={"workspace_id": {"$eq": workspace_id}},
            include=["documents", "metadatas"],
        )
        if not all_docs or not all_docs.get("documents"):
            return []

        documents: List[Dict[str, Any]] = []
        raw_documents = all_docs.get("documents") or []
        raw_metadatas = all_docs.get("metadatas") or []
        raw_ids = all_docs.get("ids") or []
        for i, doc in enumerate(raw_documents):
            chunk_id = raw_ids[i] if i < len(raw_ids) else f"doc_{i}"
            metadata = raw_metadatas[i] if i < len(raw_metadatas) else {}
            documents.append(
                {
                    "chunk_id": chunk_id,
                    "content": doc,
                    "metadata": metadata or {},
                }
            )
        return documents

    def _build_and_save_bm25_sync(
        self,
        workspace_id: str,
        build_id: str,
        documents: List[Dict[str, Any]],
        use_jieba: bool,
    ) -> bool:
        build_dir = self._get_bm25_build_dir(workspace_id, build_id)
        if build_dir.exists():
            shutil.rmtree(build_dir, ignore_errors=True)
        build_dir.parent.mkdir(parents=True, exist_ok=True)

        corpus = [str(item.get("content") or "") for item in documents]
        corpus_tokens = tokenize_corpus(corpus, use_jieba=use_jieba)
        if not any(corpus_tokens):
            return False

        retriever = bm25s.BM25()
        retriever.index(corpus_tokens, show_progress=False, leave_progress=False)
        retriever.save(str(build_dir), corpus=documents)
        return True

    async def _load_bm25_index_from_disk(
        self,
        workspace_id: str,
        meta: BM25LifecycleMeta,
    ) -> Optional["BM25Index"]:
        cache_key = self._get_bm25_active_cache_key(workspace_id, meta)
        if not cache_key or not meta.ready_build_id:
            return None
        if cache_key in self._bm25_cache:
            return self._bm25_cache[cache_key]

        build_dir = self._get_bm25_build_dir(workspace_id, meta.ready_build_id)
        if not build_dir.exists():
            return None

        try:
            mmap_enabled = bool(self._rag_flag("bm25_load_mmap", False))
            retriever = await asyncio.to_thread(
                bm25s.BM25.load,
                str(build_dir),
                load_corpus=True,
                mmap=mmap_enabled,
            )
            documents = getattr(retriever, "corpus", []) or []
            bm25_index = BM25Index(
                retriever=retriever,
                documents=documents,
                workspace_id=workspace_id,
                cache_key=cache_key,
                data_version=int(meta.built_data_version),
                build_id=meta.ready_build_id,
            )
            self._bm25_cache[cache_key] = bm25_index
            return bm25_index
        except Exception as exc:
            logger.warning(
                "[HybridRetriever] failed to load BM25 index from disk workspace=%s build=%s: %s",
                workspace_id,
                meta.ready_build_id,
                exc,
            )
            return None

    def _cleanup_bm25_build_dirs_sync(self, workspace_id: str, keep_build_id: Optional[str]) -> None:
        builds_dir = self._get_bm25_workspace_dir(workspace_id) / "builds"
        if not builds_dir.exists():
            return
        for child in builds_dir.iterdir():
            if not child.is_dir():
                continue
            if keep_build_id and child.name == keep_build_id:
                continue
            shutil.rmtree(child, ignore_errors=True)

    async def _recover_stale_building_meta(
        self,
        workspace_id: str,
        meta: BM25LifecycleMeta,
    ) -> BM25LifecycleMeta:
        if not self._is_stale_building_meta(meta):
            return meta

        meta_lock = self._get_bm25_meta_lock(workspace_id)
        async with meta_lock:
            current = self._read_bm25_meta_unlocked(workspace_id)
            if not self._is_stale_building_meta(current):
                return current

            current.state = (
                BM25_STATE_DIRTY
                if (self._bm25_has_ready_build(workspace_id, current) or current.data_version > 0)
                else BM25_STATE_UNAVAILABLE
            )
            current.last_error = BM25_ERROR_STALE_BUILDING
            self._write_bm25_meta_unlocked(current)
            return current

    async def _mark_bm25_ready_build_broken(
        self,
        workspace_id: str,
        build_id: Optional[str],
        error: str,
    ) -> BM25LifecycleMeta:
        meta_lock = self._get_bm25_meta_lock(workspace_id)
        async with meta_lock:
            current = self._read_bm25_meta_unlocked(workspace_id)
            if build_id and current.ready_build_id == build_id:
                current.ready_build_id = None
                current.built_data_version = 0
                current.last_build_completed_version = 0
            current.state = (
                BM25_STATE_DIRTY
                if (current.data_version > 0 and not current.sparse_disabled)
                else BM25_STATE_UNAVAILABLE
            )
            current.last_error = (error or "ready_build_broken")[:1000]
            self._write_bm25_meta_unlocked(current)

        self.invalidate_bm25_cache(workspace_id)
        if build_id:
            await asyncio.to_thread(
                shutil.rmtree,
                self._get_bm25_build_dir(workspace_id, build_id),
                True,
            )
        return current

    async def _finalize_failed_bm25_build(
        self,
        workspace_id: str,
        *,
        error: str,
        failed_build_id: Optional[str] = None,
    ) -> bool:
        meta_lock = self._get_bm25_meta_lock(workspace_id)
        async with meta_lock:
            current = self._read_bm25_meta_unlocked(workspace_id)
            keep_ready = self._bm25_has_servable_ready_build(workspace_id, current)
            current.last_error = (error or "bm25_build_failed")[:1000]
            current.last_build_completed_at = _utc_now_iso()
            if keep_ready:
                current.state = BM25_STATE_DIRTY
            else:
                current.state = BM25_STATE_UNAVAILABLE
                current.ready_build_id = None
                current.built_data_version = 0
                current.last_build_completed_version = 0
                current.sparse_disabled = False
            self._write_bm25_meta_unlocked(current)

        self.invalidate_bm25_cache(workspace_id)
        if keep_ready:
            if failed_build_id:
                await asyncio.to_thread(
                    shutil.rmtree,
                    self._get_bm25_build_dir(workspace_id, failed_build_id),
                    True,
                )
        else:
            await asyncio.to_thread(self._cleanup_bm25_build_dirs_sync, workspace_id, None)
        return keep_ready

    async def mark_bm25_dirty(
        self,
        workspace_id: str,
        *,
        disable_sparse: bool = False,
    ) -> int:
        if not workspace_id:
            return 0

        meta_lock = self._get_bm25_meta_lock(workspace_id)
        async with meta_lock:
            meta = self._read_bm25_meta_unlocked(workspace_id)
            baseline_version = max(int(meta.data_version), int(meta.built_data_version), 0)
            meta.data_version = baseline_version + 1
            meta.state = BM25_STATE_DIRTY
            meta.last_error = None
            if disable_sparse:
                meta.sparse_disabled = True
            self._write_bm25_meta_unlocked(meta)

        if disable_sparse:
            self.invalidate_bm25_cache(workspace_id)

        return meta.data_version

    async def notify_bm25_content_change(
        self,
        workspace_id: str,
        *,
        disable_sparse: bool = False,
        debounce_seconds: Optional[float] = None,
    ) -> int:
        data_version = await self.mark_bm25_dirty(
            workspace_id,
            disable_sparse=disable_sparse,
        )
        self.schedule_bm25_rebuild(
            workspace_id,
            debounce_seconds=debounce_seconds,
            reset_debounce=True,
            reason="delete" if disable_sparse else "content_change",
        )
        return data_version

    def schedule_bm25_rebuild(
        self,
        workspace_id: str,
        *,
        debounce_seconds: Optional[float] = None,
        reset_debounce: bool = True,
        reason: str = "",
    ) -> None:
        if not workspace_id:
            return

        delay = debounce_seconds
        if delay is None:
            delay = float(self._rag_flag("bm25_rebuild_debounce_seconds", 5))
        delay = max(0.0, float(delay))

        task_key = self._bm25_workspace_key(workspace_id)
        existing = _BM25_DEBOUNCE_TASKS.get(task_key)
        if existing and not existing.done():
            if not reset_debounce:
                return
            existing.cancel()

        async def _debounced_trigger() -> None:
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                asyncio.create_task(self._run_bm25_build_task(workspace_id, reason=reason))
            except asyncio.CancelledError:
                return
            finally:
                current = _BM25_DEBOUNCE_TASKS.get(task_key)
                if current is asyncio.current_task():
                    _BM25_DEBOUNCE_TASKS.pop(task_key, None)

        _BM25_DEBOUNCE_TASKS[task_key] = asyncio.create_task(_debounced_trigger())

    async def _run_bm25_build_task(self, workspace_id: str, *, reason: str = "") -> None:
        try:
            await self.rebuild_bm25_index_now(workspace_id)
        except Exception as exc:
            logger.warning(
                "[HybridRetriever] async BM25 rebuild failed workspace=%s reason=%s error=%s",
                workspace_id,
                reason,
                exc,
            )

    async def rebuild_bm25_index_now(
        self,
        workspace_id: str,
        *,
        force: bool = False,
    ) -> bool:
        if not workspace_id:
            return False

        build_lock = self._get_bm25_build_lock(workspace_id)
        meta_lock = self._get_bm25_meta_lock(workspace_id)
        async with build_lock:
            build_id: Optional[str] = None
            ready_probe_meta: Optional[BM25LifecycleMeta] = None
            try:
                async with meta_lock:
                    meta = self._read_bm25_meta_unlocked(workspace_id)
                    target_version = max(int(meta.data_version), int(meta.built_data_version), 0)
                    if target_version <= 0:
                        target_version = 1
                        meta.data_version = 1

                    ready_dir_exists = bool(
                        meta.ready_build_id and self._get_bm25_build_dir(workspace_id, meta.ready_build_id).exists()
                    )
                    if (
                        not force
                        and meta.state == BM25_STATE_READY
                        and ready_dir_exists
                        and meta.ready_build_id
                        and meta.built_data_version == meta.data_version
                        and not meta.sparse_disabled
                    ):
                        ready_probe_meta = BM25LifecycleMeta.from_dict(
                            workspace_id,
                            self._bm25_index_version(),
                            meta.to_dict(),
                        )
                    else:
                        meta.state = BM25_STATE_BUILDING
                        meta.last_build_started_version = target_version
                        meta.last_build_started_at = _utc_now_iso()
                        meta.last_error = None
                        self._write_bm25_meta_unlocked(meta)

                if ready_probe_meta is not None:
                    probe = await self._load_bm25_index_from_disk(workspace_id, ready_probe_meta)
                    if probe is not None:
                        return True
                    await self._mark_bm25_ready_build_broken(
                        workspace_id,
                        ready_probe_meta.ready_build_id,
                        "ready_build_load_failed",
                    )
                    async with meta_lock:
                        meta = self._read_bm25_meta_unlocked(workspace_id)
                        target_version = max(int(meta.data_version), int(meta.built_data_version), 0)
                        if target_version <= 0:
                            target_version = 1
                            meta.data_version = 1
                        meta.state = BM25_STATE_BUILDING
                        meta.last_build_started_version = target_version
                        meta.last_build_started_at = _utc_now_iso()
                        meta.last_error = None
                        self._write_bm25_meta_unlocked(meta)

                documents = await asyncio.to_thread(self._fetch_workspace_documents_sync, workspace_id)
                if not documents:
                    await self._finalize_failed_bm25_build(
                        workspace_id,
                        error=BM25_ERROR_EMPTY_WORKSPACE,
                    )
                    return False

                use_jieba = bool(self._rag_flag("bm25_use_jieba", True))
                build_id = self._make_bm25_build_id(target_version)
                build_ok = await asyncio.to_thread(
                    self._build_and_save_bm25_sync,
                    workspace_id,
                    build_id,
                    documents,
                    use_jieba,
                )
                if not build_ok:
                    await self._finalize_failed_bm25_build(
                        workspace_id,
                        error=BM25_ERROR_EMPTY_CORPUS_TOKENS,
                        failed_build_id=build_id,
                    )
                    return False

                async with meta_lock:
                    current = self._read_bm25_meta_unlocked(workspace_id)
                    if current.data_version != target_version:
                        current.state = BM25_STATE_DIRTY
                        current.last_error = None
                        self._write_bm25_meta_unlocked(current)
                        stale_build = True
                    else:
                        current.ready_build_id = build_id
                        current.built_data_version = target_version
                        current.last_build_completed_version = target_version
                        current.last_build_completed_at = _utc_now_iso()
                        current.state = BM25_STATE_READY
                        current.sparse_disabled = False
                        current.last_error = None
                        self._write_bm25_meta_unlocked(current)
                        stale_build = False

                self.invalidate_bm25_cache(workspace_id)
                if stale_build:
                    await asyncio.to_thread(
                        shutil.rmtree,
                        self._get_bm25_build_dir(workspace_id, build_id),
                        True,
                    )
                    self.schedule_bm25_rebuild(
                        workspace_id,
                        debounce_seconds=0,
                        reset_debounce=False,
                        reason="version_changed_during_build",
                    )
                    return False

                await asyncio.to_thread(self._cleanup_bm25_build_dirs_sync, workspace_id, build_id)
                return True
            except Exception as exc:
                await self._finalize_failed_bm25_build(
                    workspace_id,
                    error=str(exc),
                    failed_build_id=build_id,
                )
                logger.warning("[HybridRetriever] rebuild BM25 failed workspace=%s: %s", workspace_id, exc)
                return False

    def _apply_file_cap(
        self,
        candidates: List[RetrievalCandidate],
        max_per_file: int,
    ) -> List[RetrievalCandidate]:
        if max_per_file <= 0 or not candidates:
            return candidates

        counts: Dict[str, int] = defaultdict(int)
        filtered: List[RetrievalCandidate] = []
        for candidate in candidates:
            file_id = str((candidate.metadata or {}).get("file_id") or candidate.chunk_id)
            if counts[file_id] >= max_per_file:
                continue
            counts[file_id] += 1
            filtered.append(candidate)
        return filtered

    async def _get_soft_deleted_file_ids(self, workspace_id: str) -> Set[str]:
        if not workspace_id:
            return set()

        cache_key = f"soft_deleted:{workspace_id}"
        if cache_key in self._soft_deleted_cache:
            cached = self._soft_deleted_cache.get(cache_key)
            return set(cached or set())

        try:
            from sqlalchemy import select

            async with get_async_db_context() as session:
                stmt = select(File.id).where(
                    File.workspace_id == workspace_id,
                    File.is_deleted.is_(True),
                )
                result = await session.execute(stmt)
                deleted_ids = {str(file_id) for file_id in result.scalars().all() if file_id}
                self._soft_deleted_cache[cache_key] = deleted_ids
                return deleted_ids
        except Exception as e:
            logger.warning("[HybridRetriever] failed to query soft-deleted files: %s", e)
            return set()

    def invalidate_soft_deleted_cache(self, workspace_id: Optional[str] = None) -> None:
        if workspace_id:
            self._soft_deleted_cache.pop(f"soft_deleted:{workspace_id}", None)
            return
        self._soft_deleted_cache.clear()

    def _exclude_deleted_candidates(
        self,
        candidates: List[RetrievalCandidate],
        deleted_file_ids: Set[str],
    ) -> List[RetrievalCandidate]:
        if not candidates or not deleted_file_ids:
            return candidates
        return [
            c
            for c in candidates
            if str((c.metadata or {}).get("file_id", "")) not in deleted_file_ids
        ]
    
    @property
    def collection(self):
        """懒加载 Collection"""
        if self._collection is None:
            self._collection = self.chroma_client.get_or_create_collection(
                name="tenant_docs",
                metadata={"description": "User uploaded documents for RAG"},
                embedding_function=None  # 禁用默认 embedding，使用 DashScope API
            )
        return self._collection
    
    @property
    def embedding_client(self):
        if self._embedding_client is None:
            self._embedding_client = get_async_embedding()
        return self._embedding_client
    
    async def _build_permission_filter(self, user_context: UserContext) -> tuple[Dict[str, Any], Optional[List[str]]]:
        scope = resolve_data_scope(user_context.data_scope)
        scope_dept_ids = await resolve_scope_dept_ids(
            user_context,
            descendants_loader=lambda dept_id: self._get_descendant_dept_ids(
                dept_id=dept_id,
                workspace_id=user_context.workspace_id,
            ),
        )
        where_filter = build_chroma_permission_filter(
            workspace_id=user_context.workspace_id,
            user_id=str(user_context.user_id),
            scope=scope,
            scope_dept_ids=scope_dept_ids,
            is_workspace_admin=user_context.is_workspace_admin,
        )
        return where_filter, [str(item) for item in scope_dept_ids]

    async def _get_descendant_dept_ids(self, dept_id: int, workspace_id: str) -> List[int]:
        from sqlalchemy import select
        from app.models.auth.organization import DepartmentModel

        cache_key = f"{workspace_id}:dept_tree:{dept_id}"
        cached = self._dept_tree_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        descendants: List[int] = []
        try:
            async with get_async_db_context() as session:
                stmt = select(DepartmentModel.id).where(
                    DepartmentModel.workspace_id == workspace_id,
                    DepartmentModel.ancestors.like(f"%/{dept_id}/%"),
                )
                result = await session.execute(stmt)
                descendants = [int(item) for item in result.scalars().all() if item is not None]
        except Exception as exc:
            logger.warning("[HybridRetriever] Failed to fetch sub-departments: %s", exc)

        self._dept_tree_cache[cache_key] = descendants
        return descendants
    
    def _filter_by_permission(
        self, 
        candidates: List['RetrievalCandidate'], 
        user_context: UserContext,
        allowed_dept_ids: Optional[List[str]] = None
    ) -> List['RetrievalCandidate']:
        """
        应用层权限过滤 (对 Sparse 检索结果进行精确过滤)
        
        基于 visibility + DataScope 双重过滤:
        1. visibility=public: 全局可见
        2. visibility=dept: 部门可见 (受 allowed_dept_ids 限制)
        3. visibility=private: 仅所有者可见
        """
        result = []
        for candidate in candidates:
            if can_access_metadata(candidate.metadata or {}, user_context, allowed_dept_ids):
                result.append(candidate)
        return result
    
    def _apply_scope_filters(
        self,
        candidates: List['RetrievalCandidate'],
        file_ids: Optional[List[str]] = None,
        visibilities: Optional[List[str]] = None,
        dept_ids: Optional[List[str]] = None
    ) -> List['RetrievalCandidate']:
        """在权限过滤后应用 doc_scope 过滤。"""
        if not candidates:
            return []

        file_id_set = set(str(fid) for fid in file_ids) if file_ids else None
        vis_set = {normalize_visibility(v) for v in visibilities} if visibilities else None
        dept_id_set = set(str(d) for d in dept_ids) if dept_ids else None

        result: List[RetrievalCandidate] = []
        for candidate in candidates:
            meta = candidate.metadata or {}
            if file_id_set:
                if str(meta.get("file_id", "")) not in file_id_set:
                    continue
            if vis_set:
                if normalize_visibility(meta.get("visibility")) not in vis_set:
                    continue
            if dept_id_set:
                if normalize_visibility(meta.get("visibility")) == "dept":
                    if str(meta.get("dept_id", "")) not in dept_id_set:
                        continue
            result.append(candidate)
        return result


    async def search(
        self,
        queries: List[str],
        user_context: UserContext,
        top_n: Optional[int] = None,
        include_images: bool = True,  # [v2.1] 是否包含图片摘要
        file_ids: Optional[List[str]] = None,
        visibilities: Optional[List[str]] = None,
        dept_ids: Optional[List[str]] = None,
        **kwargs  # 兼容 BaseRetriever 接口，接受额外参数 (如 session_id)
    ) -> List[DocumentChunk]:
        """
        执行混合检索 (带权限过滤 + 类型过滤)
        
        Args:
            queries: 查询列表（包含原始查询和改写变体）
            user_context: 用户上下文（用于租户隔离 + 权限过滤）
            top_n: 返回候选数量（默认使用配置的 merged_top_n）
            include_images: [v2.1] 是否包含图片摘要结果（True=全部，False=仅文本）
            
        Returns:
            DocumentChunk 候选列表
        """
        _top_n = top_n or self.rag_settings.merged_top_n
        
        if not queries:
            return []
        
        # 并行执行 Dense 和 Sparse 检索
        # 预先构建权限 Filter
        where_filter, allowed_dept_ids = await self._build_permission_filter(user_context)

        # DocScope 过滤（可选）
        if file_ids:
            file_id_list = [str(fid) for fid in file_ids if fid]
            if file_id_list:
                where_filter = {"$and": [where_filter, {"file_id": {"$in": file_id_list}}]}

        if visibilities:
            vis_list = [str(v) for v in visibilities if v]
            if vis_list:
                if "dept" in {normalize_visibility(v) for v in vis_list} and "workspace" not in vis_list:
                    vis_list.append("workspace")
                where_filter = {"$and": [where_filter, {"visibility": {"$in": vis_list}}]}
        
        # [v2.1] 类型过滤：如果不包含图片，则排除 image_summary 类型
        if not include_images:
            where_filter = {
                "$and": [
                    where_filter,
                    {"$or": [
                        {"type": {"$eq": "text"}},
                        {"type": {"$exists": False}}  # 兼容旧数据
                    ]}
                ]
            }
        
        dense_task = self._dense_search(queries, where_filter)
        sparse_enabled = bool(self._rag_flag("hybrid_sparse_enabled", True))
        sparse_task = self._sparse_search(queries, user_context) if sparse_enabled else None

        if sparse_task is not None:
            dense_results, sparse_result = await asyncio.gather(
                dense_task, sparse_task, return_exceptions=True
            )
        else:
            dense_results = await dense_task
            sparse_result = SparseSearchResult(candidates=[], sparse_degenerate=True)
        
        # 处理异常
        if isinstance(dense_results, Exception):
            dense_results = []
        if isinstance(sparse_result, Exception):
            sparse_result = SparseSearchResult(candidates=[], sparse_degenerate=True)

        dense_results = sorted(dense_results, key=lambda x: x.score, reverse=True)
        sparse_candidates = sorted(sparse_result.candidates, key=lambda x: x.score, reverse=True)

        per_source_cap = int(self._rag_flag("hybrid_file_cap_per_source", 4))
        dense_results = self._apply_file_cap(dense_results, per_source_cap)
        sparse_candidates = self._apply_file_cap(sparse_candidates, per_source_cap)
        
        merge_pool_multiplier = max(1, int(self._rag_flag("hybrid_merge_pool_multiplier", 2)))
        merge_pool_top_n = max(_top_n, _top_n * merge_pool_multiplier)

        # 合并去重
        merged = self._merge_results(
            dense_results,
            sparse_candidates,
            merge_pool_top_n,
            sparse_degenerate=bool(sparse_result.sparse_degenerate),
        )
        merged_cap = int(self._rag_flag("hybrid_file_cap_merged", 3))
        merged = self._apply_file_cap(merged, merged_cap)
        merged = merged[:_top_n]

        if bool(self._rag_flag("retrieval_exclude_soft_deleted", True)):
            deleted_file_ids = await self._get_soft_deleted_file_ids(user_context.workspace_id)
            merged = self._exclude_deleted_candidates(merged, deleted_file_ids)
        
        # 应用层权限过滤 (次要过滤，确保安全，同时也处理Sparse结果)
        filtered = self._filter_by_permission(merged, user_context, allowed_dept_ids)
        # 权限过滤后应用 doc_scope 过滤
        filtered = self._apply_scope_filters(filtered, file_ids, visibilities, dept_ids)
        
        # [v2.1] 如果不包含图片，二次过滤 Sparse 结果中的图片摘要
        if not include_images:
            filtered = [c for c in filtered if c.metadata.get("type") != "image_summary"]
        
        # 转换为 DocumentChunk
        return self._to_document_chunks(filtered)
    
    async def _dense_search(
        self,
        queries: List[str],
        where_filter: Dict[str, Any]
    ) -> List[RetrievalCandidate]:
        """
        Dense 检索：向量语义搜索 (带权限过滤)
        
        对每个查询变体分别检索，合并结果
        """
        top_per_query = self.rag_settings.dense_top_n // len(queries)
        top_per_query = max(top_per_query, 5)  # 至少每个查询5条
        
        # 构建权限过滤条件
        # where_filter 由外部传入

        
        all_candidates = []
        
        for query_index, query in enumerate(queries):
            try:
                # 生成查询向量
                query_embedding = await self.embedding_client.embed_single(query)
                
                # ChromaDB 检索 (带权限过滤)
                results = self.collection.query(
                    query_embeddings=[query_embedding],
                    n_results=top_per_query,
                    where=where_filter,
                    include=["documents", "metadatas", "distances"]
                )
                
                # 解析结果
                candidates = self._parse_chroma_results(results, "dense")
                all_candidates.extend(candidates)
                
            except Exception as exc:
                logger.warning(
                    "[HybridRetriever] dense query failed query_index=%d error_type=%s",
                    query_index,
                    type(exc).__name__,
                )
                continue
        
        return all_candidates
    
    async def _sparse_search(
        self,
        queries: List[str],
        user_context: UserContext
    ) -> SparseSearchResult:
        """
        Sparse 检索：BM25 关键词匹配
        
        使用 bm25s 库进行快速 BM25 检索
        """
        # 获取或构建 BM25 索引
        bm25_index = await self._get_or_build_bm25_index(user_context.workspace_id)
        
        if bm25_index is None or bm25_index.is_empty:
            return SparseSearchResult(candidates=[], sparse_degenerate=True)
        
        top_per_query = self.rag_settings.sparse_top_n // len(queries)
        top_per_query = max(top_per_query, 5)
        top_per_query = min(top_per_query, len(bm25_index.documents))
        
        all_candidates: List[RetrievalCandidate] = []
        has_non_empty_query_tokens = False
        has_non_zero_score = False
        malformed_hits = 0
        use_jieba = bool(self._rag_flag("bm25_use_jieba", True))
        
        for query in queries:
            try:
                query_tokens = tokenize_mixed_text(query, use_jieba=use_jieba)
                if not query_tokens:
                    continue
                has_non_empty_query_tokens = True
                
                # BM25 检索
                results, scores = bm25_index.retriever.retrieve(
                    [query_tokens],
                    k=top_per_query,
                    show_progress=False,
                    leave_progress=False,
                )
                
                # 解析结果
                for i, (doc_indices, doc_scores) in enumerate(zip(results, scores)):
                    for hit, score in zip(doc_indices, doc_scores):
                        score_value = float(score)
                        doc: Optional[Mapping[str, Any]] = None
                        fallback_id = "bm25_hit"

                        if isinstance(hit, Mapping):
                            doc = hit
                            fallback_id = str(hit.get("chunk_id") or fallback_id)
                        else:
                            try:
                                doc_index = int(hit)
                            except (TypeError, ValueError, OverflowError):
                                malformed_hits += 1
                                continue
                            if 0 <= doc_index < len(bm25_index.documents):
                                candidate_doc = bm25_index.documents[doc_index]
                                if isinstance(candidate_doc, Mapping):
                                    doc = candidate_doc
                                    fallback_id = f"bm25_{doc_index}"

                        if doc is None:
                            malformed_hits += 1
                            continue

                        if score_value > 0:
                            has_non_zero_score = True
                        all_candidates.append(
                            RetrievalCandidate(
                                chunk_id=str(doc.get("chunk_id") or fallback_id),
                                content=str(doc.get("content") or ""),
                                score=score_value,
                                source="sparse",
                                metadata=dict(doc.get("metadata") or {}),
                            )
                        )

            except Exception as exc:
                logger.warning(
                    "[HybridRetriever] sparse query failed error_type=%s",
                    type(exc).__name__,
                )
                continue

        if malformed_hits:
            logger.warning(
                "[HybridRetriever] ignored malformed sparse hits count=%d",
                malformed_hits,
            )

        sparse_degenerate = (not has_non_empty_query_tokens) or (not has_non_zero_score)
        if sparse_degenerate:
            if not has_non_empty_query_tokens:
                logger.info("[HybridRetriever] sparse degenerated: empty query tokens after tokenization")
            else:
                logger.info("[HybridRetriever] sparse degenerated: top-k scores are all zero")
            return SparseSearchResult(candidates=[], sparse_degenerate=True)

        return SparseSearchResult(candidates=all_candidates, sparse_degenerate=False)
    
    async def _get_or_build_bm25_index(
        self,
        workspace_id: str
    ) -> Optional['BM25Index']:
        """优先从磁盘加载 BM25 索引；查询链路不做同步全量构建。"""
        if not workspace_id:
            return None

        meta = await self._read_bm25_meta(workspace_id)
        meta = await self._recover_stale_building_meta(workspace_id, meta)
        rebuild_needed = (
            meta.state != BM25_STATE_READY
            or meta.built_data_version != meta.data_version
            or meta.sparse_disabled
        )
        if self._bm25_meta_can_serve(workspace_id, meta):
            bm25_index = await self._load_bm25_index_from_disk(workspace_id, meta)
            if bm25_index is not None:
                if rebuild_needed and meta.state != BM25_STATE_BUILDING:
                    query_delay = float(self._rag_flag("bm25_query_build_debounce_seconds", 0))
                    self.schedule_bm25_rebuild(
                        workspace_id,
                        debounce_seconds=query_delay,
                        reset_debounce=False,
                        reason="query_lazy_build",
                    )
                return bm25_index
            await self._mark_bm25_ready_build_broken(
                workspace_id,
                meta.ready_build_id,
                "ready_build_load_failed",
            )
            meta = await self._read_bm25_meta(workspace_id)

        if meta.state not in {BM25_STATE_BUILDING}:
            query_delay = float(self._rag_flag("bm25_query_build_debounce_seconds", 0))
            self.schedule_bm25_rebuild(
                workspace_id,
                debounce_seconds=query_delay,
                reset_debounce=False,
                reason="query_lazy_build",
            )
        return None
    
    def invalidate_bm25_cache(self, workspace_id: str):
        """
        使 BM25 索引缓存失效
        
        当文档库更新时调用此方法
        """
        if not workspace_id:
            return
        stale_keys = [
            key for key in list(self._bm25_cache.keys())
            if str(key).startswith(f"{workspace_id}:")
        ]
        for key in stale_keys:
            del self._bm25_cache[key]

    def invalidate_all_bm25_cache(self):
        """清空所有 workspace 的 BM25 缓存。"""
        self._bm25_cache.clear()
    
    def _parse_chroma_results(
        self,
        results: Dict,
        source: str
    ) -> List[RetrievalCandidate]:
        """解析 ChromaDB 检索结果"""
        candidates = []
        
        if not results or not results.get("documents"):
            return candidates
        
        documents = results["documents"][0] if results["documents"] else []
        metadatas = results["metadatas"][0] if results.get("metadatas") else []
        distances = results["distances"][0] if results.get("distances") else []
        ids = results["ids"][0] if results.get("ids") else []
        
        for i, doc in enumerate(documents):
            metadata = metadatas[i] if i < len(metadatas) else {}
            distance = distances[i] if i < len(distances) else 0
            chunk_id = ids[i] if i < len(ids) else ""
            
            # 距离转相似度分数
            score = 1 / (1 + distance)
            
            candidates.append(RetrievalCandidate(
                chunk_id=chunk_id,
                content=doc,
                score=score,
                source=source,
                metadata=metadata
            ))
        
        return candidates
    
    def _merge_results(
        self,
        dense_results: List[RetrievalCandidate],
        sparse_results: List[RetrievalCandidate],
        top_n: int,
        sparse_degenerate: bool = False,
    ) -> List[RetrievalCandidate]:
        """
        合并 Dense 和 Sparse 结果 (使用 RRF 算法)
        
        RRF (Reciprocal Rank Fusion) 优势：
        - 不依赖具体分数，只依赖排名
        - 避免 Dense/Sparse 分数不可比的问题
        """
        if not dense_results and not sparse_results:
            return []

        k = 60  # RRF 常数，通常取 60
        dense_weight = float(self._rag_flag("hybrid_dense_weight", 0.5))
        sparse_weight = float(self._rag_flag("hybrid_sparse_weight", 0.5))
        sparse_enabled = bool(self._rag_flag("hybrid_sparse_enabled", True))
        if sparse_degenerate or (not sparse_results) or (not sparse_enabled):
            sparse_weight = 0.0
        if not dense_results:
            dense_weight = 0.0

        total_weight = dense_weight + sparse_weight
        if total_weight <= 0:
            dense_weight, sparse_weight = 1.0, 0.0
        else:
            dense_weight /= total_weight
            sparse_weight /= total_weight
        
        # 存储 RRF 分数
        rrf_scores: Dict[str, float] = {}
        # 存储文档对象 (chunk_id -> candidate)
        candidates_map: Dict[str, RetrievalCandidate] = {}
        
        # 处理 Dense 结果
        for rank, doc in enumerate(dense_results):
            chunk_id = doc.chunk_id
            if chunk_id not in rrf_scores:
                rrf_scores[chunk_id] = 0
            rrf_scores[chunk_id] += dense_weight * (1.0 / (k + rank + 1))
            if chunk_id not in candidates_map:
                candidates_map[chunk_id] = doc
        
        # 处理 Sparse 结果
        for rank, doc in enumerate(sparse_results):
            chunk_id = doc.chunk_id
            if chunk_id not in rrf_scores:
                rrf_scores[chunk_id] = 0
            rrf_scores[chunk_id] += sparse_weight * (1.0 / (k + rank + 1))
            if chunk_id not in candidates_map:
                candidates_map[chunk_id] = doc
        
        # 按 RRF 分数排序
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        
        # 返回 top_n 结果，更新 score 为 RRF 分数
        result = []
        for chunk_id in sorted_ids[:top_n]:
            candidate = candidates_map[chunk_id]
            # 更新 score 为 RRF 分数
            result.append(RetrievalCandidate(
                chunk_id=candidate.chunk_id,
                content=candidate.content,
                score=rrf_scores[chunk_id],  # RRF 分数
                source=candidate.source,
                metadata=candidate.metadata
            ))
        return result
    
    def _to_document_chunks(
        self,
        candidates: List[RetrievalCandidate]
    ) -> List[DocumentChunk]:
        """转换为 DocumentChunk"""
        chunks = []
        
        for candidate in candidates:
            chunk = DocumentChunk(
                content=candidate.content,
                source_file=candidate.metadata.get("source_file", ""),
                chunk_id=candidate.chunk_id,
                score=candidate.score,
                metadata=candidate.metadata,
                # 从 metadata 恢复链表关系
                parent_id=candidate.metadata.get("parent_id"),
                prev_id=candidate.metadata.get("prev_id"),
                next_id=candidate.metadata.get("next_id"),
                summary=candidate.metadata.get("summary"),
                header_path=candidate.metadata.get("header_path")
            )
            chunks.append(chunk)
        
        return chunks


@dataclass
class BM25Index:
    """BM25 索引包装"""
    retriever: bm25s.BM25
    documents: Any
    workspace_id: str
    cache_key: str
    data_version: int = 0
    build_id: Optional[str] = None
    
    @property
    def is_empty(self) -> bool:
        return len(self.documents) == 0


# 工厂函数
def get_hybrid_retriever(
    chroma_client: Optional[chromadb.ClientAPI] = None
) -> HybridRetriever:
    """获取混合检索器实例"""
    return HybridRetriever(chroma_client)
