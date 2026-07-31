import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.experience.services.answer_runtime_service import AnswerRuntimeService


class _StubLlm:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages, *, model: str):
        self.calls.append(messages)
        return self.response


class _StubVoiceConfigService:
    async def resolve_voice_key(self, **_kwargs):
        return "robot"


class _StubDocSkill:
    def __init__(self, chunks=None):
        self.chunks = list(chunks or [])
        self.last_query = ""

    async def query_knowledge_base(self, **kwargs):
        self.last_query = kwargs.get("query", "")
        return list(self.chunks)


class _StubDb:
    def __init__(self):
        self.flush_count = 0

    async def flush(self):
        self.flush_count += 1


def _build_service(
    response: str,
    *,
    doc_skill=None,
    db=None,
    context=None,
) -> tuple[AnswerRuntimeService, _StubLlm]:
    llm = _StubLlm(response)
    settings = SimpleNamespace(
        llm=SimpleNamespace(fast_model="qwen3.5-flash"),
        experience=SimpleNamespace(
            answer_system_prompt="system",
            answer_prompt_template=(
                "知识回答，限制 {min_chars}-{max_chars} 字。\n问题: {query}\n内容:\n{context}"
            ),
            answer_empty_context_prompt=(
                "人格化兜底，限制 {min_chars}-{max_chars} 字。\n问题: {query}\n画像: {profile_summary}"
            ),
            answer_no_context_message="我是熊猫讲解员，我们继续聊聊吧，或者先来一题挑战？",
            answer_extract_fallback_message="我来换个更轻松的说法，你也可以继续追问我。",
            answer_target_min_chars=30,
            answer_max_chars=50,
        ),
    )
    service = AnswerRuntimeService(
        db=db,
        context=context,
        user_context=None,
        settings=settings,
        llm=llm,
        doc_skill=doc_skill,
        voice_config_service=_StubVoiceConfigService(),
    )
    return service, llm


def _build_session():
    return SimpleNamespace(
        id="session-1",
        metadata_json={
            "profile": {
                "age_group": "adult",
                "gender": "female",
                "feature_tags": ["实验风", "好奇", "彩色外套"],
                "persona_text": "会把复杂内容讲得更自然、更好懂。",
            }
        },
    )


@pytest.mark.asyncio
async def test_build_answer_from_chunks_uses_empty_context_prompt_only_when_no_chunks():
    service, llm = _build_service(
        "你好呀，我是熊猫讲解员，想先听科学故事，还是马上来一题挑战？"
    )

    answer, fallback_used = await service.build_answer_from_chunks(
        session=_build_session(),
        query="这里有什么互动装置",
        chunks=[],
        retrieval_scope={
            "mode": "files",
            "file_count": 1,
            "dept_count": 0,
            "visibility_count": 0,
        },
    )

    assert fallback_used is True
    assert len(answer) <= 50
    user_prompt = llm.calls[0][1]["content"]
    assert "人格化兜底" in user_prompt
    assert "这里有什么互动装置" in user_prompt
    assert "实验风" in user_prompt
    assert "范围:" not in user_prompt


@pytest.mark.asyncio
async def test_build_answer_from_chunks_uses_knowledge_prompt_and_clamps_length():
    service, llm = _build_service(
        "互动装置最重要的作用，是让大家边动手边理解科学原理。\n你也可以继续追问我它怎么工作的。"
    )
    chunks = [SimpleNamespace(content="互动装置可以帮助参观者通过操作理解科学原理。")]

    answer, fallback_used = await service.build_answer_from_chunks(
        session=_build_session(),
        query="互动装置是干什么的",
        chunks=chunks,
        retrieval_scope={
            "mode": "workspace",
            "file_count": 0,
            "dept_count": 0,
            "visibility_count": 0,
        },
    )

    assert fallback_used is False
    assert len(answer) <= 50
    user_prompt = llm.calls[0][1]["content"]
    assert "知识回答" in user_prompt
    assert "互动装置可以帮助参观者通过操作理解科学原理。" in user_prompt
    assert "空检索兜底" not in user_prompt


@pytest.mark.asyncio
async def test_build_answer_from_chunks_includes_recent_turns_when_present():
    service, llm = _build_service("它主要靠光电传感器识别动作。")
    session = _build_session()
    session.metadata_json["conversation"] = {
        "recent_turns": [
            {
                "user_text": "这个互动装置是干什么的",
                "assistant_text": "它能让大家边动手边理解科学原理。",
            }
        ]
    }

    await service.build_answer_from_chunks(
        session=session,
        query="它怎么工作的",
        chunks=[SimpleNamespace(content="互动装置通过光电传感器识别动作，再触发反馈。")],
        retrieval_scope={"mode": "workspace", "file_count": 0, "dept_count": 0, "visibility_count": 0},
    )

    user_prompt = llm.calls[0][1]["content"]
    assert "最近对话" in user_prompt
    assert "游客1: 这个互动装置是干什么的" in user_prompt
    assert "讲解员1: 它能让大家边动手边理解科学原理。" in user_prompt


@pytest.mark.asyncio
async def test_handle_asr_turn_uses_recent_turns_for_follow_up_and_persists_memory(monkeypatch):
    doc_skill = _StubDocSkill(
        [
            SimpleNamespace(
                content="互动装置通过光电传感器识别动作，再触发灯光和声音反馈。",
                metadata={"file_id": "file-1", "source_file": "science.txt"},
                rerank_score=0.88,
            )
        ]
    )
    db = _StubDb()
    service, _ = _build_service(
        "它主要通过光电传感器识别你的动作，再做出反馈。",
        doc_skill=doc_skill,
        db=db,
        context=SimpleNamespace(workspace_id="ws-1"),
    )
    session = _build_session()
    session.metadata_json["conversation"] = {
        "recent_turns": [
            {
                "user_text": "这个互动装置是干什么的",
                "assistant_text": "它能让大家边动手边理解科学原理。",
            }
        ]
    }

    async def _fake_scope(_self, workspace_id: str):
        assert workspace_id == "ws-1"
        return {"file_ids": [], "visibilities": [], "dept_ids": []}

    monkeypatch.setattr(
        "app.experience.services.answer_runtime_service.WorkspaceScopeService.get_scope",
        _fake_scope,
    )

    result = await service.handle_asr_turn(
        session=session,
        text="它怎么工作的",
        enable_tts=False,
        age_group="adult",
        gender="female",
        gender_confidence=0.9,
    )

    assert "上一轮问题：这个互动装置是干什么的" in doc_skill.last_query
    assert "当前问题：它怎么工作的" in doc_skill.last_query
    assert result["payload"]["answer"] == "它主要通过光电传感器识别你的动作，再做出反馈。"
    assert db.flush_count == 1
    turns = session.metadata_json["conversation"]["recent_turns"]
    assert turns[-1]["user_text"] == "它怎么工作的"
    assert "光电传感器" in turns[-1]["assistant_text"]
