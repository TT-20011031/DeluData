import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings
from app.api.knowledge.documents import get_file_access_url
import app.api.knowledge.documents as documents_module
from app.core.storage.service import StorageService
from app.core.utils.image_service import ImageService
import app.tools.doc_tool as doc_tool_module


def _build_request(path: str = "/api/knowledge/files/file_1/access-url") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 50000),
        "root_path": "",
    }
    return Request(scope)


def test_storage_service_generate_signed_url_oss_sets_response_cache_control(monkeypatch):
    service = StorageService()
    captured: dict[str, object] = {}

    class FakeBucket:
        def sign_url(self, method, key, expires, params=None, slash_safe=False):
            captured["method"] = method
            captured["key"] = key
            captured["expires"] = expires
            captured["params"] = params or {}
            captured["slash_safe"] = slash_safe
            return "https://oss.example.com/demo.pdf"

    monkeypatch.setattr(service, "_bucket_client", lambda: FakeBucket())

    url = service._generate_signed_url_oss(
        "oss://test-bucket/path/to/demo.pdf",
        "demo.pdf",
        True,
        1800,
        "public, max-age=1800",
    )

    assert url == "https://oss.example.com/demo.pdf"
    assert captured["method"] == "GET"
    assert captured["key"] == "path/to/demo.pdf"
    assert captured["expires"] == 1800
    assert captured["slash_safe"] is True
    assert captured["params"]["response-cache-control"] == "public, max-age=1800"
    assert "response-content-disposition" in captured["params"]


@pytest.mark.asyncio
async def test_get_file_access_url_includes_expires_at_for_remote_preview(monkeypatch):
    class FakeFilesystem:
        async def get_file(self, _file_id):
            return SimpleNamespace(
                storage_path="oss://test-bucket/path/to/demo.pdf",
                name="demo.pdf",
            )

    class FakeStorageService:
        signed_url_ttl_sec = 1800

        def __init__(self):
            self.calls = []

        async def generate_signed_url(self, storage_path, *, filename=None, inline=True, expires=None, cache_control=None):
            self.calls.append(
                {
                    "storage_path": storage_path,
                    "filename": filename,
                    "inline": inline,
                    "expires": expires,
                    "cache_control": cache_control,
                }
            )
            return "https://oss.example.com/demo.pdf"

    fake_storage = FakeStorageService()
    monkeypatch.setattr(documents_module, "get_storage_service", lambda: fake_storage)

    payload = await get_file_access_url(
        file_id="file_1",
        request=_build_request(),
        kind="raw",
        inline=True,
        user_context=SimpleNamespace(),
        filesystem=FakeFilesystem(),
    )

    assert payload["url"] == "https://oss.example.com/demo.pdf"
    assert payload["requires_auth"] is False
    assert payload["kind"] == "raw"
    assert payload["expires_at"] is not None
    assert fake_storage.calls == [
        {
            "storage_path": "oss://test-bucket/path/to/demo.pdf",
            "filename": None,
            "inline": True,
            "expires": 1800,
            "cache_control": "public, max-age=1800",
        }
    ]


@pytest.mark.asyncio
async def test_image_service_ensure_image_storage_path_async_reuses_remote_when_local_missing(tmp_path):
    service = ImageService()
    service.storage_root = tmp_path / "images"
    service.storage_root.mkdir(parents=True, exist_ok=True)

    class FakeStorageService:
        backend = "oss"

        def build_document_image_object_key(self, workspace_id: str, file_id: str, image_name: str) -> str:
            return f"workspaces/{workspace_id}/document-images/{file_id}/{image_name}"

        async def exists(self, storage_path: str) -> bool:
            return storage_path.endswith("/page_0001.png")

    service._storage_service = FakeStorageService()
    settings = get_settings()
    original_bucket = settings.oss.bucket
    settings.oss.bucket = "test-bucket"
    try:
        storage_path = await service.ensure_image_storage_path_async(
            workspace_id="ws_1",
            file_id="file_1",
            image_id="page_0001",
        )
    finally:
        settings.oss.bucket = original_bucket

    assert storage_path == "oss://test-bucket/workspaces/ws_1/document-images/file_1/page_0001.png"


@pytest.mark.asyncio
async def test_run_doc_task_skips_materialize_when_cached_page_image_exists(monkeypatch):
    settings = get_settings()
    original_transfer_mode = settings.rag.mm_image_transfer_mode
    settings.rag.mm_image_transfer_mode = "url"

    chunk = SimpleNamespace(
        content="cached page content",
        score=0.95,
        rerank_score=0.95,
        source_file="demo.pdf",
        page_number=7,
        chunk_id="chunk_7",
        metadata={"file_id": "file_1", "page_numbers": "7"},
    )

    class FakeDocSkill:
        async def query_knowledge_base(self, **_kwargs):
            return [chunk]

    class FakeResult:
        def __iter__(self):
            return iter(
                [
                    SimpleNamespace(
                        id="file_1",
                        name="demo",
                        file_type="pdf",
                        storage_path="oss://test-bucket/path/to/demo.pdf",
                        workspace_id="ws_1",
                    )
                ]
            )

    class FakeSession:
        async def execute(self, _stmt):
            return FakeResult()

    @asynccontextmanager
    async def fake_session_scope():
        yield FakeSession()

    class FakeDbManager:
        def session_scope(self):
            return fake_session_scope()

    class FakeStorageService:
        async def exists(self, _storage_path: str) -> bool:
            raise AssertionError("source pdf should not be checked when page image is already cached")

        @asynccontextmanager
        async def materialize(self, *_args, **_kwargs):
            raise AssertionError("cached page image should skip pdf materialize")
            yield ""

    class FakeImageService:
        async def ensure_image_storage_path_async(self, *, workspace_id: str, file_id: str, image_id: str):
            assert workspace_id == "ws_1"
            assert file_id == "file_1"
            assert image_id == "page_0007"
            return "oss://test-bucket/workspaces/ws_1/document-images/file_1/page_0007.png"

        async def render_pdf_page_image_async(self, **_kwargs):
            raise AssertionError("cached page image should skip page rendering")

        def get_image_base64(self, *_args, **_kwargs):
            return None

        async def get_image_base64_from_storage_path_async(self, _storage_path: str):
            return None

        async def get_multimodal_image_url_async(self, *, workspace_id: str, file_id: str, image_id: str, ttl: int | None = None):
            assert workspace_id == "ws_1"
            assert file_id == "file_1"
            assert image_id == "page_0007"
            assert ttl is not None
            return "https://oss.example.com/page_0007.png"

    monkeypatch.setattr("app.skills.doc_skill.DocSkill", FakeDocSkill)
    monkeypatch.setattr("app.core.db.database.get_async_db_manager", lambda: FakeDbManager())
    monkeypatch.setattr("app.core.utils.image_service.get_image_service", lambda: FakeImageService())
    monkeypatch.setattr(doc_tool_module, "get_storage_service", lambda: FakeStorageService())
    monkeypatch.setattr(doc_tool_module.doc_selection, "filter_effective_chunks", lambda chunks, _threshold: (chunks, len(chunks), 0))
    monkeypatch.setattr(doc_tool_module.doc_selection, "select_chunks_for_synthesizer", lambda **_kwargs: ([chunk], 42, False))
    monkeypatch.setattr(
        doc_tool_module.doc_selection,
        "select_page_images_for_synthesizer",
        lambda **_kwargs: [
            {
                "file_id": "file_1",
                "file_name": "demo",
                "page_number": 7,
                "chunk_score": 0.95,
                "support_score": 0.95,
                "kind": "主文件锚点页",
            }
        ],
    )
    monkeypatch.setattr(doc_tool_module.doc_selection, "chunks_to_df", lambda _chunks: [])
    monkeypatch.setattr(doc_tool_module.doc_selection, "log_image_alignment", lambda *_args, **_kwargs: None)

    try:
        result = await doc_tool_module.run_doc_task(
            query="demo query",
            user_context={
                "user_id": "user_1",
                "workspace_id": "ws_1",
                "allowed_tables": ["*"],
                "role": "admin",
            },
            doc_scope={"include_images": True},
        )
    finally:
        settings.rag.mm_image_transfer_mode = original_transfer_mode

    assert result.meta["mm_evidence"]["images"] == [
        {
            "file_id": "file_1",
            "file_name": "demo",
            "page_number": 7,
            "image_id": "page_0007",
            "kind": "主文件锚点页",
            "url": "https://oss.example.com/page_0007.png",
        }
    ]


@pytest.mark.asyncio
async def test_run_doc_task_materializes_each_file_only_once_for_missing_pages(monkeypatch):
    settings = get_settings()
    original_transfer_mode = settings.rag.mm_image_transfer_mode
    settings.rag.mm_image_transfer_mode = "url"

    chunks = [
        SimpleNamespace(
            content="cached page content",
            score=0.96,
            rerank_score=0.96,
            source_file="demo.pdf",
            page_number=7,
            chunk_id="chunk_7",
            metadata={"file_id": "file_1", "page_numbers": "7"},
        ),
        SimpleNamespace(
            content="missing page content",
            score=0.94,
            rerank_score=0.94,
            source_file="demo.pdf",
            page_number=8,
            chunk_id="chunk_8",
            metadata={"file_id": "file_1", "page_numbers": "8"},
        ),
    ]

    class FakeDocSkill:
        async def query_knowledge_base(self, **_kwargs):
            return chunks

    class FakeResult:
        def __iter__(self):
            return iter(
                [
                    SimpleNamespace(
                        id="file_1",
                        name="demo",
                        file_type="pdf",
                        storage_path="oss://test-bucket/path/to/demo.pdf",
                        workspace_id="ws_1",
                    )
                ]
            )

    class FakeSession:
        async def execute(self, _stmt):
            return FakeResult()

    @asynccontextmanager
    async def fake_session_scope():
        yield FakeSession()

    class FakeDbManager:
        def session_scope(self):
            return fake_session_scope()

    materialize_calls = 0
    source_exists_checks = 0

    class FakeStorageService:
        async def exists(self, storage_path: str) -> bool:
            nonlocal source_exists_checks
            assert storage_path == "oss://test-bucket/path/to/demo.pdf"
            source_exists_checks += 1
            return True

        @asynccontextmanager
        async def materialize(self, storage_path: str, *_args, **_kwargs):
            nonlocal materialize_calls
            assert storage_path == "oss://test-bucket/path/to/demo.pdf"
            materialize_calls += 1
            yield "D:/tmp/demo.pdf"

    rendered_image_ids: set[str] = set()
    render_calls: list[int] = []

    class FakeImageService:
        async def ensure_image_storage_path_async(self, *, workspace_id: str, file_id: str, image_id: str):
            assert workspace_id == "ws_1"
            assert file_id == "file_1"
            if image_id == "page_0007":
                return "oss://test-bucket/workspaces/ws_1/document-images/file_1/page_0007.png"
            if image_id in rendered_image_ids:
                return f"oss://test-bucket/workspaces/ws_1/document-images/file_1/{image_id}.png"
            return None

        async def render_pdf_page_image_async(self, *, pdf_path: str, file_id: str, page_number: int, dpi: int):
            assert pdf_path == "D:/tmp/demo.pdf"
            assert file_id == "file_1"
            assert dpi == int(settings.rag.page_image_dpi)
            render_calls.append(page_number)
            image_id = f"page_{page_number:04d}"
            rendered_image_ids.add(image_id)
            return image_id

        def get_image_base64(self, *_args, **_kwargs):
            return None

        async def get_image_base64_from_storage_path_async(self, _storage_path: str):
            return None

        async def get_multimodal_image_url_async(self, *, workspace_id: str, file_id: str, image_id: str, ttl: int | None = None):
            assert workspace_id == "ws_1"
            assert file_id == "file_1"
            assert ttl is not None
            return f"https://oss.example.com/{image_id}.png"

    monkeypatch.setattr("app.skills.doc_skill.DocSkill", FakeDocSkill)
    monkeypatch.setattr("app.core.db.database.get_async_db_manager", lambda: FakeDbManager())
    monkeypatch.setattr("app.core.utils.image_service.get_image_service", lambda: FakeImageService())
    monkeypatch.setattr(doc_tool_module, "get_storage_service", lambda: FakeStorageService())
    monkeypatch.setattr(doc_tool_module.doc_selection, "filter_effective_chunks", lambda found_chunks, _threshold: (found_chunks, len(found_chunks), 0))
    monkeypatch.setattr(doc_tool_module.doc_selection, "select_chunks_for_synthesizer", lambda **_kwargs: (chunks, 42, False))
    monkeypatch.setattr(
        doc_tool_module.doc_selection,
        "select_page_images_for_synthesizer",
        lambda **_kwargs: [
            {
                "file_id": "file_1",
                "file_name": "demo",
                "page_number": 7,
                "chunk_score": 0.96,
                "support_score": 0.96,
                "kind": "主文件锚点页",
            },
            {
                "file_id": "file_1",
                "file_name": "demo",
                "page_number": 8,
                "chunk_score": 0.94,
                "support_score": 0.94,
                "kind": "辅助页",
            },
        ],
    )
    monkeypatch.setattr(doc_tool_module.doc_selection, "chunks_to_df", lambda _chunks: [])
    monkeypatch.setattr(doc_tool_module.doc_selection, "log_image_alignment", lambda *_args, **_kwargs: None)

    try:
        result = await doc_tool_module.run_doc_task(
            query="demo query",
            user_context={
                "user_id": "user_1",
                "workspace_id": "ws_1",
                "allowed_tables": ["*"],
                "role": "admin",
            },
            doc_scope={"include_images": True},
        )
    finally:
        settings.rag.mm_image_transfer_mode = original_transfer_mode

    assert source_exists_checks == 1
    assert materialize_calls == 1
    assert render_calls == [8]
    assert result.meta["mm_evidence"]["images"] == [
        {
            "file_id": "file_1",
            "file_name": "demo",
            "page_number": 7,
            "image_id": "page_0007",
            "kind": "主文件锚点页",
            "url": "https://oss.example.com/page_0007.png",
        },
        {
            "file_id": "file_1",
            "file_name": "demo",
            "page_number": 8,
            "image_id": "page_0008",
            "kind": "辅助页",
            "url": "https://oss.example.com/page_0008.png",
        },
    ]
