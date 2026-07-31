import logging
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app.services.knowledge_mcp_service as knowledge_mcp_service
from app.models.common.context import UserContext
from app.models.common.execution import DocumentChunk
from app.services.knowledge_mcp_service import KnowledgeMcpService
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


def _service():
    service = KnowledgeMcpService.__new__(KnowledgeMcpService)
    service.mcp_settings = SimpleNamespace(
        knowledge_user_id="sport",
        knowledge_workspace_id="workspace-1",
        knowledge_role="admin",
        knowledge_dept_id=None,
        knowledge_default_top_k=5,
    )
    return service


class KnowledgeMcpAskConsistencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_runs_auto_plan_to_completion_and_returns_citations(self):
        calls = {"start": None, "confirm": None}

        async def fake_start(_self, **kwargs):
            calls["start"] = kwargs
            return {
                "session_id": "session-1",
                "plan_id": "plan-1",
                "summary": "查询供应商历史价格",
                "steps": [{"step_id": "1", "worker": "doc_worker"}],
                "status": "draft",
                "message": "已生成任务计划。",
                "need_confirm": False,
            }

        async def fake_confirm(_self, session_id, plan_id, modified_steps=None):
            calls["confirm"] = (session_id, plan_id, modified_steps)
            return {
                "plan_status": "completed",
                "final_answer": "大型打印机 3700 元/台，小型桌面打印机 290 元/台。",
            }

        async def fake_plan(_self, _session_id):
            return {
                "status": "completed",
                "execution_results": [
                    {
                        "worker": "doc_worker",
                        "meta": {
                            "final_context": [
                                {
                                    "used": True,
                                    "source": "rag",
                                    "file_id": "file-1",
                                    "file_name": "24年-1-杭州泰泽办公设备有限公司.pdf",
                                    "page_number": 2,
                                    "chunk_id": "chunk-1",
                                }
                            ]
                        },
                    }
                ],
            }

        with (
            patch("app.services.chat_service.ChatService.start_new_session", new=fake_start),
            patch("app.services.chat_service.ChatService.confirm_and_execute", new=fake_confirm),
            patch("app.services.chat_service.ChatService.get_session_plan", new=fake_plan),
        ):
            payload = await _service().ask(
                query="杭州泰泽办公设备有限公司提供的打印机历史价格是多少？",
                reply_model_key="plus",
                session_id="session-1",
            )

        self.assertEqual(calls["start"]["execution_mode"], "auto")
        self.assertEqual(
            calls["confirm"],
            ("session-1", "plan-1", [{"step_id": "1", "worker": "doc_worker"}]),
        )
        self.assertIn("3700", payload["answer"])
        self.assertIn("290", payload["answer"])
        self.assertEqual(
            payload["citations"][0]["original_file_name"],
            "24年-1-杭州泰泽办公设备有限公司.pdf",
        )

    async def test_ask_injects_default_mcp_evidence_top_k(self):
        captured = {}

        async def fake_start(_self, **kwargs):
            captured.update(kwargs)
            return {
                "session_id": "session-1",
                "plan_id": "plan-1",
                "steps": [],
                "status": "completed",
                "message": "ok",
            }

        async def fake_plan(_self, _session_id):
            return {"status": "completed", "execution_results": []}

        with (
            patch("app.services.chat_service.ChatService.start_new_session", new=fake_start),
            patch("app.services.chat_service.ChatService.get_session_plan", new=fake_plan),
        ):
            await _service().ask(query="q")

        self.assertEqual(captured["mcp_ask_evidence_top_k"], 20)

    async def test_ask_clamps_request_top_k(self):
        captured = []

        async def fake_start(_self, **kwargs):
            captured.append(kwargs["mcp_ask_evidence_top_k"])
            return {
                "session_id": "session-1",
                "plan_id": "plan-1",
                "steps": [],
                "status": "completed",
                "message": "ok",
            }

        async def fake_plan(_self, _session_id):
            return {"status": "completed", "execution_results": []}

        with (
            patch("app.services.chat_service.ChatService.start_new_session", new=fake_start),
            patch("app.services.chat_service.ChatService.get_session_plan", new=fake_plan),
        ):
            await _service().ask(query="q", top_k=8)
            await _service().ask(query="q", top_k=99)
            await _service().ask(query="q", top_k=0)

        self.assertEqual(captured, [8, 20, 20])

    def test_extracts_only_explicit_file_names(self):
        extractor = getattr(
            knowledge_mcp_service,
            "_extract_explicit_file_names",
            None,
        )
        self.assertIsNotNone(
            extractor,
            "MCP Ask must expose deterministic explicit filename extraction",
        )
        self.assertEqual(
            extractor(
                "请查询《25年框-浙江欣赞文化创意有限公司.pdf》，"
                "并对照 24年-4-浙江欣赞文化创意有限公司.pdf。"
            ),
            [
                "25年框-浙江欣赞文化创意有限公司.pdf",
                "24年-4-浙江欣赞文化创意有限公司.pdf",
            ],
        )
        self.assertEqual(
            extractor("浙江欣赞文化创意有限公司的打印机历史价格是多少？"),
            [],
        )

    def test_matches_unquoted_file_name_after_query_prefix_without_suffix_collision(self):
        matcher = getattr(
            knowledge_mcp_service,
            "_match_explicit_file_ids",
            None,
        )
        self.assertIsNotNone(matcher)
        rows = [
            ("short-file", "report.pdf"),
            ("long-file", "notreport.pdf"),
            ("target-file", "采购 清单 (A).pdf"),
        ]

        self.assertEqual(
            matcher(["请查询采购 清单 (A).pdf中的设备价格"], rows),
            ["target-file"],
        )
        self.assertEqual(
            matcher(["请查询notreport.pdf"], rows),
            ["long-file"],
        )

    async def test_ask_injects_budget_and_explicit_file_targets(self):
        parameters = inspect.signature(KnowledgeMcpService.ask).parameters
        self.assertIn("evidence_token_budget", parameters)

        captured = {}

        async def fake_start(_self, **kwargs):
            captured.update(kwargs)
            return {
                "session_id": "session-1",
                "plan_id": "plan-1",
                "steps": [],
                "status": "completed",
                "message": "ok",
            }

        async def fake_plan(_self, _session_id):
            return {"status": "completed", "execution_results": []}

        service = _service()
        service._resolve_required_file_ids = AsyncMock(
            return_value=["target-file"]
        )
        with (
            patch(
                "app.services.chat_service.ChatService.start_new_session",
                new=fake_start,
            ),
            patch(
                "app.services.chat_service.ChatService.get_session_plan",
                new=fake_plan,
            ),
        ):
            await service.ask(
                query=(
                    "请查询《25年框-浙江欣赞文化创意有限公司.pdf》"
                    "中的打印机"
                ),
            )

        self.assertEqual(captured["mcp_ask_evidence_token_budget"], 9000)
        self.assertEqual(
            captured["mcp_ask_required_file_ids"],
            ["target-file"],
        )
        self.assertTrue(captured["mcp_ask_preserve_related_candidates"])
        service._resolve_required_file_ids.assert_awaited_once_with(
            "workspace-1",
            ["25年框-浙江欣赞文化创意有限公司.pdf"],
            (
                "请查询《25年框-浙江欣赞文化创意有限公司.pdf》"
                "中的打印机"
            ),
        )

    async def test_ask_caps_required_file_targets_to_top_k(self):
        captured = {}

        async def fake_start(_self, **kwargs):
            captured.update(kwargs)
            return {
                "session_id": "session-1",
                "plan_id": "plan-1",
                "steps": [],
                "status": "completed",
                "message": "ok",
            }

        async def fake_plan(_self, _session_id):
            return {"status": "completed", "execution_results": []}

        service = _service()
        service._resolve_required_file_ids = AsyncMock(
            return_value=[f"file-{index}" for index in range(10)]
        )
        with (
            patch(
                "app.services.chat_service.ChatService.start_new_session",
                new=fake_start,
            ),
            patch(
                "app.services.chat_service.ChatService.get_session_plan",
                new=fake_plan,
            ),
        ):
            await service.ask(query="请查询 report.pdf", top_k=3)

        self.assertEqual(
            captured["mcp_ask_required_file_ids"],
            ["file-0", "file-1", "file-2"],
        )

    async def test_ask_clamps_request_evidence_token_budget(self):
        parameters = inspect.signature(KnowledgeMcpService.ask).parameters
        self.assertIn("evidence_token_budget", parameters)

        captured = []

        async def fake_start(_self, **kwargs):
            captured.append(kwargs["mcp_ask_evidence_token_budget"])
            return {
                "session_id": "session-1",
                "plan_id": "plan-1",
                "steps": [],
                "status": "completed",
                "message": "ok",
            }

        async def fake_plan(_self, _session_id):
            return {"status": "completed", "execution_results": []}

        service = _service()
        service._resolve_required_file_ids = AsyncMock(return_value=[])
        with (
            patch(
                "app.services.chat_service.ChatService.start_new_session",
                new=fake_start,
            ),
            patch(
                "app.services.chat_service.ChatService.get_session_plan",
                new=fake_plan,
            ),
        ):
            await service.ask(query="q", evidence_token_budget=6000)
            await service.ask(query="q", evidence_token_budget=99999)
            await service.ask(query="q", evidence_token_budget=1000)

        self.assertEqual(captured, [6000, 16000, 2000])

    async def test_doc_skill_fuses_exact_filename_hits_with_existing_candidates(self):
        raw_query = "杭州泰泽办公设备有限公司提供的打印机历史价格是多少？"
        other = DocumentChunk(
            content="浙江欣赞打印机报价 3600 元",
            source_file="浙江欣赞.pdf",
            chunk_id="other-1",
            score=0.82,
            rerank_score=0.82,
            metadata={"file_id": "other-file"},
        )
        target = DocumentChunk(
            content="大型打印机 3700 元/台；小型桌面打印机 290 元/台",
            source_file="24年-1-杭州泰泽办公设备有限公司.pdf",
            chunk_id="target-1",
            score=0.94,
            rerank_score=0.94,
            metadata={"file_id": "target-file", "exact_file_name_match": True},
        )
        rewriter = _FakeRewriter()
        retriever = _FakeRetriever([other])
        skill = DocSkill.__new__(DocSkill)
        skill._query_rewriter = rewriter
        skill._retriever = retriever
        skill._reranker = _FakeReranker()
        skill._context_expander = None
        skill.validate_user_context = lambda _ctx: True

        async def fake_exact_search(**_kwargs):
            return [target]

        skill._exact_term_fallback_search = fake_exact_search
        settings = SimpleNamespace(
            rag=SimpleNamespace(
                merged_top_n=20,
                rerank_score_threshold=0.1,
                enable_context_expansion=False,
                image_score_threshold=0.5,
            )
        )
        with patch("app.skills.doc_skill.get_settings", return_value=settings):
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

        self.assertEqual([chunk.chunk_id for chunk in chunks], ["target-1"])
        self.assertEqual(rewriter.calls, [("查询打印机价格", raw_query)])
        self.assertIn(raw_query, retriever.queries)

    async def test_doc_skill_mcp_mode_preserves_related_semantic_candidates(self):
        parameters = inspect.signature(DocSkill.query_knowledge_base).parameters
        self.assertIn("preserve_related_candidates", parameters)

        raw_query = "请查询《采购清单-A.pdf》，并补充其他相似采购历史。"
        related = DocumentChunk(
            content="相似采购记录：设备乙 3200 元/台",
            source_file="采购清单-B.pdf",
            chunk_id="related-1",
            score=0.82,
            rerank_score=0.82,
            metadata={"file_id": "related-file"},
        )
        target = DocumentChunk(
            content="目标采购记录：设备甲 3500 元/台",
            source_file="采购清单-A.pdf",
            chunk_id="target-1",
            score=0.94,
            rerank_score=0.94,
            metadata={"file_id": "target-file", "exact_file_name_match": True},
        )
        skill = DocSkill.__new__(DocSkill)
        skill._query_rewriter = _FakeRewriter()
        skill._retriever = _FakeRetriever([related])
        skill._reranker = _FakeReranker()
        skill._context_expander = None
        skill.validate_user_context = lambda _ctx: True

        async def fake_exact_search(**_kwargs):
            return [target]

        skill._exact_term_fallback_search = fake_exact_search
        settings = SimpleNamespace(
            rag=SimpleNamespace(
                merged_top_n=20,
                rerank_score_threshold=0.1,
                enable_context_expansion=False,
                image_score_threshold=0.5,
            )
        )
        with patch("app.skills.doc_skill.get_settings", return_value=settings):
            chunks = await skill.query_knowledge_base(
                query="查询目标文件并补充相似历史",
                original_query=raw_query,
                user_context=UserContext(
                    user_id="sport",
                    workspace_id="workspace-1",
                    role="admin",
                    capabilities=["*"],
                ),
                top_k=5,
                include_images=False,
                preserve_related_candidates=True,
            )

        self.assertEqual(
            [chunk.chunk_id for chunk in chunks],
            ["target-1", "related-1"],
        )

    async def test_exact_filename_hits_survive_semantic_retriever_failure(self):
        target = DocumentChunk(
            content="大型打印机 3700 元/台；小型桌面打印机 290 元/台",
            source_file="24年-1-杭州泰泽办公设备有限公司.pdf",
            chunk_id="target-1",
            score=0.94,
            rerank_score=0.94,
            metadata={"file_id": "target-file", "exact_file_name_match": True},
        )

        class FailingRetriever:
            async def search(self, **_kwargs):
                raise ConnectionError("embedding unavailable")

        skill = DocSkill.__new__(DocSkill)
        skill.name = "DocSkill"
        skill.logger = logging.getLogger("test.DocSkill")
        skill._query_rewriter = _FakeRewriter()
        skill._retriever = FailingRetriever()
        skill._reranker = _FakeReranker()
        skill._context_expander = None
        skill.validate_user_context = lambda _ctx: True

        async def fake_exact_search(**_kwargs):
            return [target]

        skill._exact_term_fallback_search = fake_exact_search
        settings = SimpleNamespace(
            rag=SimpleNamespace(
                merged_top_n=20,
                rerank_score_threshold=0.1,
                enable_context_expansion=False,
                image_score_threshold=0.5,
            )
        )
        with patch("app.skills.doc_skill.get_settings", return_value=settings):
            chunks = await skill.query_knowledge_base(
                query="查询打印机价格",
                original_query="杭州泰泽办公设备有限公司提供的打印机历史价格是多少？",
                user_context=UserContext(
                    user_id="sport",
                    workspace_id="workspace-1",
                    role="admin",
                    capabilities=["*"],
                ),
                top_k=5,
                include_images=False,
            )

        self.assertEqual([chunk.chunk_id for chunk in chunks], ["target-1"])

    async def test_exact_filename_match_restricts_exact_hits_to_matching_files(self):
        class FakeCollection:
            def get(self, **_kwargs):
                return {
                    "ids": ["target-1", "other-1"],
                    "documents": [
                        "大型打印机 3700 元/台；小型桌面打印机 290 元/台",
                        "杭州泰泽办公设备有限公司与浙江欣赞打印机价格比较",
                    ],
                    "metadatas": [
                        {
                            "workspace_id": "workspace-1",
                            "file_id": "target-file",
                            "source_file": "stored-target.pdf",
                        },
                        {
                            "workspace_id": "workspace-1",
                            "file_id": "other-file",
                            "source_file": "浙江欣赞.pdf",
                        },
                    ],
                }

        skill = DocSkill.__new__(DocSkill)
        skill._collection = FakeCollection()

        async def fake_file_names(_workspace_id, _terms):
            return {"target-file": "24年-1-杭州泰泽办公设备有限公司.pdf"}

        async def fake_scope(_ctx):
            return []

        skill._load_matching_file_names = fake_file_names
        settings = SimpleNamespace(rag=SimpleNamespace(repair_synonym_pairs={}))
        with (
            patch("app.skills.doc_skill.get_settings", return_value=settings),
            patch("app.skills.doc_skill.resolve_scope_dept_ids", new=fake_scope),
            patch("app.skills.doc_skill.can_access_metadata", return_value=True),
        ):
            chunks = await skill._exact_term_fallback_search(
                query="杭州泰泽办公设备有限公司提供的打印机历史价格是多少？",
                original_query=None,
                user_context=UserContext(
                    user_id="sport",
                    workspace_id="workspace-1",
                    role="admin",
                    capabilities=["*"],
                ),
                top_k=5,
                include_images=False,
                file_ids=None,
                visibilities=None,
                dept_ids=None,
            )

        self.assertEqual([chunk.chunk_id for chunk in chunks], ["target-1"])
        self.assertTrue(chunks[0].metadata["exact_file_name_match"])

    async def test_executor_passes_current_user_query_to_doc_worker(self):
        captured = {}

        class FakeTool:
            async def ainvoke(self, args):
                captured.update(args)
                return WorkerResult(
                    output="知识库结果",
                    quality_signal={"verdict": "pass", "reason_code": "rag_ok"},
                    meta={"chunks_used": 1, "stop_reason": "rag_ok"},
                )

        with patch("app.tools.registry.get_tool", return_value=FakeTool()):
            await execute_worker_task(
                worker="doc_worker",
                description="查询打印机历史价格",
                user_context={"user_id": "sport", "workspace_id": "workspace-1"},
                user_query="杭州泰泽办公设备有限公司提供的打印机历史价格是多少？",
            )

        self.assertEqual(
            captured["original_query"],
            "杭州泰泽办公设备有限公司提供的打印机历史价格是多少？",
        )


if __name__ == "__main__":
    unittest.main()
