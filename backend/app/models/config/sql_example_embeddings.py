"""
SQL 示例向量化模块。

改为显式生成 embedding 后再交给 ChromaDB，避免触发 Chroma 默认 ONNX 模型下载。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError

from app.config import get_settings
from app.core.llm.async_embedding import get_async_embedding

if TYPE_CHECKING:
    from app.models.config.sql_example import SqlExample

logger = logging.getLogger(__name__)

SQL_EXAMPLES_COLLECTION = "sql_examples_v2"
SQL_EXAMPLES_SYNC_BATCH_SIZE = 32

_chroma_client: Optional[chromadb.ClientAPI] = None


def _is_missing_collection_error(exc: Exception) -> bool:
    if isinstance(exc, NotFoundError):
        return True
    if isinstance(exc, ValueError):
        return "does not exist" in str(exc).lower()
    return False


def _get_chroma_client() -> chromadb.ClientAPI:
    """获取 ChromaDB 客户端（单例）。"""
    global _chroma_client
    if _chroma_client is None:
        settings = get_settings()
        persist_path = f"{settings.app.data_dir}/chroma_sql_examples"
        _chroma_client = chromadb.PersistentClient(
            path=persist_path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info("SQL 示例向量库初始化: %s", persist_path)
    return _chroma_client


def get_sql_examples_collection():
    """获取 SQL 示例向量集合。"""
    client = _get_chroma_client()
    return client.get_or_create_collection(
        name=SQL_EXAMPLES_COLLECTION,
        metadata={
            "hnsw:space": "cosine",
            "description": "SQL 示例向量匹配库（显式 embedding）",
            "embedding_source": "external",
        },
    )


def _example_chroma_id(example_id: int) -> str:
    return f"example_{example_id}"


def _build_sql_example_text(question: str, description: str = "") -> str:
    parts = [str(question or "").strip(), str(description or "").strip()]
    return "\n".join(part for part in parts if part).strip()


def _build_sql_example_metadata(
    *,
    example_id: int,
    question: str,
    sql: str,
    workspace_id: str,
    description: str = "",
    tables: str = "",
    is_active: bool = True,
) -> Dict[str, Any]:
    return {
        "id": str(example_id),
        "workspace_id": str(workspace_id),
        "sql": str(sql or ""),
        "tables": str(tables or ""),
        "question": str(question or ""),
        "description": str(description or ""),
        "is_active": bool(is_active),
    }


def _search_where_filter(workspace_id: str) -> Dict[str, Any]:
    return {
        "$and": [
            {"workspace_id": {"$eq": str(workspace_id)}},
            {"is_active": {"$eq": True}},
        ]
    }


async def _upsert_sql_example_records(records: Iterable[Dict[str, Any]]) -> bool:
    payloads = list(records)
    if not payloads:
        return True

    collection = get_sql_examples_collection()
    texts = [payload["text"] for payload in payloads]
    embedding_client = get_async_embedding()

    try:
        embeddings = await embedding_client.embed_texts(texts)
        await asyncio.to_thread(
            collection.upsert,
            ids=[payload["chroma_id"] for payload in payloads],
            documents=texts,
            embeddings=embeddings,
            metadatas=[payload["metadata"] for payload in payloads],
        )
        return True
    except Exception as exc:
        logger.error("SQL 示例向量 upsert 失败: %s", exc)
        return False


async def add_sql_example_embedding(
    example_id: int,
    question: str,
    sql: str,
    workspace_id: str,
    description: str = "",
    tables: str = "",
    is_active: bool = True,
) -> bool:
    """添加 SQL 示例向量。"""
    text = _build_sql_example_text(question, description)
    success = await _upsert_sql_example_records(
        [
            {
                "chroma_id": _example_chroma_id(example_id),
                "text": text,
                "metadata": _build_sql_example_metadata(
                    example_id=example_id,
                    question=question,
                    sql=sql,
                    workspace_id=workspace_id,
                    description=description,
                    tables=tables,
                    is_active=is_active,
                ),
            }
        ]
    )
    if success:
        logger.info("SQL 示例向量添加成功: id=%s, active=%s", example_id, is_active)
    return success


async def update_sql_example_embedding(
    example_id: int,
    question: str,
    sql: str,
    workspace_id: str,
    description: str = "",
    tables: str = "",
    is_active: bool = True,
) -> bool:
    """更新 SQL 示例向量。"""
    success = await add_sql_example_embedding(
        example_id=example_id,
        question=question,
        sql=sql,
        workspace_id=workspace_id,
        description=description,
        tables=tables,
        is_active=is_active,
    )
    if success:
        logger.info("SQL 示例向量更新成功: id=%s, active=%s", example_id, is_active)
    return success


async def delete_sql_example_embedding(example_id: int) -> bool:
    """删除 SQL 示例向量。"""
    collection = get_sql_examples_collection()
    try:
        await asyncio.to_thread(collection.delete, ids=[_example_chroma_id(example_id)])
        logger.info("SQL 示例向量删除成功: id=%s", example_id)
        return True
    except Exception as exc:
        logger.warning("SQL 示例向量删除失败 (可能不存在): %s", exc)
        return False


async def batch_delete_sql_example_embeddings(example_ids: List[int]) -> bool:
    """批量删除 SQL 示例向量。"""
    if not example_ids:
        return True

    collection = get_sql_examples_collection()
    chroma_ids = [_example_chroma_id(eid) for eid in example_ids]

    try:
        await asyncio.to_thread(collection.delete, ids=chroma_ids)
        logger.info("SQL 示例向量批量删除成功: count=%s", len(example_ids))
        return True
    except Exception as exc:
        logger.error("SQL 示例向量批量删除失败: %s", exc)
        return False


async def _load_sql_examples_by_ids(
    example_ids: List[int],
    workspace_id: str,
    *,
    active_only: bool,
) -> Dict[int, "SqlExample"]:
    if not example_ids:
        return {}

    from sqlalchemy import select

    from app.core.db.database import get_async_db_context
    from app.models.config.sql_example import SqlExample, SqlExampleModel

    async with get_async_db_context() as session:
        stmt = select(SqlExampleModel).where(
            SqlExampleModel.id.in_(example_ids),
            SqlExampleModel.workspace_id == workspace_id,
        )
        if active_only:
            stmt = stmt.where(SqlExampleModel.is_active.is_(True))

        result = await session.execute(stmt)
        rows = result.scalars().all()

    return {int(row.id): SqlExample.from_orm(row) for row in rows}


async def search_sql_examples_by_similarity(
    question: str,
    workspace_id: str,
    threshold: Optional[float] = None,
    n_results: int = 1,
) -> List[Tuple["SqlExample", float]]:
    """通过向量相似度搜索 SQL 示例。"""
    query = str(question or "").strip()
    if not query:
        return []

    if threshold is None:
        settings = get_settings()
        threshold = float(getattr(settings.supervisor, "sql_example_threshold", 0.7))

    collection = get_sql_examples_collection()
    embedding_client = get_async_embedding()

    try:
        query_embedding = await embedding_client.embed_single(query)
        results = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_embedding],
            n_results=max(1, int(n_results)),
            where=_search_where_filter(workspace_id),
            include=["metadatas", "distances"],
        )
    except Exception as exc:
        logger.error("SQL 示例向量搜索失败: %s", exc)
        return []

    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]
    if not metadatas:
        logger.info("SQL 示例匹配: 用户问题='%s...' -> 无结果", query[:30])
        return []

    candidate_ids: List[int] = []
    score_logs: List[Tuple[str, float]] = []
    ranked_candidates: List[Tuple[int, float, str]] = []

    for metadata, distance in zip(metadatas, distances):
        if not metadata:
            continue
        raw_id = metadata.get("id")
        if raw_id is None:
            continue
        try:
            example_id = int(raw_id)
        except (TypeError, ValueError):
            continue

        similarity = 1 - float(distance)
        question_preview = str(metadata.get("question", ""))[:30]
        candidate_ids.append(example_id)
        score_logs.append((question_preview, similarity))
        ranked_candidates.append((example_id, similarity, question_preview))

    if not candidate_ids:
        logger.info("SQL 示例匹配: 用户问题='%s...' -> 无有效结果", query[:30])
        return []

    example_map = await _load_sql_examples_by_ids(candidate_ids, workspace_id, active_only=True)

    matches: List[Tuple["SqlExample", float]] = []
    for example_id, similarity, _preview in ranked_candidates:
        example = example_map.get(example_id)
        if example is None:
            continue
        if similarity >= threshold:
            matches.append((example, similarity))

    scores_str = ", ".join([f"'{q}': {s:.4f}" for q, s in score_logs[:3]])
    if matches:
        logger.info(
            "SQL 示例匹配成功: 用户问题='%s...' -> 命中 %s 条, 分数=[%s], 阈值=%s",
            query[:30],
            len(matches),
            scores_str,
            threshold,
        )
    else:
        logger.info(
            "SQL 示例匹配失败: 用户问题='%s...' -> 分数=[%s] < 阈值 %s",
            query[:30],
            scores_str,
            threshold,
        )

    return matches


async def clear_sql_example_embeddings(workspace_id: Optional[str] = None) -> None:
    """清理 SQL 示例向量，可按工作空间或全量清理。"""
    client = _get_chroma_client()

    if workspace_id:
        collection = get_sql_examples_collection()
        await asyncio.to_thread(
            collection.delete,
            where={"workspace_id": {"$eq": str(workspace_id)}},
        )
        logger.info("已清理工作空间 SQL 示例向量: workspace_id=%s", workspace_id)
        return

    try:
        client.delete_collection(SQL_EXAMPLES_COLLECTION)
        logger.info("已删除 SQL 示例向量集合: %s", SQL_EXAMPLES_COLLECTION)
    except Exception as exc:
        if _is_missing_collection_error(exc):
            logger.info("SQL 示例向量集合不存在，无需删除: %s", SQL_EXAMPLES_COLLECTION)
            return
        logger.exception("删除 SQL 示例向量集合失败: %s", SQL_EXAMPLES_COLLECTION)
        raise


async def _load_examples_for_sync(workspace_id: Optional[str] = None) -> List["SqlExample"]:
    from sqlalchemy import select

    from app.core.db.database import get_async_db_context
    from app.models.config.sql_example import SqlExample, SqlExampleModel

    async with get_async_db_context() as session:
        stmt = select(SqlExampleModel).order_by(
            SqlExampleModel.workspace_id.asc(),
            SqlExampleModel.created_at.asc(),
        )
        if workspace_id:
            stmt = stmt.where(SqlExampleModel.workspace_id == workspace_id)

        result = await session.execute(stmt)
        rows = result.scalars().all()

    return [SqlExample.from_orm(row) for row in rows]


async def _update_sync_status(example_ids: List[int], status: str) -> None:
    if not example_ids:
        return

    from sqlalchemy import update

    from app.core.db.database import get_async_db_context
    from app.models.config.sql_example import SqlExampleModel

    async with get_async_db_context() as session:
        await session.execute(
            update(SqlExampleModel)
            .where(SqlExampleModel.id.in_(example_ids))
            .values(vector_sync_status=status)
        )
        await session.commit()


async def _update_sync_status_for_scope(
    *,
    status: str,
    workspace_id: Optional[str] = None,
) -> None:
    from sqlalchemy import update

    from app.core.db.database import get_async_db_context
    from app.models.config.sql_example import SqlExampleModel

    async with get_async_db_context() as session:
        stmt = update(SqlExampleModel).values(vector_sync_status=status)
        if workspace_id:
            stmt = stmt.where(SqlExampleModel.workspace_id == workspace_id)

        await session.execute(stmt)
        await session.commit()


async def sync_all_sql_examples_to_vector(
    workspace_id: Optional[str] = None,
    *,
    reset: bool = False,
) -> Dict[str, Any]:
    """
    同步 SQL 示例到向量库。

    用于首次迁移、历史重建或修复向量缺失。
    """
    await _update_sync_status_for_scope(
        status="pending_update",
        workspace_id=workspace_id,
    )

    if reset:
        await clear_sql_example_embeddings(workspace_id=workspace_id)

    examples = await _load_examples_for_sync(workspace_id)
    summary = {
        "workspace_id": workspace_id,
        "total": len(examples),
        "synced": 0,
        "failed": 0,
        "reset": bool(reset),
        "collection": SQL_EXAMPLES_COLLECTION,
    }

    if not examples:
        return summary

    for offset in range(0, len(examples), SQL_EXAMPLES_SYNC_BATCH_SIZE):
        batch = examples[offset : offset + SQL_EXAMPLES_SYNC_BATCH_SIZE]
        payloads = [
            {
                "chroma_id": _example_chroma_id(int(example.id or 0)),
                "text": _build_sql_example_text(example.question, example.description or ""),
                "metadata": _build_sql_example_metadata(
                    example_id=int(example.id or 0),
                    question=example.question,
                    sql=example.sql,
                    workspace_id=example.workspace_id,
                    description=example.description or "",
                    tables=example.tables or "",
                    is_active=bool(example.is_active),
                ),
            }
            for example in batch
            if example.id is not None
        ]
        example_ids = [int(example.id) for example in batch if example.id is not None]

        if not payloads:
            continue

        success = await _upsert_sql_example_records(payloads)
        if success:
            summary["synced"] += len(payloads)
            await _update_sync_status(example_ids, "synced")
        else:
            summary["failed"] += len(payloads)
            await _update_sync_status(example_ids, "pending_update")

    return summary
