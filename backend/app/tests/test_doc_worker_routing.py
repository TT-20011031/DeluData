"""DocWorker 知识路径分支单元测试 (M3.3)。

覆盖：
1. _apply_knowledge_path_to_params 注入逻辑
2. run_doc_task 三分支：rag(默认) / wiki / both
3. Executor 会把 knowledge_path 透传给 doc_worker
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bm25s
import pytest
from requests import ConnectionError as RequestsConnectionError

from app.models.common.execution import DocumentChunk
from app.supervisor.nodes.executor import _apply_knowledge_path_to_params, execute_worker_task


# ============ executor 注入逻辑 ============

class TestApplyKnowledgePathToParams:
    def test_默认_rag_注入(self):
        params = _apply_knowledge_path_to_params({}, "rag")
        assert params["knowledge_path"] == "rag"

    def test_wiki_路径注入(self):
        params = _apply_knowledge_path_to_params({}, "wiki")
        assert params["knowledge_path"] == "wiki"

    def test_both_路径注入(self):
        params = _apply_knowledge_path_to_params({}, "both")
        assert params["knowledge_path"] == "both"

    def test_步骤级显式_path_优先(self):
        # Planner 显式给的 step.params.knowledge_path 不被 KnowledgeRouter 覆盖
        params = _apply_knowledge_path_to_params({"knowledge_path": "wiki"}, "rag")
        assert params["knowledge_path"] == "wiki"

    def test_非法路径不写入(self):
        params = _apply_knowledge_path_to_params({}, "garbage")
        assert "knowledge_path" not in params

    def test_保留其他参数(self):
        params = _apply_knowledge_path_to_params({"top_k": 10, "include_images": False}, "wiki")
        assert params["top_k"] == 10
        assert params["include_images"] is False
        assert params["knowledge_path"] == "wiki"


@pytest.mark.asyncio
async def test_execute_worker_task_透传_knowledge_path(monkeypatch):
    captured: dict = {}

    class _FakeTool:
        async def ainvoke(self, args):
            captured.update(args)
            from app.tools.base import WorkerResult
            return WorkerResult(output="ok", meta={"knowledge_path": args.get("knowledge_path")})

    monkeypatch.setattr("app.tools.registry.get_tool", lambda worker: _FakeTool())

    result = await execute_worker_task(
        worker="doc_worker",
        description="介绍 wiki-first",
        user_context={"user_id": "u-1", "workspace_id": "ws-1"},
        step_params={"knowledge_path": "wiki"},
    )

    assert captured["knowledge_path"] == "wiki"
    assert result["meta"]["knowledge_path"] == "wiki"


# ============ run_doc_task 三分支 ============

def _make_user_context_dict() -> dict:
    return {"user_id": "u-1", "workspace_id": "ws-1"}


def _make_doc_chunk(
    *, content: str = "test", chunk_id: str = "c1", score: float = 0.85,
    file_id: str | None = "f-1", is_wiki: bool = False,
) -> DocumentChunk:
    metadata: dict = {}
    if file_id:
        metadata["file_id"] = file_id
    if is_wiki:
        metadata["kind"] = "wiki_page"
        metadata["slug"] = "test-slug"
    return DocumentChunk(
        content=content,
        source_file="wiki:test-slug" if is_wiki else "test.pdf",
        chunk_id=chunk_id,
        score=score,
        rerank_score=score,
        metadata=metadata,
    )


def _install_mock_skill(monkeypatch, mock_skill: MagicMock) -> None:
    """统一通过 monkeypatch 替换模块级 DocSkill 工厂，规避 mock decorator 顺序敏感性。"""
    from app.skills import doc_skill as doc_skill_module
    monkeypatch.setattr(doc_skill_module, "DocSkill", lambda *a, **kw: mock_skill)


@pytest.mark.asyncio
class TestRunDocTaskRouting:
    async def test_默认_rag_路径调_query_knowledge_base(self, monkeypatch):
        """rag 路径：调 query_knowledge_base，不应调 wiki_navigate。"""
        from app.tools.doc_tool import run_doc_task

        mock_skill = MagicMock()
        mock_skill.query_knowledge_base = AsyncMock(return_value=[
            _make_doc_chunk(content="rag content", chunk_id="c1", score=0.9),
        ])
        mock_skill.wiki_navigate = AsyncMock()
        _install_mock_skill(monkeypatch, mock_skill)

        result = await run_doc_task(
            query="差旅怎么报销",
            user_context=_make_user_context_dict(),
            knowledge_path="rag",
        )

        mock_skill.query_knowledge_base.assert_awaited()
        mock_skill.wiki_navigate.assert_not_called()
        assert result.meta["knowledge_path"] == "rag"
        assert result.meta["wiki_chunks_count"] == 0

    async def test_mcp_ask_top_k_controls_retrieval_and_final_evidence(self, monkeypatch):
        from app.tools.doc_tool import run_doc_task

        captured = {}
        chunks = [
            _make_doc_chunk(
                content=f"证据{i}",
                chunk_id=f"c{i}",
                score=0.9,
                file_id=None,
            )
            for i in range(20)
        ]

        async def fake_query_knowledge_base(*args, **kwargs):
            captured["top_k"] = kwargs["top_k"]
            return chunks

        skill_stub = MagicMock()
        skill_stub.query_knowledge_base = fake_query_knowledge_base
        skill_stub.wiki_navigate = AsyncMock()
        _install_mock_skill(monkeypatch, skill_stub)

        result = await run_doc_task(
            query="打印机历史价格",
            user_context={
                **_make_user_context_dict(),
                "mcp_ask_evidence_top_k": 20,
            },
            knowledge_path="rag",
            doc_scope={"include_images": False},
        )

        assert captured["top_k"] == 20
        assert len(result.meta["final_context"]) == 20

    async def test_mcp_ask_guarantees_explicit_file_with_request_budget(self, monkeypatch):
        from app.tools.doc_tool import run_doc_task

        captured_calls = []
        related_chunk = _make_doc_chunk(
            content="普通相关采购历史 3200 元/台",
            chunk_id="related-1",
            score=0.95,
            file_id="related-file",
        )
        target_chunk = _make_doc_chunk(
            content="明确文件采购记录 3500 元/台",
            chunk_id="target-1",
            score=0.01,
            file_id="target-file",
        )

        async def fake_query_knowledge_base(*args, **kwargs):
            captured_calls.append(kwargs)
            if kwargs.get("file_ids") == ["target-file"]:
                return []
            return [related_chunk]

        skill_stub = MagicMock()
        skill_stub.query_knowledge_base = fake_query_knowledge_base
        skill_stub.load_file_chunks_by_ids = AsyncMock(return_value=[target_chunk])
        skill_stub.wiki_navigate = AsyncMock(return_value=[
            _make_doc_chunk(
                content="Wiki 相关说明",
                chunk_id="wiki-1",
                score=0.90,
                file_id=None,
                is_wiki=True,
            )
        ])
        _install_mock_skill(monkeypatch, skill_stub)
        monkeypatch.setattr(
            "app.tools.doc_tool._ensure_both_context",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("MCP required-file mode must not mutate selection after budgeting")
            ),
        )

        class FakeSession:
            async def execute(self, _stmt):
                return []

        class FakeSessionScope:
            async def __aenter__(self):
                return FakeSession()

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

        class FakeDbManager:
            def session_scope(self):
                return FakeSessionScope()

        monkeypatch.setattr(
            "app.core.db.database.get_async_db_manager",
            lambda: FakeDbManager(),
        )

        result = await run_doc_task(
            query="请查询《采购清单-A.pdf》，并补充其他相似采购历史",
            user_context={
                **_make_user_context_dict(),
                "mcp_ask_evidence_top_k": 20,
                "mcp_ask_evidence_token_budget": 6000,
                "mcp_ask_required_file_ids": ["target-file"],
                "mcp_ask_preserve_related_candidates": True,
            },
            knowledge_path="both",
            doc_scope={"include_images": False},
            deep_search=True,
        )

        assert any(
            call.get("preserve_related_candidates") is True
            and call.get("file_ids") is None
            for call in captured_calls
        )
        assert any(
            call.get("file_ids") == ["target-file"]
            for call in captured_calls
        )
        skill_stub.load_file_chunks_by_ids.assert_awaited_once()
        assert result.meta["evidence_token_budget"] == 6000
        assert result.meta["required_file_evidence_count"] == 1
        final_file_ids = {
            item["file_id"]
            for item in result.meta["final_context"]
            if item["file_id"]
        }
        assert final_file_ids == {"target-file", "related-file"}

    async def test_unmarked_request_keeps_default_final_evidence_cap(self, monkeypatch):
        from app.tools.doc_tool import run_doc_task

        chunks = [
            _make_doc_chunk(
                content=f"证据{i}",
                chunk_id=f"c{i}",
                score=0.9,
                file_id=None,
            )
            for i in range(20)
        ]
        skill_stub = MagicMock()
        skill_stub.query_knowledge_base = AsyncMock(return_value=chunks)
        skill_stub.wiki_navigate = AsyncMock()
        _install_mock_skill(monkeypatch, skill_stub)

        result = await run_doc_task(
            query="打印机历史价格",
            user_context=_make_user_context_dict(),
            knowledge_path="rag",
            doc_scope={"include_images": False},
        )

        assert len(result.meta["final_context"]) == 5
        assert result.meta["evidence_token_budget"] == 4500
        assert result.meta["required_file_ids"] == []
        assert result.meta["preserve_related_candidates"] is False

    async def test_wiki_路径调_wiki_navigate(self, monkeypatch):
        """wiki 路径：调 wiki_navigate，不调 query_knowledge_base。"""
        from app.tools.doc_tool import run_doc_task

        mock_skill = MagicMock()
        mock_skill.query_knowledge_base = AsyncMock()
        mock_skill.wiki_navigate = AsyncMock(return_value=[
            _make_doc_chunk(
                content="# Wiki Title\n正文",
                chunk_id="wiki_page:1",
                score=1.0,
                file_id=None,
                is_wiki=True,
            ),
        ])
        _install_mock_skill(monkeypatch, mock_skill)

        result = await run_doc_task(
            query="差旅报销制度是什么",
            user_context=_make_user_context_dict(),
            knowledge_path="wiki",
        )

        mock_skill.wiki_navigate.assert_awaited_once()
        mock_skill.query_knowledge_base.assert_not_called()
        assert result.meta["knowledge_path"] == "wiki"
        assert result.meta["wiki_chunks_count"] == 1
        assert result.meta["wiki_chunks_used"] >= 1
        assert result.meta["answer_context_source"] == "wiki"
        assert result.meta["final_context"][0]["source"] == "wiki"

    async def test_both_路径并发调用_wiki_在前(self, monkeypatch):
        """both 路径：并发调用，合并时 wiki chunks 在前。

        说明：使用真实 async 函数替代 AsyncMock，避免 AsyncMock + asyncio.gather
        在批量测试中出现的 "Future attached to different loop" 跨 loop 问题。
        """
        from app.tools.doc_tool import run_doc_task

        # 用真实 async 函数避免 AsyncMock 跨 loop 状态问题
        rag_call_count = {"n": 0}
        wiki_call_count = {"n": 0}

        async def fake_query_kb(*args, **kwargs):
            rag_call_count["n"] += 1
            # file_id=None 避免触发 doc_tool 内的 DB session（跨 loop 问题）
            return [_make_doc_chunk(
                content="rag chunk content",
                chunk_id="rag-c1",
                score=1.0,
                file_id=None,
            )]

        async def fake_wiki_navigate(*args, **kwargs):
            wiki_call_count["n"] += 1
            return [_make_doc_chunk(
                content="# Wiki page content",
                chunk_id="wiki_page:1",
                score=1.0,
                file_id=None,
                is_wiki=True,
            )]

        skill_stub = MagicMock()
        skill_stub.query_knowledge_base = fake_query_kb
        skill_stub.wiki_navigate = fake_wiki_navigate
        _install_mock_skill(monkeypatch, skill_stub)

        result = await run_doc_task(
            query="总结差旅制度并列出原文",
            user_context=_make_user_context_dict(),
            knowledge_path="both",
            # 禁用图片证据流程，避免 image_service 单例的跨 loop 问题
            doc_scope={"include_images": False},
        )

        assert wiki_call_count["n"] == 1
        assert rag_call_count["n"] == 1
        assert result.meta["knowledge_path"] == "both"
        assert result.meta["wiki_chunks_count"] == 1
        assert result.meta["wiki_chunks_used"] >= 1
        assert result.meta["rag_chunks_used"] >= 1
        assert result.meta["answer_context_source"] == "both"
        # 输出 output_text 中应同时含 wiki 与 rag 内容
        assert "Wiki page" in result.output
        assert "rag chunk" in result.output
        # wiki chunk 在前
        assert result.output.index("Wiki page") < result.output.index("rag chunk")

    async def test_非法_path_归一化为_rag(self, monkeypatch):
        from app.tools.doc_tool import run_doc_task

        mock_skill = MagicMock()
        mock_skill.query_knowledge_base = AsyncMock(return_value=[])
        mock_skill.wiki_navigate = AsyncMock()
        _install_mock_skill(monkeypatch, mock_skill)

        result = await run_doc_task(
            query="q",
            user_context=_make_user_context_dict(),
            knowledge_path="garbage_value",
        )

        assert result.meta["knowledge_path"] == "rag"
        assert result.meta["final_context"] == []
        assert "no_final_context" in result.meta["issues"]
        mock_skill.wiki_navigate.assert_not_called()

    async def test_None_path_默认_rag(self, monkeypatch):
        from app.tools.doc_tool import run_doc_task

        mock_skill = MagicMock()
        mock_skill.query_knowledge_base = AsyncMock(return_value=[])
        mock_skill.wiki_navigate = AsyncMock()
        _install_mock_skill(monkeypatch, mock_skill)

        result = await run_doc_task(
            query="q",
            user_context=_make_user_context_dict(),
            # 不传 knowledge_path
        )

        assert result.meta["knowledge_path"] == "rag"
        mock_skill.wiki_navigate.assert_not_called()

    async def test_wiki空且dense失败时_bm25仍召回答案(self, monkeypatch, tmp_path):
        from app.core.rag.hybrid_retriever import BM25LifecycleMeta, BM25_STATE_READY, HybridRetriever
        from app.tools.doc_tool import run_doc_task

        class _EmptyDenseCollection:
            def query(self, **_kwargs):
                return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        class _FakeChromaClient:
            def get_or_create_collection(self, **_kwargs):
                return _EmptyDenseCollection()

        class _UnavailableEmbedding:
            async def embed_single(self, _query):
                raise RequestsConnectionError("dense unavailable")

        retriever = HybridRetriever(chroma_client=_FakeChromaClient())
        retriever._bm25_storage_root = tmp_path / "bm25"
        retriever._embedding_client = _UnavailableEmbedding()
        retriever._soft_deleted_cache["soft_deleted:ws-1"] = set()

        build_id = "dv1_purchase_paths"
        build_dir = retriever._get_bm25_build_dir("ws-1", build_id)
        build_dir.parent.mkdir(parents=True, exist_ok=True)
        corpus = [
            {
                "chunk_id": "chunk-purchase-paths",
                "content": (
                    "新建路径一：进入采购管理门户，通过快捷链接进入一般采购需求新建页面；\n"
                    "新建路径二：进入采购需求列表，点击新建并选择一般采购需求单。"
                ),
                "metadata": {
                    "workspace_id": "ws-1",
                    "source_file": "purchase-manual.docx",
                    "visibility": "public",
                    "type": "text",
                },
            }
        ]
        disk_retriever = bm25s.BM25()
        disk_retriever.index(
            [["新建", "路径", "采购", "需求", "单"]],
            show_progress=False,
            leave_progress=False,
        )
        disk_retriever.save(str(build_dir), corpus=corpus)
        retriever._write_bm25_meta_unlocked(
            BM25LifecycleMeta(
                workspace_id="ws-1",
                index_version=retriever._bm25_index_version(),
                state=BM25_STATE_READY,
                data_version=1,
                built_data_version=1,
                ready_build_id=build_id,
            )
        )

        async def _query_knowledge_base(query, user_context, **kwargs):
            queries = [query, "新建路径 采购需求单"]
            return await retriever.search(
                queries=queries,
                user_context=user_context,
                top_n=kwargs.get("top_k", 5),
                include_images=False,
            )

        skill_stub = MagicMock()
        skill_stub.wiki_navigate = AsyncMock(return_value=[])
        skill_stub.query_knowledge_base = _query_knowledge_base
        _install_mock_skill(monkeypatch, skill_stub)

        result = await run_doc_task(
            query="新建需求单方式有哪些",
            user_context=_make_user_context_dict(),
            knowledge_path="wiki",
            doc_scope={"include_images": False},
        )

        assert result.meta["wiki_chunks_count"] == 0
        assert result.meta["rag_chunks_used"] >= 1
        assert "新建路径一" in result.output
        assert "新建路径二" in result.output


# ============ Planner Prompt 注入 ============

class TestPlannerKnowledgePathHint:
    def test_rag_默认_无_hint(self):
        from app.supervisor.nodes.planner.utils import _build_current_goal_section

        text = _build_current_goal_section(
            query="hi", summary="", file_info="", memory_dfs_info="",
            sources_section="src", task_plan=[], max_chars=2000,
            knowledge_path_hint="",  # rag 默认时空字符串
        )
        assert "知识检索路由" not in text

    def test_wiki_hint_注入_prompt(self):
        from app.supervisor.nodes.planner.utils import _build_current_goal_section

        hint = "知识检索路由: wiki（doc_worker 将装载预编译实体页）"
        text = _build_current_goal_section(
            query="hi", summary="", file_info="", memory_dfs_info="",
            sources_section="src", task_plan=[], max_chars=2000,
            knowledge_path_hint=hint,
        )
        assert hint in text

    def test_build_planner_context_集成_rag_默认无_hint(self):
        # 集成测试：state.knowledge_path=rag 时 build_planner_context 不输出 hint
        from app.supervisor.nodes.planner.utils import build_planner_context

        state = {
            "user_query": "test query",
            "summary": "",
            "user_context": {},
            "knowledge_path": "rag",
            "knowledge_path_reason": "Wiki-First 开关未开启",
        }
        ctx = build_planner_context(state)
        # current_goal_section 段中不应有路由提示
        cgs = ctx.get("current_goal_section", "")
        assert "知识检索路由" not in cgs

    def test_build_planner_context_集成_wiki_输出_hint(self):
        from app.supervisor.nodes.planner.utils import build_planner_context

        state = {
            "user_query": "差旅是什么",
            "summary": "",
            "user_context": {},
            "knowledge_path": "wiki",
            "knowledge_path_reason": "规则匹配：含 Wiki 关键词 ['是什么']",
        }
        ctx = build_planner_context(state)
        cgs = ctx.get("current_goal_section", "")
        assert "知识检索路由: wiki" in cgs

    def test_build_planner_context_集成_both_输出_hint(self):
        from app.supervisor.nodes.planner.utils import build_planner_context

        state = {
            "user_query": "总结差旅并给出原文",
            "summary": "",
            "user_context": {},
            "knowledge_path": "both",
            "knowledge_path_reason": "规则匹配：同时含 RAG 与 Wiki 关键词",
        }
        ctx = build_planner_context(state)
        cgs = ctx.get("current_goal_section", "")
        assert "知识检索路由: both" in cgs
