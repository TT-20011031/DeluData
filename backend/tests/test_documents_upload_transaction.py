from contextlib import asynccontextmanager
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, ".")

import app.api.knowledge.documents as documents_module


class _FakeUploadFile:
    def __init__(self, filename: str, chunks: list[bytes], *, content_type: str = "text/plain"):
        self.filename = filename
        self.content_type = content_type
        self.size = sum(len(item) for item in chunks)
        self._chunks = list(chunks)

    async def read(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _FakeResult:
    def scalar_one_or_none(self):
        return None


class _FakeSessionTransaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        if self.session.in_tx:
            raise RuntimeError("A transaction is already begun on this Session")
        self.session.in_tx = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.session.in_tx = False
        return False


class _FakeSession:
    def __init__(self, label: str):
        self.label = label
        self.in_tx = False
        self.execute_calls = 0

    async def execute(self, _stmt):
        self.execute_calls += 1
        self.in_tx = True
        return _FakeResult()

    def begin(self):
        return _FakeSessionTransaction(self)


@pytest.mark.asyncio
async def test_upload_document_reads_governance_config_in_separate_session(monkeypatch):
    main_session = _FakeSession("main")
    snapshot_session = _FakeSession("snapshot")

    @asynccontextmanager
    async def _fake_db_context():
        yield snapshot_session

    class _FakeGovernanceService:
        def __init__(self, db):
            self.db = db

        async def get_workspace_config(self, workspace_id: str):
            assert workspace_id == "ws1"
            await self.db.execute("select governance")
            return SimpleNamespace(max_upload_file_size_bytes=None)

        def evaluate_max_upload_file_size_allowed(self, _limit, incoming_bytes: int):
            return SimpleNamespace(allowed=incoming_bytes <= 1024 * 1024, message=None, code=None)

        async def ensure_upload_allowed(self, workspace_id: str, *, incoming_bytes: int, lock_workspace: bool = False):
            assert workspace_id == "ws1"
            assert incoming_bytes == 5
            assert lock_workspace is True
            assert self.db.label == "main"
            assert self.db.in_tx is True

    class _FakeStorageService:
        def build_document_object_key(self, workspace_id: str, doc_id: str, filename: str) -> str:
            return f"{workspace_id}/{doc_id}/{filename}"

        async def upload_file(self, temp_upload_path: str, object_key: str, content_type: str | None = None) -> str:
            assert temp_upload_path
            assert object_key.endswith("/demo.txt")
            assert content_type == "text/plain"
            return "oss://bucket/ws1/demo.txt"

        async def delete(self, _storage_path: str) -> None:
            return None

    class _FakeTaskQueueService:
        def __init__(self, db):
            assert db is main_session

        async def enqueue_task(self, **kwargs):
            assert kwargs["workspace_id"] == "ws1"
            assert kwargs["user_id"] == "u1"
            return SimpleNamespace(id="task-1")

    class _FakeFilesystemService:
        def __init__(self):
            self.db = main_session
            self.created_records = []

        async def create_file_record(self, payload, commit: bool = False):
            self.created_records.append((payload, commit))

    monkeypatch.setattr(documents_module, "get_async_db_context", _fake_db_context)
    monkeypatch.setattr(documents_module, "WorkspaceKnowledgeGovernanceService", _FakeGovernanceService)
    monkeypatch.setattr(documents_module, "get_storage_service", lambda: _FakeStorageService())
    monkeypatch.setattr(documents_module, "TaskQueueService", _FakeTaskQueueService)
    monkeypatch.setattr(
        documents_module,
        "get_settings",
        lambda: SimpleNamespace(
            rag=SimpleNamespace(
                ingest_use_db_queue=True,
                ingest_task_max_attempts=3,
            )
        ),
    )

    filesystem = _FakeFilesystemService()
    current_user = SimpleNamespace(
        id="u1",
        workspace_id="ws1",
        role="admin",
        department_id=None,
    )
    upload_file = _FakeUploadFile("demo.txt", [b"hello"])

    response = await documents_module.upload_document(
        file=upload_file,
        name="demo",
        description="",
        folder_id=None,
        target_dept_id=None,
        visibility="dept",
        current_user=current_user,
        filesystem=filesystem,
    )

    assert snapshot_session.execute_calls == 1
    assert main_session.execute_calls == 0
    assert len(filesystem.created_records) == 1
    assert response.task_id == "task-1"
