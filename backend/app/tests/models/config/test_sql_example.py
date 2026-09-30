from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.config import sql_example as module
from app.models.config import sql_example_embeddings as embeddings_module


class DummyScalarResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class DummySession:
    def __init__(self, owned_ids, deleted_count):
        self.owned_ids = list(owned_ids)
        self.deleted_count = deleted_count
        self.execute_calls = []

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        if len(self.execute_calls) == 1:
            return DummyScalarResult(self.owned_ids)
        return SimpleNamespace(rowcount=self.deleted_count)


class DummySessionScope:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DummyDBManager:
    def __init__(self, session):
        self.session = session

    def session_scope(self):
        return DummySessionScope(self.session)


@pytest.mark.asyncio
async def test_batch_delete_sql_examples_only_deletes_owned_vectors(monkeypatch):
    session = DummySession(owned_ids=[1, 3], deleted_count=2)
    deleted_vector_ids = []

    async def fake_batch_delete(example_ids):
        deleted_vector_ids.append(list(example_ids))
        return True

    monkeypatch.setattr(module, "get_async_db_manager", lambda: DummyDBManager(session))
    monkeypatch.setattr(
        embeddings_module,
        "batch_delete_sql_example_embeddings",
        fake_batch_delete,
    )

    deleted_count = await module.batch_delete_sql_examples_async(
        example_ids=[1, 2, 3],
        workspace_id="ws-1",
        owner_id="tester",
    )

    assert deleted_count == 2
    assert deleted_vector_ids == [[1, 3]]
    assert len(session.execute_calls) == 2


@pytest.mark.asyncio
async def test_batch_delete_sql_examples_raises_when_vector_delete_fails(monkeypatch):
    session = DummySession(owned_ids=[1, 3], deleted_count=2)
    deleted_vector_ids = []

    async def fake_batch_delete(example_ids):
        deleted_vector_ids.append(list(example_ids))
        return False

    monkeypatch.setattr(module, "get_async_db_manager", lambda: DummyDBManager(session))
    monkeypatch.setattr(
        embeddings_module,
        "batch_delete_sql_example_embeddings",
        fake_batch_delete,
    )

    with pytest.raises(RuntimeError, match="批量删除 SQL 示例向量失败"):
        await module.batch_delete_sql_examples_async(
            example_ids=[1, 2, 3],
            workspace_id="ws-1",
            owner_id="tester",
        )

    assert deleted_vector_ids == [[1, 3]]
    assert len(session.execute_calls) == 2
