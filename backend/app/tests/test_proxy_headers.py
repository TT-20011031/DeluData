from fastapi import APIRouter, Request
from fastapi.testclient import TestClient

from app.api.knowledge.documents import _build_backend_file_url
from app.entrypoints.common import create_application


def test_build_backend_file_url_honors_forwarded_proto_header():
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "scheme": "http",
            "method": "GET",
            "path": "/api/knowledge/files/file-1/access-url",
            "raw_path": b"/api/knowledge/files/file-1/access-url",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"agent.deluagent.com"),
                (b"x-forwarded-proto", b"https"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("agent.deluagent.com", 80),
        }
    )

    url = _build_backend_file_url(request, "file-1", "raw")

    assert url == "https://agent.deluagent.com/api/knowledge/files/file-1/raw"


def test_create_application_uses_proxy_headers_for_https_requests():
    api_router = APIRouter()
    health_router = APIRouter()

    @api_router.get("/proxy-debug")
    async def proxy_debug(request: Request):
        return {
            "scheme": request.url.scheme,
            "base_url": str(request.base_url),
            "raw_url": _build_backend_file_url(request, "file-1", "raw"),
        }

    app = create_application(
        title="Proxy Test App",
        description="Proxy header regression test",
        api_router=api_router,
        health_router=health_router,
        runtime_mode="experience",
    )
    client = TestClient(app)

    response = client.get(
        "/api/proxy-debug",
        headers={"X-Forwarded-Proto": "https", "Host": "agent.deluagent.com"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scheme"] == "https"
    assert payload["base_url"] == "https://agent.deluagent.com/"
    assert payload["raw_url"] == "https://agent.deluagent.com/api/knowledge/files/file-1/raw"
