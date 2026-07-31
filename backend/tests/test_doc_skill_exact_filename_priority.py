from types import SimpleNamespace

import pytest

from app.models.common.context import UserContext
from app.models.common.execution import DocumentChunk
from app.skills.doc_skill import DocSkill
from app.supervisor.nodes.executor import execute_worker_task
from app.tools.base import WorkerResult


class _FakeRewriter:
    def __init__(self):
        self.calls = []

    async def rewrite(self, query, *, original_query=None, **_kwargs):
        self.calls.append((query, original_query))
        return ["打印机历史报价", query, original_query]


class _FakeRetriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self.queries = None

    async def search(self, *, queries, **_kwargs):
        self.queries = queries
        return list(self.chunks)


class _FakeReranker:
    async def rerank(self, _query, candidates, _top_k):
        return list(candidates)


@pytest.mark.asyncio
async def test_query_knowledge_base_merges_filename_hits_even_with_semantic_candidates(monkeypatch):
    raw_query = "杭州泰泽办公设备有限公司提供的打印机历史价格是多少？"
    semantic_other = DocumentChunk(
        content="浙江欣赞打印机报价 3600 元",
        source_file="浙江欣赞.pdf",
        chunk_id="other-1",
        score=0.82,
        rerank_score=0.82,
        metadata={"file_id": "other-file", "source_file": "浙江欣赞.pdf"},
    )
    filename_hit = DocumentChunk(
        content="大型打印机 3700 元/台；小型桌面打印机 290 元/台",
        source_file="24年-1-杭州泰泽办公设备有限公司.pdf",
        chunk_id="target-1",
        score=0.94,
        rerank_score=0.94,
        metadata={
            "file_id": "target-file",
            "source_file": "24年-1-杭州泰泽办公设备有限公司.pdf",
            "exact_file_name_match": True,
        },
    )
    rewriter = _FakeRewriter()
    retriever = _FakeRetriever([semantic_other])
    skill = DocSkill.__new__(DocSkill)
    skill._query_rewriter = rewriter
    skill._retriever = retriever
    skill._reranker = _FakeReranker()
    skill._context_expander = None
    skill.validate_user_context = lambda _ctx: True

    async def fake_exact_search(**_kwargs):
        return [filename_hit]

    skill._exact_term_fallback_search = fake_exact_search
    monkeypatch.setattr(
        "app.skills.doc_skill.get_settings",
        lambda: SimpleNamespace(
            rag=SimpleNamespace(
                merged_top_n=20,
                rerank_score_threshold=0.1,
                enable_context_expansion=False,
                image_score_threshold=0.5,
            )
        ),
    )

    chunks = await skill.query_knowledge_base(
        query="查询打印机价格",
        original_query=raw_query,
        user_context=UserContext(
            user_id="sport",
            workspace_id="workspace-1",
            role="admin",
            capabilities=["*"],
        ),
        top_k=5,
        include_images=False,
    )

    assert [chunk.chunk_id for chunk in chunks] == ["target-1"]
    assert rewriter.calls == [("查询打印机价格", raw_query)]
    assert raw_query in retriever.queries


@pytest.mark.asyncio
async def test_executor_passes_current_user_query_to_doc_worker(monkeypatch):
    captured = {}

    class _FakeTool:
        async def ainvoke(self, args):
            captured.update(args)
            return WorkerResult(
                output="知识库结果",
                quality_signal={"verdict": "pass", "reason_code": "rag_ok"},
                meta={"chunks_used": 1, "stop_reason": "rag_ok"},
            )

    monkeypatch.setattr("app.tools.registry.get_tool", lambda _worker: _FakeTool())

    await execute_worker_task(
        worker="doc_worker",
        description="查询打印机历史价格",
        user_context={"user_id": "sport", "workspace_id": "workspace-1"},
        user_query="杭州泰泽办公设备有限公司提供的打印机历史价格是多少？",
    )

    assert captured["original_query"] == "杭州泰泽办公设备有限公司提供的打印机历史价格是多少？"
