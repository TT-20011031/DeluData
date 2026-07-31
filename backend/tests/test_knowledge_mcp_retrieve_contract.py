from types import SimpleNamespace

import pytest

from app.models.common.execution import DocumentChunk
from app.services.knowledge_mcp_service import KnowledgeMcpService


@pytest.mark.asyncio
async def test_retrieve_answer_context_uses_original_file_name(monkeypatch):
    async def fake_query_knowledge_base(self, **_kwargs):
        return [
            DocumentChunk(
                content="沐浴露 国际知名品牌 20瓶 400ml",
                source_file="storage_ndk38aee.pdf",
                chunk_id="chunk-1",
                score=0.91,
                rerank_score=0.88,
                metadata={
                    "file_id": "file-1",
                    "source_file": "storage_ndk38aee.pdf",
                    "page_numbers": "2,3",
                    "type": "text",
                },
            )
        ]

    async def fake_load_file_display_names(self, workspace_id, file_ids):
        assert workspace_id == "workspace-1"
        assert file_ids == {"file-1"}
        return {"file-1": "24年-1-杭州泰泽办公设备有限公司.pdf"}

    monkeypatch.setattr(
        "app.services.knowledge_mcp_service.DocSkill.query_knowledge_base",
        fake_query_knowledge_base,
    )
    monkeypatch.setattr(
        KnowledgeMcpService,
        "_load_file_display_names",
        fake_load_file_display_names,
    )

    service = KnowledgeMcpService.__new__(KnowledgeMcpService)
    service.mcp_settings = SimpleNamespace(
        knowledge_user_id="user-1",
        knowledge_workspace_id="workspace-1",
        knowledge_role="admin",
        knowledge_dept_id=None,
        knowledge_default_top_k=5,
    )

    payload = await service.retrieve(query="沐浴露")

    assert "24年-1-杭州泰泽办公设备有限公司.pdf" in payload["answer_context"]
    assert "storage_ndk38aee.pdf" not in payload["answer_context"]
    assert "引用编号：[1]" in payload["answer_context"]
    assert "页码：2,3" in payload["answer_context"]

    item = payload["answer_context_items"][0]
    assert item["reference"] == "[1]"
    assert item["citation_number"] == 1
    assert item["original_file_name"] == "24年-1-杭州泰泽办公设备有限公司.pdf"
    assert item["page_numbers"] == [2, 3]

    result = payload["results"][0]
    assert result["source_file"] == "24年-1-杭州泰泽办公设备有限公司.pdf"
    assert result["stored_source_file"] == "storage_ndk38aee.pdf"
    assert result["reference"] == "[1]"

    citation = payload["citations"][0]
    assert citation["original_file_name"] == "24年-1-杭州泰泽办公设备有限公司.pdf"
    assert citation["stored_source_file"] == "storage_ndk38aee.pdf"
    assert citation["reference"] == "[1]"


@pytest.mark.asyncio
async def test_ask_uses_auto_workflow_and_returns_used_file_citations(monkeypatch):
    calls = {"confirm": []}

    async def fake_start_new_session(self, **kwargs):
        calls.update(kwargs)
        return {
            "session_id": "session-1",
            "plan_id": "plan-1",
            "summary": "查询供应商历史价格",
            "steps": [{"step_id": "1", "worker": "doc_worker"}],
            "status": "draft",
            "message": "已生成任务计划。",
            "need_confirm": False,
            "selected_skill_name": None,
        }

    async def fake_confirm_and_execute(
        self,
        session_id,
        plan_id,
        user_context,
        modified_steps=None,
    ):
        calls["confirm"].append((session_id, plan_id, modified_steps))
        return {
            "plan_status": "completed",
            "final_answer": "大型打印机 3700 元/台，小型桌面打印机 290 元/台。",
        }

    async def fake_get_session_plan(self, session_id, user_context):
        assert session_id == "session-1"
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

    monkeypatch.setattr(
        "app.services.chat_service.ChatService.start_new_session",
        fake_start_new_session,
    )
    monkeypatch.setattr(
        "app.services.chat_service.ChatService.confirm_and_execute",
        fake_confirm_and_execute,
    )
    monkeypatch.setattr(
        "app.services.chat_service.ChatService.get_session_plan",
        fake_get_session_plan,
    )

    service = KnowledgeMcpService.__new__(KnowledgeMcpService)
    service.mcp_settings = SimpleNamespace(
        knowledge_user_id="user-1",
        knowledge_workspace_id="workspace-1",
        knowledge_role="admin",
        knowledge_dept_id=None,
        knowledge_default_top_k=5,
    )

    payload = await service.ask(
        query="杭州泰泽办公设备有限公司提供的打印机历史价格是多少？",
        reply_model_key="plus",
        deep_search=True,
        session_id="session-1",
    )

    assert payload["answer"] == "大型打印机 3700 元/台，小型桌面打印机 290 元/台。"
    assert payload["status"] == "completed"
    assert payload["session_id"] == "session-1"
    assert calls["message"] == "杭州泰泽办公设备有限公司提供的打印机历史价格是多少？"
    assert calls["execution_mode"] == "auto"
    assert calls["reply_model_key"] == "plus"
    assert calls["deep_search"] is True
    assert calls["user_context"].workspace_id == "workspace-1"
    assert calls["confirm"] == [
        (
            "session-1",
            "plan-1",
            [{"step_id": "1", "worker": "doc_worker"}],
        )
    ]
    assert payload["citations"] == [
        {
            "citation_number": 1,
            "reference": "[1]",
            "document_id": "file-1",
            "original_file_name": "24年-1-杭州泰泽办公设备有限公司.pdf",
            "source_file": "24年-1-杭州泰泽办公设备有限公司.pdf",
            "page_numbers": [2],
            "chunk_id": "chunk-1",
        }
    ]
