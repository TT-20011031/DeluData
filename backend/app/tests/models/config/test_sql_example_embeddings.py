from __future__ import annotations

import pytest
from chromadb.errors import NotFoundError

from app.models.config import sql_example_embeddings as module
from app.models.config.sql_example import SqlExample


class DummyEmbeddingClient:
    def __init__(self) -> None:
        self.embed_texts_calls = []
        self.embed_single_calls = []

    async def embed_texts(self, texts, model=None, dimensions=None):
        self.embed_texts_calls.append(list(texts))
        return [[float(index + 1)] * 3 for index, _ in enumerate(texts)]

    async def embed_single(self, text, model=None, dimensions=None):
        self.embed_single_calls.append(text)
        return [0.1, 0.2, 0.3]


class DummyCollection:
    def __init__(self, query_result=None) -> None:
        self.upsert_calls = []
        self.query_calls = []
        self.delete_calls = []
        self.query_result = query_result or {"metadatas": [[]], "distances": [[]]}

    def upsert(self, **kwargs):
        self.upsert_calls.append(kwargs)

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return self.query_result

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)


class DummyChromaClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.delete_collection_calls = []

    def delete_collection(self, name):
        self.delete_collection_calls.append(name)
        if self.error is not None:
            raise self.error


@pytest.mark.asyncio
async def test_add_sql_example_embedding_uses_explicit_embeddings(monkeypatch):
    collection = DummyCollection()
    embedding_client = DummyEmbeddingClient()

    monkeypatch.setattr(module, "get_sql_examples_collection", lambda: collection)
    monkeypatch.setattr(module, "get_async_embedding", lambda: embedding_client)

    ok = await module.add_sql_example_embedding(
        example_id=7,
        question="统计近30天订单数",
        sql="select count(*) from orders",
        workspace_id="ws-1",
        description="按创建时间过滤",
        tables="orders",
        is_active=False,
    )

    assert ok is True
    assert embedding_client.embed_texts_calls == [["统计近30天订单数\n按创建时间过滤"]]
    assert len(collection.upsert_calls) == 1

    payload = collection.upsert_calls[0]
    assert payload["ids"] == ["example_7"]
    assert payload["documents"] == ["统计近30天订单数\n按创建时间过滤"]
    assert payload["embeddings"] == [[1.0, 1.0, 1.0]]
    assert payload["metadatas"][0]["workspace_id"] == "ws-1"
    assert payload["metadatas"][0]["is_active"] is False


@pytest.mark.asyncio
async def test_search_sql_examples_by_similarity_uses_query_embeddings(monkeypatch):
    example = SqlExample(
        id=1,
        question="查询本月销量",
        sql="select sum(amount) from sales",
        workspace_id="ws-1",
        created_by="tester",
    )
    collection = DummyCollection(
        query_result={
            "metadatas": [[{"id": "1", "question": "查询本月销量"}]],
            "distances": [[0.1]],
        }
    )
    embedding_client = DummyEmbeddingClient()

    monkeypatch.setattr(module, "get_sql_examples_collection", lambda: collection)
    monkeypatch.setattr(module, "get_async_embedding", lambda: embedding_client)

    async def fake_load(example_ids, workspace_id, *, active_only):
        assert example_ids == [1]
        assert workspace_id == "ws-1"
        assert active_only is True
        return {1: example}

    monkeypatch.setattr(module, "_load_sql_examples_by_ids", fake_load)

    matches = await module.search_sql_examples_by_similarity(
        question="本月销售额是多少",
        workspace_id="ws-1",
        threshold=0.5,
        n_results=2,
    )

    assert embedding_client.embed_single_calls == ["本月销售额是多少"]
    assert len(collection.query_calls) == 1

    query_payload = collection.query_calls[0]
    assert query_payload["query_embeddings"] == [[0.1, 0.2, 0.3]]
    assert "query_texts" not in query_payload
    assert query_payload["where"] == {
        "$and": [
            {"workspace_id": {"$eq": "ws-1"}},
            {"is_active": {"$eq": True}},
        ]
    }
    assert matches == [(example, 0.9)]


@pytest.mark.asyncio
async def test_sync_all_sql_examples_to_vector_marks_scope_pending_before_reset(monkeypatch):
    examples = [
        SqlExample(
            id=1,
            question="查询本月销量",
            sql="select 1",
            workspace_id="ws-1",
            created_by="tester",
        ),
        SqlExample(
            id=2,
            question="查询上月销量",
            sql="select 2",
            workspace_id="ws-1",
            created_by="tester",
        ),
    ]
    call_log = []
    upsert_results = iter([True, False])

    monkeypatch.setattr(module, "SQL_EXAMPLES_SYNC_BATCH_SIZE", 1)

    async def fake_update_scope(*, status, workspace_id=None):
        call_log.append(("scope", status, workspace_id))

    async def fake_clear(workspace_id=None):
        call_log.append(("clear", workspace_id))

    async def fake_load(workspace_id=None):
        assert workspace_id == "ws-1"
        return examples

    async def fake_upsert(payloads):
        payload_list = list(payloads)
        call_log.append(("upsert", payload_list[0]["metadata"]["id"]))
        return next(upsert_results)

    async def fake_update(example_ids, status):
        call_log.append(("ids", list(example_ids), status))

    monkeypatch.setattr(module, "_update_sync_status_for_scope", fake_update_scope)
    monkeypatch.setattr(module, "clear_sql_example_embeddings", fake_clear)
    monkeypatch.setattr(module, "_load_examples_for_sync", fake_load)
    monkeypatch.setattr(module, "_upsert_sql_example_records", fake_upsert)
    monkeypatch.setattr(module, "_update_sync_status", fake_update)

    summary = await module.sync_all_sql_examples_to_vector(workspace_id="ws-1", reset=True)

    assert summary == {
        "workspace_id": "ws-1",
        "total": 2,
        "synced": 1,
        "failed": 1,
        "reset": True,
        "collection": module.SQL_EXAMPLES_COLLECTION,
    }
    assert call_log == [
        ("scope", "pending_update", "ws-1"),
        ("clear", "ws-1"),
        ("upsert", "1"),
        ("ids", [1], "synced"),
        ("upsert", "2"),
        ("ids", [2], "pending_update"),
    ]


@pytest.mark.asyncio
async def test_clear_sql_example_embeddings_ignores_missing_collection(monkeypatch):
    client = DummyChromaClient(error=NotFoundError("Collection missing"))

    monkeypatch.setattr(module, "_get_chroma_client", lambda: client)

    await module.clear_sql_example_embeddings()

    assert client.delete_collection_calls == [module.SQL_EXAMPLES_COLLECTION]


@pytest.mark.asyncio
async def test_clear_sql_example_embeddings_raises_unexpected_delete_errors(monkeypatch):
    client = DummyChromaClient(error=RuntimeError("disk error"))

    monkeypatch.setattr(module, "_get_chroma_client", lambda: client)

    with pytest.raises(RuntimeError, match="disk error"):
        await module.clear_sql_example_embeddings()
