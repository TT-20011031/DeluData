from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.core.db.checkpointer import MySQLSaver
from app.models.common.context import UserContext
from app.services.chat_service import ChatService
from app.services.session_repository import SessionRepository


def _user_context(*, user_id: str = "user-a", workspace_id: str = "workspace-a") -> UserContext:
    return UserContext(user_id=user_id, workspace_id=workspace_id)


class _EmptyResult:
    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []

    def one_or_none(self):
        return None


class _OwnerResult:
    def __init__(self, *, user_id: str, workspace_id: str):
        self.owner = SimpleNamespace(user_id=user_id, workspace_id=workspace_id)

    def one_or_none(self):
        return self.owner


class _CapturingSession:
    def __init__(self, results=None):
        self.statements = []
        self.merged = []
        self.committed = False
        self.results = list(results or [])

    async def execute(self, statement):
        self.statements.append(statement)
        if self.results:
            return self.results.pop(0)
        return _EmptyResult()

    async def merge(self, value):
        self.merged.append(value)

    async def commit(self):
        self.committed = True


def _saver_with_session(session: _CapturingSession) -> MySQLSaver:
    saver = object.__new__(MySQLSaver)

    @asynccontextmanager
    async def get_session():
        yield session

    saver._get_session = get_session
    return saver


@pytest.mark.asyncio
async def test_checkpoint_read_requires_user_and_workspace_identity():
    saver = _saver_with_session(_CapturingSession())

    with pytest.raises(ValueError, match="user_id.*workspace_id"):
        await saver.aget_tuple({"configurable": {"thread_id": "session-a"}})


@pytest.mark.asyncio
async def test_checkpoint_read_is_scoped_to_exact_user_and_workspace():
    session = _CapturingSession()
    saver = _saver_with_session(session)

    result = await saver.aget_tuple(
        {
            "configurable": {
                "thread_id": "session-a",
                "user_id": "user-a",
                "workspace_id": "workspace-a",
            }
        }
    )

    assert result is None
    statement = session.statements[0]
    compiled = statement.compile()
    assert "user-a" in compiled.params.values()
    assert "workspace-a" in compiled.params.values()
    assert "IS NULL" not in str(compiled)


@pytest.mark.asyncio
async def test_checkpoint_writes_allow_langgraph_pre_checkpoint_sequence():
    """LangGraph may persist task writes before the checkpoint row itself exists."""
    session = _CapturingSession()
    saver = _saver_with_session(session)

    await saver.aput_writes(
        {
            "configurable": {
                "thread_id": "session-a",
                "checkpoint_id": "checkpoint-new",
                "user_id": "user-a",
                "workspace_id": "workspace-a",
            }
        },
        [("planner", {"status": "draft"})],
        "task-a",
    )

    assert len(session.merged) == 1
    assert session.committed is True


@pytest.mark.asyncio
async def test_checkpoint_writes_reject_existing_checkpoint_owned_by_another_user():
    session = _CapturingSession(
        [_OwnerResult(user_id="user-b", workspace_id="workspace-a")]
    )
    saver = _saver_with_session(session)

    with pytest.raises(PermissionError, match="does not belong"):
        await saver.aput_writes(
            {
                "configurable": {
                    "thread_id": "session-a",
                    "checkpoint_id": "checkpoint-existing",
                    "user_id": "user-a",
                    "workspace_id": "workspace-a",
                }
            },
            [("planner", {"status": "draft"})],
            "task-a",
        )

    assert session.merged == []
    assert session.committed is False


@pytest.mark.asyncio
async def test_chat_service_passes_identity_to_graph_reads(monkeypatch):
    captured = {}

    class FakeGraph:
        async def aget_state(self, config):
            captured.update(config)
            return SimpleNamespace(values={})

    monkeypatch.setattr("app.supervisor.get_supervisor_graph", lambda: FakeGraph())

    result = await ChatService().get_session_plan("session-a", _user_context())

    assert result["status"] == "none"
    assert captured["configurable"] == {
        "thread_id": "session-a",
        "user_id": "user-a",
        "workspace_id": "workspace-a",
    }


@pytest.mark.asyncio
async def test_delete_does_not_touch_session_without_matching_owner():
    db = _CapturingSession()
    repository = SessionRepository(db)

    deleted = await repository.delete_session(
        "session-b",
        user_id="user-a",
        workspace_id="workspace-a",
    )

    assert deleted is False
    assert len(db.statements) == 1
