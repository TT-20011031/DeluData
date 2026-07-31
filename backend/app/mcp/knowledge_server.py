"""DeluData Knowledge MCP server.

Run from the backend directory:

    python -m app.mcp.knowledge_server

The server exposes four agent-facing capabilities:
upload documents, check ingestion status, retrieve evidence, and run the full
knowledge question-answering workflow.
"""

from __future__ import annotations

import logging
import platform
from typing import Any, Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.services.knowledge_mcp_service import KnowledgeMcpError, KnowledgeMcpService

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional in minimal MCP envs
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

logger = logging.getLogger("deludata.knowledge_mcp")


class KnowledgeMcpDocumentMetadata(BaseModel):
    """Optional governance metadata stored with the uploaded document."""

    model_config = ConfigDict(extra="forbid")

    document_type: Optional[str] = Field(
        default=None,
        description="Document category, for example manual, policy, contract, report, or faq.",
    )
    business_domain: Optional[str] = Field(
        default=None,
        description="Business domain used for later filtering and governance.",
    )
    confidentiality_level: Optional[str] = Field(
        default=None,
        description="Confidentiality label such as public, internal, confidential, or restricted.",
    )
    external_ref: Optional[str] = Field(
        default=None,
        description="Caller-side stable id for deduplication or traceability.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Short description of the document content.",
    )
    effective_from: Optional[str] = Field(
        default=None,
        description="Optional ISO-8601 start time for document validity.",
    )
    effective_until: Optional[str] = Field(
        default=None,
        description="Optional ISO-8601 end time for document validity.",
    )


class KnowledgeMcpDocumentSource(BaseModel):
    """Where the MCP server should read the document bytes or text from."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["base64", "text", "http_url", "local_path"] = Field(
        description=(
            "Source type. For PDF uploads from another agent, use base64 with "
            "base64=<PDF bytes encoded as base64> and mime_type=application/pdf."
        ),
    )
    filename: Optional[str] = Field(
        default=None,
        description="Original filename including extension, for example product-manual.pdf.",
    )
    mime_type: Optional[str] = Field(
        default=None,
        description="MIME type of the file, for example application/pdf.",
    )
    content_type: Optional[str] = Field(
        default=None,
        description="Alias for mime_type; used when mime_type is omitted.",
    )
    base64: Optional[str] = Field(
        default=None,
        description="Required when kind=base64. Base64-encoded file bytes.",
    )
    content_base64: Optional[str] = Field(
        default=None,
        description="Alias for base64; used when base64 is omitted.",
    )
    content: Optional[str] = Field(
        default=None,
        description="Required when kind=text. Plain text or markdown content.",
    )
    text: Optional[str] = Field(
        default=None,
        description="Alias for content; used when content is omitted.",
    )
    url: Optional[str] = Field(
        default=None,
        description="Required when kind=http_url. Public or signed URL to download.",
    )
    headers: Optional[dict[str, str]] = Field(
        default=None,
        description="Optional HTTP headers for kind=http_url downloads.",
    )
    path: Optional[str] = Field(
        default=None,
        description=(
            "Required when kind=local_path. Server-local path; disabled unless "
            "MCP_KNOWLEDGE_ALLOW_LOCAL_PATHS=true."
        ),
    )


class KnowledgeMcpDocument(BaseModel):
    """One document to upload into the server-bound DeluData workspace."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="Display name stored in the knowledge base, including extension when possible.",
        examples=["product-manual.pdf"],
    )
    source: KnowledgeMcpDocumentSource = Field(
        description="Document content source. PDF uploads should use kind=base64.",
    )
    document_id: Optional[str] = Field(
        default=None,
        description="Optional caller-provided UUID. Omit to let DeluData generate one.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional human-readable document description.",
    )
    folder_id: Optional[str] = Field(
        default=None,
        description="Optional DeluData folder id. Omit to use the MCP server default folder.",
    )
    metadata: Optional[KnowledgeMcpDocumentMetadata] = Field(
        default=None,
        description="Optional governance and traceability metadata.",
    )


class KnowledgeMcpSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings().mcp
        token = str(settings.knowledge_remote_token or "").strip()
        if not token:
            return JSONResponse(
                {"error": "mcp_remote_token_not_configured"},
                status_code=503,
            )
        expected = f"Bearer {token}"
        if request.headers.get("authorization", "") != expected:
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        allowed_origins = _allowed_origins()
        origin = request.headers.get("origin")
        if allowed_origins and origin and origin not in allowed_origins:
            return JSONResponse({"error": "origin_forbidden"}, status_code=403)

        return await call_next(request)


def _allowed_origins() -> list[str]:
    raw = str(get_settings().mcp.knowledge_allowed_origins or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _transport_security_settings() -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "agent.pro.deluagent.com",
            "agent.pro.deluagent.com:*",
            "127.0.0.1:*",
            "localhost:*",
            "knowledge-mcp:*",
            "knowledge-mcp:8020",
        ],
        allowed_origins=_allowed_origins(),
    )


mcp = FastMCP(
    "DeluData-Knowledge",
    host=get_settings().mcp.knowledge_http_host,
    port=int(get_settings().mcp.knowledge_http_port),
    stateless_http=True,
    json_response=True,
    transport_security=_transport_security_settings(),
)


def _service() -> KnowledgeMcpService:
    return KnowledgeMcpService()


def _error_payload(exc: Exception) -> dict[str, Any]:
    logger.warning("MCP tool failed: %s", exc, exc_info=not isinstance(exc, KnowledgeMcpError))
    return {
        "ok": False,
        "error": {
            "type": exc.__class__.__name__,
            "message": str(exc),
        },
    }


@mcp.tool()
async def delu_kb_upload_documents(
    documents: list[KnowledgeMcpDocument],
    wait_until_ready: bool = False,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Upload documents into the bound DeluData workspace and start RAG ingestion.

    Each document uses:
    {
      "name": "manual.pdf",
      "source": {"kind": "base64", "base64": "...", "mime_type": "application/pdf"},
      "description": "optional",
      "metadata": {
        "document_type": "manual",
        "business_domain": "maintenance",
        "confidentiality_level": "internal",
        "external_ref": "optional-stable-id"
      }
    }

    Supported source.kind values: text, base64, http_url, local_path.
    local_path is disabled unless MCP_KNOWLEDGE_ALLOW_LOCAL_PATHS=true.
    """
    try:
        document_payloads = [document.model_dump(exclude_none=True) for document in documents]
        payload = await _service().upload_documents(
            document_payloads,
            wait_until_ready=wait_until_ready,
            timeout_seconds=timeout_seconds,
        )
        return {"ok": True, **payload}
    except Exception as exc:  # noqa: BLE001 - MCP tools should return structured failures
        return _error_payload(exc)


@mcp.tool()
async def delu_kb_get_tasks(
    task_ids: Optional[list[str]] = None,
    document_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Get ingestion/vectorization task status for task ids or document ids."""
    try:
        payload = await _service().get_task_status(
            task_ids=task_ids,
            document_ids=document_ids,
        )
        return {"ok": True, **payload}
    except Exception as exc:  # noqa: BLE001
        return _error_payload(exc)


@mcp.tool()
async def delu_kb_retrieve(
    query: str,
    top_k: Optional[int] = None,
    deep_search: bool = False,
    include_images: bool = True,
    max_chars_per_chunk: int = 3000,
) -> dict[str, Any]:
    """Retrieve relevant DeluData knowledge evidence for another agent to answer with.

    The caller does not choose workspace, permissions, visibility, folders, or
    departments. Those are fixed by the MCP server configuration.
    """
    try:
        payload = await _service().retrieve(
            query=query,
            top_k=top_k,
            deep_search=deep_search,
            include_images=include_images,
            max_chars_per_chunk=max_chars_per_chunk,
        )
        return {"ok": True, **payload}
    except Exception as exc:  # noqa: BLE001
        return _error_payload(exc)


@mcp.tool()
async def delu_kb_ask(
    query: str,
    reply_model_key: Optional[Literal["flash", "plus", "max"]] = None,
    deep_search: bool = False,
    session_id: Optional[str] = None,
    top_k: int = 20,
    evidence_token_budget: int = 9000,
) -> dict[str, Any]:
    """Ask the bound DeluData knowledge agent and return its final answer.

    Use this when the caller wants DeluData to perform retrieval and answer
    generation itself. The caller only sends the user question; workspace,
    permissions, visibility, folders, and departments are fixed by server-side
    MCP configuration.
    """
    try:
        payload = await _service().ask(
            query=query,
            reply_model_key=reply_model_key,
            deep_search=deep_search,
            session_id=session_id,
            top_k=top_k,
            evidence_token_budget=evidence_token_budget,
        )
        return {"ok": True, **payload}
    except Exception as exc:  # noqa: BLE001
        return _error_payload(exc)


async def _shutdown() -> None:
    try:
        await get_async_db_manager().dispose()
    except Exception:
        logger.debug("failed to dispose async db manager", exc_info=True)


def _configure_logging() -> None:
    settings = get_settings()
    level_name = str(getattr(settings.log, "level", "INFO")).strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def build_http_app() -> Starlette:
    allowed_origins = _allowed_origins()
    app = mcp.streamable_http_app()
    app.add_middleware(KnowledgeMcpSecurityMiddleware)
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Mcp-Session-Id", "mcp-session-id"],
            expose_headers=["Mcp-Session-Id", "mcp-session-id"],
        )
    return app


http_app = build_http_app()


if __name__ == "__main__":
    _configure_logging()
    transport = str(get_settings().mcp.knowledge_transport or "stdio").strip().lower()
    if transport in {"http", "streamable-http", "streamable_http"}:
        import uvicorn

        uvicorn.run(
            "app.mcp.knowledge_server:http_app",
            host=get_settings().mcp.knowledge_http_host,
            port=int(get_settings().mcp.knowledge_http_port),
            log_level=str(get_settings().log.level or "info").lower(),
        )
    else:
        if platform.system() == "Windows":
            import asyncio

            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        mcp.run()
