from types import SimpleNamespace

import pytest
from requests import ConnectionError as RequestsConnectionError

from app.core.llm.async_embedding import AsyncEmbeddingClient, TextEmbedding


@pytest.mark.asyncio
async def test_embedding_recovers_after_transient_connection_failures(monkeypatch):
    attempts = 0

    def _call(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RequestsConnectionError("temporary DNS failure")
        return SimpleNamespace(
            status_code=200,
            code=None,
            message=None,
            output={"embeddings": [{"text_index": 0, "embedding": [0.1, 0.2]}]},
        )

    monkeypatch.setattr(TextEmbedding, "call", _call)
    monkeypatch.setattr("app.core.llm.async_embedding.asyncio.sleep", lambda _delay: _no_wait())
    client = AsyncEmbeddingClient.__new__(AsyncEmbeddingClient)
    client.model = "test-embedding"
    client.dimensions = 2
    client.max_attempts = 3
    client.retry_backoff_sec = 0.25

    embedding = await client.embed_single("新建需求单方式有哪些")

    assert embedding == [0.1, 0.2]
    assert attempts == 3


@pytest.mark.asyncio
async def test_embedding_does_not_retry_non_retryable_client_error(monkeypatch):
    attempts = 0

    def _call(**_kwargs):
        nonlocal attempts
        attempts += 1
        return SimpleNamespace(
            status_code=400,
            code="InvalidParameter",
            message="bad request",
            output={},
        )

    monkeypatch.setattr(TextEmbedding, "call", _call)
    client = AsyncEmbeddingClient.__new__(AsyncEmbeddingClient)
    client.model = "test-embedding"
    client.dimensions = 2
    client.max_attempts = 3
    client.retry_backoff_sec = 0.25

    with pytest.raises(RuntimeError, match="InvalidParameter"):
        await client.embed_single("invalid request")

    assert attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500])
async def test_embedding_retries_retryable_service_responses(monkeypatch, status_code):
    attempts = 0

    def _call(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return SimpleNamespace(
                status_code=status_code,
                code="ServiceUnavailable",
                message="retry later",
                output={},
            )
        return SimpleNamespace(
            status_code=200,
            code=None,
            message=None,
            output={"embeddings": [{"text_index": 0, "embedding": [0.3, 0.4]}]},
        )

    monkeypatch.setattr(TextEmbedding, "call", _call)
    monkeypatch.setattr("app.core.llm.async_embedding.asyncio.sleep", lambda _delay: _no_wait())
    client = AsyncEmbeddingClient.__new__(AsyncEmbeddingClient)
    client.model = "test-embedding"
    client.dimensions = 2
    client.max_attempts = 3
    client.retry_backoff_sec = 0.25

    assert await client.embed_single("retryable request") == [0.3, 0.4]
    assert attempts == 2


async def _no_wait():
    return None
