import asyncio
from types import SimpleNamespace
import sys
from pathlib import Path
from contextlib import asynccontextmanager

import bm25s
import pytest
from cachetools import LRUCache, TTLCache

sys.path.insert(0, ".")

import app.core.rag.hybrid_retriever as hybrid_module
import app.skills.doc_skill as doc_skill_module
import app.scripts.rebuild_all_workspaces as rebuild_module
from app.core.rag.hybrid_retriever import BM25Index, HybridRetriever, RetrievalCandidate
from app.core.rag import tokenization
from app.core.rag.tokenization import tokenize_mixed_text
from app.models.common.context import UserContext
from app.skills.doc_skill import DocSkill


def _build_retriever() -> HybridRetriever:
    retriever = object.__new__(HybridRetriever)
    retriever.rag_settings = SimpleNamespace(
        hybrid_dense_weight=0.5,
        hybrid_sparse_weight=0.5,
        hybrid_sparse_enabled=True,
        hybrid_file_cap_per_source=4,
        hybrid_file_cap_merged=2,
        hybrid_merge_pool_multiplier=2,
        sparse_top_n=10,
        bm25_use_jieba=True,
        bm25_index_version="test",
        bm25_rebuild_debounce_seconds=0.0,
        bm25_query_build_debounce_seconds=0.0,
        bm25_build_stale_seconds=60.0,
        bm25_load_mmap=False,
        soft_deleted_cache_ttl_seconds=30,
        soft_deleted_cache_maxsize=10,
    )
    retriever._bm25_cache = LRUCache(maxsize=10)
    retriever._soft_deleted_cache = TTLCache(maxsize=10, ttl=30)
    retriever._dept_tree_cache = LRUCache(maxsize=10)
    retriever._collection = None
    retriever._embedding_client = None
    retriever._bm25_storage_root = Path("tmp_bm25_tests")
    return retriever


def _candidate(chunk_id: str, file_id: str, score: float, source: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        content=f"content_{chunk_id}",
        score=score,
        source=source,
        metadata={"file_id": file_id, "source_file": f"{file_id}.txt"},
    )


def test_tokenize_mixed_text_keeps_chinese_and_english():
    tokens = tokenize_mixed_text("设备预测维护是什么? predictive maintenance 2026", use_jieba=True)
    assert tokens
    assert "predictive" in tokens
    assert "maintenance" in tokens
    assert any("设备" in token or "预测" in token or "维护" in token for token in tokens)


def test_tokenize_mixed_text_fallback_without_jieba(monkeypatch):
    monkeypatch.setattr(tokenization, "jieba", None)
    tokens = tokenize_mixed_text("设备预测维护是什么", use_jieba=True)
    assert tokens
    assert "预测" in tokens
    assert "维护" in tokens


def test_doc_skill_exact_fallback_terms_expand_hyphenated_phrase():
    terms = DocSkill._build_exact_fallback_terms("请你介绍Supervisor-Worker多智能体架构")

    assert "Supervisor-Worker" in terms
    assert "Supervisor Worker" in terms
    assert "SupervisorWorker" in terms


def test_doc_skill_exact_fallback_terms_expand_chinese_engineering_terms():
    terms = DocSkill._build_exact_fallback_terms("请你分析什么样的点焊连接受力方式是合理的")

    assert "点焊" in terms
    assert "连接" in terms
    assert "受力" in terms


def test_doc_skill_exact_fallback_terms_expand_maintenance_panel_terms():
    terms = DocSkill._build_exact_fallback_terms("压片机设备操作面板的清扫基准")

    assert "压片机" in terms
    assert "设备操作面板" in terms
    assert "操作面板" in terms
    assert "清扫基准" in terms
    assert "设备操作面板清扫基准" in terms


def test_doc_skill_exact_fallback_terms_expand_chinese_question_and_aliases():
    terms = DocSkill._build_exact_fallback_terms("新建需求单方式有哪些")

    assert "新建需求单方式" in terms
    assert "需求单" in terms
    assert "新建路径" in terms


@pytest.mark.asyncio
async def test_doc_skill_exact_fallback_finds_supervisor_worker_chunk():
    class _FakeCollection:
        def get(self, **_kwargs):
            return {
                "ids": ["chunk-1"],
                "documents": [
                    "DeluData 采用 Supervisor-Worker 多智能体架构，由 [[supervisor]] 负责理解用户意图、拆解任务。"
                ],
                "metadatas": [
                    {
                        "workspace_id": "ws1",
                        "file_id": "file-1",
                        "source_file": "02-architecture.md",
                        "visibility": "public",
                        "type": "text",
                    }
                ],
            }

    skill = object.__new__(DocSkill)
    skill._collection = _FakeCollection()

    ctx = UserContext(user_id="u1", workspace_id="ws1", role="admin", allowed_tables=["*"])
    chunks = await skill._exact_term_fallback_search(
        query="请你介绍Supervisor-Worker多智能体架构",
        original_query=None,
        user_context=ctx,
        top_k=5,
        include_images=True,
        file_ids=None,
        visibilities=None,
        dept_ids=None,
    )

    assert len(chunks) == 1
    assert chunks[0].source_file == "02-architecture.md"
    assert "Supervisor-Worker" in chunks[0].content
    assert chunks[0].metadata["exact_fallback_terms"]


@pytest.mark.asyncio
async def test_doc_skill_exact_fallback_ranks_specific_maintenance_chunk_first():
    class _FakeCollection:
        def get(self, **_kwargs):
            return {
                "ids": ["chunk-general", "chunk-panel"],
                "documents": [
                    "自主保全基准书 设备名称 全自动双出料高速压片机 清扫 基准",
                    "清扫 | 2 | 设备操作面板 | 表面无油污、无灰尘、无杂质（手摸） | 擦拭 | 毛巾、抹布 | 1分钟 | √ | 操作者",
                ],
                "metadatas": [
                    {
                        "workspace_id": "ws1",
                        "file_id": "file-1",
                        "source_file": "tablet-maintenance.docx",
                        "visibility": "public",
                        "type": "text",
                    },
                    {
                        "workspace_id": "ws1",
                        "file_id": "file-1",
                        "source_file": "tablet-maintenance.docx",
                        "visibility": "public",
                        "type": "text",
                    },
                ],
            }

    skill = object.__new__(DocSkill)
    skill._collection = _FakeCollection()

    ctx = UserContext(user_id="u1", workspace_id="ws1", role="admin", allowed_tables=["*"])
    chunks = await skill._exact_term_fallback_search(
        query="压片机设备操作面板的清扫基准",
        original_query=None,
        user_context=ctx,
        top_k=5,
        include_images=True,
        file_ids=None,
        visibilities=None,
        dept_ids=None,
    )

    assert len(chunks) == 2
    assert chunks[0].chunk_id == "chunk-panel"
    assert "设备操作面板" in chunks[0].content


@pytest.mark.asyncio
async def test_doc_skill_exact_fallback_prefers_answer_body_over_toc():
    class _FakeCollection:
        def get(self, **_kwargs):
            return {
                "ids": ["chunk-toc", "chunk-answer"],
                "documents": [
                    "2.2.1 新建需求单方式 6\n2.2.2 填写一般采购需求 8",
                    (
                        "新建路径一：进入采购管理门户，通过快捷链接进入一般采购需求新建页面；\n"
                        "新建路径二：进入采购需求列表，点击新建并选择一般采购需求单。"
                    ),
                ],
                "metadatas": [
                    {
                        "workspace_id": "ws1",
                        "file_id": "file-1",
                        "source_file": "purchase-manual.docx",
                        "visibility": "public",
                        "type": "text",
                    },
                    {
                        "workspace_id": "ws1",
                        "file_id": "file-1",
                        "source_file": "purchase-manual.docx",
                        "visibility": "public",
                        "type": "text",
                    },
                ],
            }

    skill = object.__new__(DocSkill)
    skill._collection = _FakeCollection()
    chunks = await skill._exact_term_fallback_search(
        query="新建需求单方式有哪些",
        original_query=None,
        user_context=UserContext(user_id="u1", workspace_id="ws1"),
        top_k=5,
        include_images=False,
        file_ids=None,
        visibilities=None,
        dept_ids=None,
    )

    assert chunks
    assert chunks[0].chunk_id == "chunk-answer"
    assert "新建路径二" in chunks[0].content
    assert chunks[0].score > chunks[1].score


def test_merge_dense_only_when_sparse_degenerate():
    retriever = _build_retriever()
    dense = [
        _candidate("dense_1", "file_a", 0.9, "dense"),
        _candidate("dense_2", "file_b", 0.8, "dense"),
    ]
    sparse = [_candidate("sparse_only", "file_c", 0.7, "sparse")]

    merged = retriever._merge_results(dense, sparse, top_n=2, sparse_degenerate=True)
    merged_ids = [item.chunk_id for item in merged]

    assert merged_ids == ["dense_1", "dense_2"]


def test_file_cap_limits_single_file_domination():
    retriever = _build_retriever()
    candidates = [
        _candidate("a1", "file_a", 1.0, "dense"),
        _candidate("a2", "file_a", 0.9, "dense"),
        _candidate("a3", "file_a", 0.8, "dense"),
        _candidate("b1", "file_b", 0.7, "dense"),
    ]

    capped = retriever._apply_file_cap(candidates, max_per_file=2)
    file_a_count = len([item for item in capped if item.metadata.get("file_id") == "file_a"])

    assert len(capped) == 3
    assert file_a_count == 2


def test_scope_filter_treats_workspace_visibility_as_dept():
    retriever = _build_retriever()
    workspace_candidate = _candidate("legacy", "file_a", 1.0, "dense")
    workspace_candidate.metadata.update({"visibility": "workspace", "dept_id": "10"})
    other_dept_candidate = _candidate("other", "file_b", 0.9, "dense")
    other_dept_candidate.metadata.update({"visibility": "workspace", "dept_id": "11"})

    filtered = retriever._apply_scope_filters(
        [workspace_candidate, other_dept_candidate],
        visibilities=["dept"],
        dept_ids=["10"],
    )

    assert [item.chunk_id for item in filtered] == ["legacy"]


@pytest.mark.asyncio
async def test_sparse_search_marks_degenerate_when_scores_all_zero():
    class StubBM25:
        def retrieve(self, query_tokens, k):
            return [[0]], [[0.0]]

    retriever = _build_retriever()

    async def _fake_index(_workspace_id: str):
        return BM25Index(
            retriever=StubBM25(),
            documents=[{"chunk_id": "chunk_0", "content": "预测性维护", "metadata": {"file_id": "f1"}}],
            workspace_id="ws1",
            cache_key="ws1:test",
        )

    retriever._get_or_build_bm25_index = _fake_index  # type: ignore[assignment]

    user_context = UserContext(
        user_id="u1",
        workspace_id="ws1",
        role="admin",
        allowed_tables=["*"],
    )
    result = await retriever._sparse_search(["设备预测维护是什么"], user_context)

    assert result.sparse_degenerate is True
    assert result.candidates == []


@pytest.mark.asyncio
async def test_sparse_search_keeps_legacy_numeric_index_results():
    class StubBM25:
        def retrieve(self, query_tokens, k, **_kwargs):
            return [[0]], [[2.5]]

    retriever = _build_retriever()

    async def _fake_index(_workspace_id: str):
        return BM25Index(
            retriever=StubBM25(),
            documents=[
                {
                    "chunk_id": "chunk-legacy-index",
                    "content": "新建路径一与新建路径二",
                    "metadata": {"file_id": "f1", "visibility": "public"},
                }
            ],
            workspace_id="ws1",
            cache_key="ws1:test",
        )

    retriever._get_or_build_bm25_index = _fake_index  # type: ignore[assignment]
    result = await retriever._sparse_search(
        ["新建需求单方式有哪些"],
        UserContext(user_id="u1", workspace_id="ws1"),
    )

    assert result.sparse_degenerate is False
    assert [item.chunk_id for item in result.candidates] == ["chunk-legacy-index"]


@pytest.mark.asyncio
async def test_sparse_search_marks_degenerate_when_hits_are_malformed(caplog):
    class StubBM25:
        def retrieve(self, query_tokens, k, **_kwargs):
            return [[object()]], [[1.0]]

    retriever = _build_retriever()

    async def _fake_index(_workspace_id: str):
        return BM25Index(
            retriever=StubBM25(),
            documents=[{"chunk_id": "valid-but-not-returned", "content": "设备维护", "metadata": {}}],
            workspace_id="ws1",
            cache_key="ws1:test",
        )

    retriever._get_or_build_bm25_index = _fake_index  # type: ignore[assignment]
    result = await retriever._sparse_search(
        ["设备预测维护是什么"],
        UserContext(user_id="u1", workspace_id="ws1"),
    )

    assert result.sparse_degenerate is True
    assert result.candidates == []
    assert "ignored malformed sparse hits count=1" in caplog.text


@pytest.mark.asyncio
async def test_search_returns_sparse_candidate_from_loaded_bm25_corpus(tmp_path):
    """A persisted bm25s corpus must remain searchable through HybridRetriever.search."""

    class _EmptyDenseCollection:
        def query(self, **_kwargs):
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    class _EmbeddingClient:
        async def embed_single(self, _query):
            return [0.0, 0.0]

    retriever = _build_retriever()
    retriever.rag_settings.dense_top_n = 10
    retriever.rag_settings.retrieval_exclude_soft_deleted = False
    retriever._bm25_storage_root = tmp_path / "bm25"
    retriever._collection = _EmptyDenseCollection()
    retriever._embedding_client = _EmbeddingClient()

    workspace_id = "ws_sparse_corpus"
    build_id = "dv1_sparse_corpus"
    build_dir = retriever._get_bm25_build_dir(workspace_id, build_id)
    build_dir.parent.mkdir(parents=True, exist_ok=True)
    disk_retriever = bm25s.BM25()
    disk_retriever.index([["新建", "需求", "单", "方式"]], show_progress=False, leave_progress=False)
    disk_retriever.save(
        str(build_dir),
        corpus=[
            {
                "chunk_id": "chunk-demand-paths",
                "content": "新建路径一：快捷入口。新建路径二：采购需求列表。",
                "metadata": {
                    "workspace_id": workspace_id,
                    "file_id": "file-demand-manual",
                    "source_file": "purchase-manual.docx",
                    "visibility": "public",
                },
            }
        ],
    )
    retriever._write_bm25_meta_unlocked(
        hybrid_module.BM25LifecycleMeta(
            workspace_id=workspace_id,
            index_version="test",
            state=hybrid_module.BM25_STATE_READY,
            data_version=1,
            built_data_version=1,
            ready_build_id=build_id,
        )
    )

    results = await retriever.search(
        ["新建需求单方式有哪些"],
        UserContext(user_id="u1", workspace_id=workspace_id),
        top_n=5,
        include_images=False,
    )

    assert [item.chunk_id for item in results] == ["chunk-demand-paths"]
    assert "新建路径二" in results[0].content


@pytest.mark.asyncio
async def test_soft_deleted_ids_use_ttl_cache(monkeypatch):
    retriever = _build_retriever()
    call_count = {"execute": 0}

    class _FakeScalarResult:
        def all(self):
            return ["f_soft_1"]

    class _FakeExecResult:
        def scalars(self):
            return _FakeScalarResult()

    class _FakeSession:
        async def execute(self, _stmt):
            call_count["execute"] += 1
            return _FakeExecResult()

    @asynccontextmanager
    async def _fake_db_context():
        yield _FakeSession()

    monkeypatch.setattr(hybrid_module, "get_async_db_context", _fake_db_context)

    first = await retriever._get_soft_deleted_file_ids("ws1")
    second = await retriever._get_soft_deleted_file_ids("ws1")

    assert first == {"f_soft_1"}
    assert second == {"f_soft_1"}
    assert call_count["execute"] == 1


@pytest.mark.asyncio
async def test_search_applies_merged_cap_and_final_top_n(monkeypatch):
    retriever = _build_retriever()

    dense = [
        _candidate("a1", "file_a", 0.99, "dense"),
        _candidate("a2", "file_a", 0.98, "dense"),
        _candidate("a3", "file_a", 0.97, "dense"),
        _candidate("b1", "file_b", 0.96, "dense"),
    ]
    sparse = [
        _candidate("a4", "file_a", 0.95, "sparse"),
        _candidate("a5", "file_a", 0.94, "sparse"),
        _candidate("c1", "file_c", 0.93, "sparse"),
    ]

    async def _fake_build_permission_filter(_ctx):
        return {}, None

    async def _fake_dense_search(_queries, _where):
        return dense

    async def _fake_sparse_search(_queries, _ctx):
        return hybrid_module.SparseSearchResult(candidates=sparse, sparse_degenerate=False)

    async def _fake_get_deleted(_workspace):
        return set()

    monkeypatch.setattr(retriever, "_build_permission_filter", _fake_build_permission_filter)
    monkeypatch.setattr(retriever, "_dense_search", _fake_dense_search)
    monkeypatch.setattr(retriever, "_sparse_search", _fake_sparse_search)
    monkeypatch.setattr(retriever, "_get_soft_deleted_file_ids", _fake_get_deleted)
    monkeypatch.setattr(retriever, "_filter_by_permission", lambda items, *_args, **_kwargs: items)
    monkeypatch.setattr(retriever, "_apply_scope_filters", lambda items, *_args, **_kwargs: items)
    monkeypatch.setattr(retriever, "_to_document_chunks", lambda items: items)

    ctx = UserContext(user_id="u1", workspace_id="ws1", role="admin", allowed_tables=["*"])
    results = await retriever.search(["设备预测维护是什么"], ctx, top_n=3)

    assert len(results) <= 3
    file_a_count = len([item for item in results if item.metadata.get("file_id") == "file_a"])
    assert file_a_count <= retriever.rag_settings.hybrid_file_cap_merged


@pytest.mark.asyncio
async def test_get_or_build_bm25_index_loads_ready_index_from_disk(tmp_path):
    retriever = _build_retriever()
    retriever._bm25_storage_root = tmp_path / "bm25"
    workspace_id = "ws_ready"

    build_id = "dv1_ready"
    build_dir = retriever._get_bm25_build_dir(workspace_id, build_id)
    build_dir.parent.mkdir(parents=True, exist_ok=True)
    disk_retriever = bm25s.BM25()
    disk_retriever.index([["设备", "预测", "维护"]], show_progress=False, leave_progress=False)
    disk_retriever.save(
        str(build_dir),
        corpus=[{"chunk_id": "chunk_1", "content": "设备预测维护", "metadata": {"file_id": "f1"}}],
    )

    meta = hybrid_module.BM25LifecycleMeta(
        workspace_id=workspace_id,
        index_version="test",
        state=hybrid_module.BM25_STATE_READY,
        data_version=1,
        built_data_version=1,
        ready_build_id=build_id,
    )
    retriever._write_bm25_meta_unlocked(meta)

    loaded = await retriever._get_or_build_bm25_index(workspace_id)

    assert loaded is not None
    assert loaded.build_id == build_id
    assert loaded.documents[0]["chunk_id"] == "chunk_1"


@pytest.mark.asyncio
async def test_get_or_build_bm25_index_uses_stale_ready_while_building(tmp_path):
    retriever = _build_retriever()
    retriever._bm25_storage_root = tmp_path / "bm25"
    workspace_id = "ws_building"

    build_id = "dv1_ready"
    build_dir = retriever._get_bm25_build_dir(workspace_id, build_id)
    build_dir.parent.mkdir(parents=True, exist_ok=True)
    disk_retriever = bm25s.BM25()
    disk_retriever.index([["维护", "策略"]], show_progress=False, leave_progress=False)
    disk_retriever.save(
        str(build_dir),
        corpus=[{"chunk_id": "chunk_2", "content": "维护策略", "metadata": {"file_id": "f2"}}],
    )

    meta = hybrid_module.BM25LifecycleMeta(
        workspace_id=workspace_id,
        index_version="test",
        state=hybrid_module.BM25_STATE_BUILDING,
        data_version=2,
        built_data_version=1,
        ready_build_id=build_id,
        sparse_disabled=False,
    )
    retriever._write_bm25_meta_unlocked(meta)

    loaded = await retriever._get_or_build_bm25_index(workspace_id)

    assert loaded is not None
    assert loaded.documents[0]["chunk_id"] == "chunk_2"


@pytest.mark.asyncio
async def test_rebuild_bm25_index_now_publishes_ready_index(tmp_path):
    retriever = _build_retriever()
    retriever._bm25_storage_root = tmp_path / "bm25"
    workspace_id = "ws_publish"

    class _FakeCollection:
        def get(self, where, include):
            assert where == {"workspace_id": {"$eq": workspace_id}}
            assert include == ["documents", "metadatas"]
            return {
                "ids": ["chunk_3"],
                "documents": ["预测性维护说明"],
                "metadatas": [{"file_id": "f3"}],
            }

    retriever._collection = _FakeCollection()

    version = await retriever.mark_bm25_dirty(workspace_id, disable_sparse=True)
    built = await retriever.rebuild_bm25_index_now(workspace_id)
    meta = await retriever._read_bm25_meta(workspace_id)
    loaded = await retriever._get_or_build_bm25_index(workspace_id)

    assert version == 1
    assert built is True
    assert meta.state == hybrid_module.BM25_STATE_READY
    assert meta.data_version == 1
    assert meta.built_data_version == 1
    assert meta.sparse_disabled is False
    assert meta.ready_build_id
    assert loaded is not None
    assert loaded.documents[0]["chunk_id"] == "chunk_3"


@pytest.mark.asyncio
async def test_rebuild_bm25_index_now_rebuilds_when_ready_build_is_broken(tmp_path):
    retriever = _build_retriever()
    retriever._bm25_storage_root = tmp_path / "bm25"
    workspace_id = "ws_broken"
    stale_build_id = "dv1_broken"
    stale_build_dir = retriever._get_bm25_build_dir(workspace_id, stale_build_id)
    stale_build_dir.mkdir(parents=True, exist_ok=True)
    (stale_build_dir / "junk.txt").write_text("broken", encoding="utf-8")

    meta = hybrid_module.BM25LifecycleMeta(
        workspace_id=workspace_id,
        index_version="test",
        state=hybrid_module.BM25_STATE_READY,
        data_version=1,
        built_data_version=1,
        ready_build_id=stale_build_id,
    )
    retriever._write_bm25_meta_unlocked(meta)

    class _FakeCollection:
        def get(self, where, include):
            assert where == {"workspace_id": {"$eq": workspace_id}}
            assert include == ["documents", "metadatas"]
            return {
                "ids": ["chunk_new"],
                "documents": ["新的预测维护内容"],
                "metadatas": [{"file_id": "f_new"}],
            }

    retriever._collection = _FakeCollection()

    built = await retriever.rebuild_bm25_index_now(workspace_id)
    meta_after = await retriever._read_bm25_meta(workspace_id)
    loaded = await retriever._get_or_build_bm25_index(workspace_id)

    assert built is True
    assert meta_after.state == hybrid_module.BM25_STATE_READY
    assert meta_after.ready_build_id != stale_build_id
    assert not stale_build_dir.exists()
    assert loaded is not None
    assert loaded.documents[0]["chunk_id"] == "chunk_new"


@pytest.mark.asyncio
async def test_get_or_build_bm25_index_recovers_stale_building_and_reschedules(tmp_path, monkeypatch):
    retriever = _build_retriever()
    retriever._bm25_storage_root = tmp_path / "bm25"
    workspace_id = "ws_stale_building"

    meta = hybrid_module.BM25LifecycleMeta(
        workspace_id=workspace_id,
        index_version="test",
        state=hybrid_module.BM25_STATE_BUILDING,
        data_version=2,
        built_data_version=0,
        ready_build_id=None,
        last_build_started_at="2020-01-01T00:00:00+00:00",
    )
    retriever._write_bm25_meta_unlocked(meta)

    scheduled = {}

    def _fake_schedule(ws_id, **kwargs):
        scheduled["workspace_id"] = ws_id
        scheduled["kwargs"] = kwargs

    monkeypatch.setattr(retriever, "schedule_bm25_rebuild", _fake_schedule)

    loaded = await retriever._get_or_build_bm25_index(workspace_id)
    meta_after = await retriever._read_bm25_meta(workspace_id)

    assert loaded is None
    assert meta_after.state == hybrid_module.BM25_STATE_DIRTY
    assert meta_after.last_error == hybrid_module.BM25_ERROR_STALE_BUILDING
    assert scheduled["workspace_id"] == workspace_id
    assert scheduled["kwargs"]["reason"] == "query_lazy_build"


@pytest.mark.asyncio
async def test_rebuild_bm25_index_now_keeps_last_ready_build_on_empty_snapshot(tmp_path):
    retriever = _build_retriever()
    retriever._bm25_storage_root = tmp_path / "bm25"
    workspace_id = "ws_keep_ready"
    ready_build_id = "dv1_ready"
    ready_build_dir = retriever._get_bm25_build_dir(workspace_id, ready_build_id)
    ready_build_dir.parent.mkdir(parents=True, exist_ok=True)

    disk_retriever = bm25s.BM25()
    disk_retriever.index([["预测", "维护"]], show_progress=False, leave_progress=False)
    disk_retriever.save(
        str(ready_build_dir),
        corpus=[{"chunk_id": "chunk_old", "content": "旧内容", "metadata": {"file_id": "f_old"}}],
    )

    meta = hybrid_module.BM25LifecycleMeta(
        workspace_id=workspace_id,
        index_version="test",
        state=hybrid_module.BM25_STATE_READY,
        data_version=1,
        built_data_version=1,
        ready_build_id=ready_build_id,
    )
    retriever._write_bm25_meta_unlocked(meta)

    class _EmptyCollection:
        def get(self, where, include):
            assert where == {"workspace_id": {"$eq": workspace_id}}
            assert include == ["documents", "metadatas"]
            return {"ids": [], "documents": [], "metadatas": []}

    retriever._collection = _EmptyCollection()

    await retriever.mark_bm25_dirty(workspace_id, disable_sparse=False)
    built = await retriever.rebuild_bm25_index_now(workspace_id)
    meta_after = await retriever._read_bm25_meta(workspace_id)
    loaded = await retriever._load_bm25_index_from_disk(workspace_id, meta_after)

    assert built is False
    assert meta_after.state == hybrid_module.BM25_STATE_DIRTY
    assert meta_after.ready_build_id == ready_build_id
    assert meta_after.last_error == hybrid_module.BM25_ERROR_EMPTY_WORKSPACE
    assert loaded is not None
    assert loaded.documents[0]["chunk_id"] == "chunk_old"


@pytest.mark.asyncio
async def test_doc_skill_ingest_document_does_not_fail_when_bm25_notify_raises(tmp_path, monkeypatch):
    doc_path = tmp_path / "doc.txt"
    doc_path.write_text("hello", encoding="utf-8")

    class _FakeRetriever:
        def __init__(self):
            self.invalidated_workspace = None

        async def notify_bm25_content_change(self, _workspace_id, **_kwargs):
            raise RuntimeError("bm25 notify failed")

        def invalidate_bm25_cache(self, workspace_id):
            self.invalidated_workspace = workspace_id

    class _FakeIngestor:
        async def ingest(self, **_kwargs):
            return {"success": True, "file_id": "f1"}

    @asynccontextmanager
    async def _fake_materialize(file_path, suffix=""):
        assert suffix == ".txt"
        yield str(file_path)

    class _FakeStorageService:
        def materialize(self, file_path, suffix=""):
            return _fake_materialize(file_path, suffix=suffix)

    monkeypatch.setattr(doc_skill_module, "get_storage_service", lambda: _FakeStorageService())
    monkeypatch.setattr(doc_skill_module, "resolve_storage_path", lambda value: str(value))

    skill = object.__new__(DocSkill)
    skill._ingestor = _FakeIngestor()
    skill._retriever = _FakeRetriever()
    skill._collection = None
    skill.validate_user_context = lambda _ctx: True

    ctx = UserContext(user_id="u1", workspace_id="ws1", role="admin", allowed_tables=["*"])
    result = await skill.ingest_document(str(doc_path), ctx)

    assert result["success"] is True
    assert skill._retriever.invalidated_workspace == "ws1"


@pytest.mark.asyncio
async def test_doc_skill_delete_document_does_not_fail_when_bm25_notify_raises():
    deleted_where = {}

    class _FakeCollection:
        def delete(self, where):
            deleted_where["where"] = where

    class _FakeRetriever:
        def __init__(self):
            self.invalidated_workspace = None

        async def notify_bm25_content_change(self, _workspace_id, **_kwargs):
            raise RuntimeError("bm25 notify failed")

        def invalidate_bm25_cache(self, workspace_id):
            self.invalidated_workspace = workspace_id

    skill = object.__new__(DocSkill)
    skill._collection = _FakeCollection()
    skill._retriever = _FakeRetriever()

    ctx = UserContext(user_id="u1", workspace_id="ws1", role="admin", allowed_tables=["*"])
    result = await skill.delete_document("file-1", ctx, raise_on_error=True)

    assert result is True
    assert deleted_where["where"]["$and"][0] == {"file_id": {"$eq": "file-1"}}
    assert skill._retriever.invalidated_workspace == "ws1"


@pytest.mark.asyncio
async def test_rebuild_all_workspaces_runs_single_final_bm25_rebuild_and_reports_failure(tmp_path, monkeypatch):
    source_path = tmp_path / "source.txt"
    source_path.write_text("content", encoding="utf-8")

    class _FakeRetriever:
        def __init__(self):
            self.invalidate_all_called = False
            self.invalidate_calls = []
            self.rebuild_calls = []

        def invalidate_all_bm25_cache(self):
            self.invalidate_all_called = True

        def invalidate_bm25_cache(self, workspace_id):
            self.invalidate_calls.append(workspace_id)

        async def rebuild_bm25_index_now(self, workspace_id, force=False):
            self.rebuild_calls.append((workspace_id, force))
            return False

    class _FakeDocSkill:
        instances = []

        def __init__(self):
            self.retriever = _FakeRetriever()
            self.notify_bm25_flags = []
            _FakeDocSkill.instances.append(self)

        async def ingest_document(self, **kwargs):
            self.notify_bm25_flags.append(kwargs["notify_bm25"])
            return {"success": True}

    async def _fake_load_target_files(_workspace_id):
        return [
            rebuild_module.RebuildFile(
                file_id="f1",
                workspace_id="ws1",
                user_id="u1",
                dept_id=None,
                visibility="dept",
                storage_path=str(source_path),
            )
        ]

    monkeypatch.setattr(rebuild_module, "DocSkill", _FakeDocSkill)
    monkeypatch.setattr(rebuild_module, "_load_target_files", _fake_load_target_files)
    monkeypatch.setattr(rebuild_module, "_load_checkpoint", lambda _path: {"completed": [], "failed": {}, "attempts": {}})
    monkeypatch.setattr(rebuild_module, "_save_checkpoint", lambda _path, _checkpoint: None)

    summary = await rebuild_module.run(
        workspace_id=None,
        checkpoint_path=tmp_path / "checkpoint.json",
        max_retries=1,
        bm25_backfill_only=False,
    )

    skill = _FakeDocSkill.instances[0]
    assert skill.notify_bm25_flags == [False]
    assert skill.retriever.invalidate_all_called is True
    assert skill.retriever.rebuild_calls == [("ws1", True)]
    assert summary["completed"] == 1
    assert summary["failed"] == 0
    assert summary["bm25_failed_workspaces"] == ["ws1"]
    assert summary["bm25_failed_count"] == 1
    assert summary["success"] is False
