"""M3.4 单元测试 - wiki_gap 信号回写 + Synthesizer Wiki 引用注入。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.common.execution import DocumentChunk
from app.supervisor.nodes.synthesizer import _build_wiki_entities_block


# ============ Synthesizer Wiki 实体清单 helper ============

class TestBuildWikiEntitiesBlock:
    def test_空_results_返回空串(self):
        assert _build_wiki_entities_block([]) == ""

    def test_无_wiki_entities_返回空串(self):
        results = [
            {"step_id": "1", "worker": "doc_worker", "meta": {"wiki_chunks_count": 0}},
            {"step_id": "2", "worker": "sql_worker", "meta": {}},
        ]
        assert _build_wiki_entities_block(results) == ""

    def test_单个_wiki_entity_格式正确(self):
        results = [{
            "step_id": "1",
            "worker": "doc_worker",
            "meta": {"wiki_entities": [
                {"slug": "travel-policy", "title": "差旅报销制度", "summary": "全员适用"}
            ]}
        }]
        text = _build_wiki_entities_block(results)
        assert "## Wiki 实体页索引（已装载）" in text
        assert "[[travel-policy|差旅报销制度]] — 全员适用" in text
        assert "## Wiki 引用规范" in text
        assert "[[slug|title]]" in text  # 规范说明

    def test_多_result_合并并按_slug_去重(self):
        results = [
            {
                "step_id": "1",
                "worker": "doc_worker",
                "meta": {"wiki_entities": [
                    {"slug": "a", "title": "A", "summary": "sa"},
                    {"slug": "b", "title": "B", "summary": "sb"},
                ]},
            },
            {
                "step_id": "2",
                "worker": "doc_worker",
                "meta": {"wiki_entities": [
                    {"slug": "a", "title": "A 重复", "summary": "重复条目"},  # 应去重
                    {"slug": "c", "title": "C", "summary": ""},
                ]},
            },
        ]
        text = _build_wiki_entities_block(results)
        assert text.count("[[a|") == 1  # 去重
        assert "[[b|B]]" in text
        assert "[[c|C]]" in text  # summary 为空时不带 — 后缀
        assert " — 重复条目" not in text  # 去重保留先出现的

    def test_无_summary_不带破折号(self):
        results = [{
            "meta": {"wiki_entities": [{"slug": "x", "title": "T", "summary": ""}]}
        }]
        text = _build_wiki_entities_block(results)
        assert "[[x|T]]" in text
        # 找该行
        lines = [l for l in text.split("\n") if "[[x|T]]" in l]
        assert lines and " — " not in lines[0]

    def test_缺失_slug_或_title_的_entity_被跳过(self):
        results = [{
            "meta": {"wiki_entities": [
                {"slug": "", "title": "无 slug"},
                {"slug": "ok", "title": ""},
                {"slug": "ok2", "title": "正常"},
            ]}
        }]
        text = _build_wiki_entities_block(results)
        assert "[[ok2|正常]]" in text
        # 非法条目不应出现
        assert "[[|" not in text
        assert "[[ok|]]" not in text


# ============ doc_tool wiki_gap 触发逻辑 ============

def _make_doc_chunk(*, content="x", chunk_id="c1", score=0.9, file_id="f-1") -> DocumentChunk:
    return DocumentChunk(
        content=content,
        source_file="test.pdf",
        chunk_id=chunk_id,
        score=score,
        rerank_score=score,
        metadata={"file_id": file_id} if file_id else {},
    )


def _install_mock_skill(monkeypatch, mock_skill: MagicMock) -> None:
    from app.skills import doc_skill as doc_skill_module
    monkeypatch.setattr(doc_skill_module, "DocSkill", lambda *a, **kw: mock_skill)


def _install_mock_queue(monkeypatch, enqueue_calls: list) -> None:
    """替换 wiki_compile_queue 单例，记录入队调用。"""
    from app.services import wiki_compile_queue_service as wq_module

    fake_task = SimpleNamespace(id="task-fake-001")

    async def fake_enqueue(*args, **kwargs):
        enqueue_calls.append(kwargs)
        return fake_task

    fake_queue = MagicMock()
    fake_queue.enqueue_task = fake_enqueue
    monkeypatch.setattr(wq_module, "get_wiki_compile_queue", lambda: fake_queue)


def _stub_async_db_manager(monkeypatch) -> None:
    """屏蔽 doc_tool 内 file_info_map 查询，避免 SQLAlchemy AsyncEngine 跨 event loop 报错。

    doc_tool 内是函数体内 import，故直接 patch 源模块属性即可（之后 import 拿到 mock）。
    """
    from app.core.db import database as db_module

    class _FakeResult:
        def __iter__(self):
            return iter([])
        def all(self):
            return []
        def fetchall(self):
            return []

    class _FakeSession:
        async def execute(self, *args, **kwargs):
            return _FakeResult()

    class _FakeScope:
        async def __aenter__(self):
            return _FakeSession()
        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_mgr = MagicMock()
    fake_mgr.session_scope = lambda: _FakeScope()
    monkeypatch.setattr(db_module, "get_async_db_manager", lambda: fake_mgr)


@pytest.mark.asyncio
class TestWikiGapEnqueue:
    async def test_wiki_path_navigator_零命中_触发入队(self, monkeypatch):
        """wiki 路径但 navigator 0 命中、有 RAG file_ids → 入队 wiki_gap。"""
        from app.tools.doc_tool import run_doc_task

        async def fake_query_kb(*args, **kwargs):
            # RAG 命中文件，提供 file_id 作为编译候选
            return [_make_doc_chunk(content="rag content", chunk_id="rag-c1", score=1.0, file_id="file-001")]

        async def fake_wiki_navigate(*args, **kwargs):
            return []  # navigator 0 命中

        skill = MagicMock()
        skill.query_knowledge_base = fake_query_kb
        skill.wiki_navigate = fake_wiki_navigate
        _install_mock_skill(monkeypatch, skill)

        enqueue_calls: list = []
        _install_mock_queue(monkeypatch, enqueue_calls)
        _stub_async_db_manager(monkeypatch)

        result = await run_doc_task(
            query="差旅怎么报销",
            user_context={"user_id": "u-1", "workspace_id": "ws-1"},
            knowledge_path="both",  # both 路径同样应触发
            doc_scope={"include_images": False},
        )

        # 入队被调用一次，trigger_type=wiki_gap
        assert len(enqueue_calls) == 1
        call = enqueue_calls[0]
        assert call["workspace_id"] == "ws-1"
        assert call["trigger_type"] == "wiki_gap"
        assert "file-001" in call["payload"]["file_ids"]
        assert call["payload"]["knowledge_path"] == "both"

        # meta 中 wiki_gap_signal 非空
        sig = result.meta["wiki_gap_signal"]
        assert sig is not None
        assert sig["task_id"] == "task-fake-001"
        assert "file-001" in sig["file_ids"]

    async def test_rag_路径不触发(self, monkeypatch):
        from app.tools.doc_tool import run_doc_task

        async def fake_query_kb(*args, **kwargs):
            return [_make_doc_chunk(content="rag", chunk_id="c1", score=1.0, file_id="file-001")]

        async def fake_wiki_navigate(*args, **kwargs):
            return []

        skill = MagicMock()
        skill.query_knowledge_base = fake_query_kb
        skill.wiki_navigate = fake_wiki_navigate
        _install_mock_skill(monkeypatch, skill)

        enqueue_calls: list = []
        _install_mock_queue(monkeypatch, enqueue_calls)
        _stub_async_db_manager(monkeypatch)

        result = await run_doc_task(
            query="q",
            user_context={"user_id": "u-1", "workspace_id": "ws-1"},
            knowledge_path="rag",  # rag 路径不应触发
            doc_scope={"include_images": False},
        )

        assert enqueue_calls == []
        assert result.meta["wiki_gap_signal"] is None

    async def test_wiki_navigator_命中_不触发(self, monkeypatch):
        """wiki 路径 + navigator 命中 → 不触发 wiki_gap。"""
        from app.tools.doc_tool import run_doc_task

        async def fake_query_kb(*args, **kwargs):
            return []  # 仅 wiki 路径无需 rag

        async def fake_wiki_navigate(*args, **kwargs):
            return [DocumentChunk(
                content="# wiki page",
                source_file="wiki:slug",
                chunk_id="wiki_page:1",
                score=1.0,
                rerank_score=1.0,
                metadata={"kind": "wiki_page", "slug": "slug-1", "title": "T1"},
            )]

        skill = MagicMock()
        skill.query_knowledge_base = fake_query_kb
        skill.wiki_navigate = fake_wiki_navigate
        _install_mock_skill(monkeypatch, skill)

        enqueue_calls: list = []
        _install_mock_queue(monkeypatch, enqueue_calls)
        _stub_async_db_manager(monkeypatch)

        result = await run_doc_task(
            query="q",
            user_context={"user_id": "u-1", "workspace_id": "ws-1"},
            knowledge_path="wiki",
            doc_scope={"include_images": False},
        )

        assert enqueue_calls == []
        assert result.meta["wiki_gap_signal"] is None
        # 同时验证 wiki_entities 透传
        assert result.meta["wiki_entities"] == [
            {"slug": "slug-1", "title": "T1", "summary": ""}
        ]

    async def test_wiki_无_file_ids_不入队(self, monkeypatch):
        """both 路径 + navigator 0 + RAG chunks 无 file_id → 没有候选可编译，不入队。"""
        from app.tools.doc_tool import run_doc_task

        async def fake_query_kb(*args, **kwargs):
            return [_make_doc_chunk(content="x", chunk_id="c1", score=1.0, file_id=None)]

        async def fake_wiki_navigate(*args, **kwargs):
            return []

        skill = MagicMock()
        skill.query_knowledge_base = fake_query_kb
        skill.wiki_navigate = fake_wiki_navigate
        _install_mock_skill(monkeypatch, skill)

        enqueue_calls: list = []
        _install_mock_queue(monkeypatch, enqueue_calls)
        _stub_async_db_manager(monkeypatch)

        result = await run_doc_task(
            query="q",
            user_context={"user_id": "u-1", "workspace_id": "ws-1"},
            knowledge_path="both",  # both 路径才会同时走 RAG，能测到 file_id 缺失分支
            doc_scope={"include_images": False},
        )

        assert enqueue_calls == []
        assert result.meta["wiki_gap_signal"] is None
