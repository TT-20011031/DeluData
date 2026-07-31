"""Service facade used by the DeluData Knowledge MCP server.

The MCP layer intentionally exposes only upload, task status, and retrieval.
Workspace, visibility, and permission decisions are bound to server-side
configuration so external agents do not need to manage knowledge scopes.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import re
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import aiofiles
import httpx
from sqlalchemy import select

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.core.db.tenant_mixin import set_current_workspace
from app.core.storage.service import get_storage_service
from app.core.utils.storage_path import normalize_storage_path
from app.models.common.context import UserContext
from app.models.common.enums import DocumentStatus
from app.models.knowledge.graph import File
from app.models.knowledge.ingestion_task import IngestionTask
from app.services.filesystem_service import FilesystemService
from app.services.ingestion_service import IngestionService
from app.services.task_queue_service import TaskQueueService
from app.services.workspace_knowledge_governance_service import (
    WorkspaceKnowledgeGovernanceService,
)
from app.skills.doc_skill import DocSkill


ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".md", ".txt"}
MCP_DEFAULT_EVIDENCE_TOKEN_BUDGET = 9000
MCP_MIN_EVIDENCE_TOKEN_BUDGET = 2000
MCP_MAX_EVIDENCE_TOKEN_BUDGET = 16000
_EXPLICIT_FILE_PATTERN = re.compile(
    r"《([^》]+?\.(?:pdf|docx?|xlsx?|pptx?|md|txt))》"
    r"|([^\s，。；;：:（）()《》]+?\.(?:pdf|docx?|xlsx?|pptx?|md|txt))",
    re.IGNORECASE,
)


def _safe_mcp_evidence_token_budget(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return MCP_DEFAULT_EVIDENCE_TOKEN_BUDGET
    return min(
        max(parsed, MCP_MIN_EVIDENCE_TOKEN_BUDGET),
        MCP_MAX_EVIDENCE_TOKEN_BUDGET,
    )


def _extract_explicit_file_names(query: str) -> list[str]:
    text = unicodedata.normalize("NFKC", str(query or ""))
    names: list[str] = []
    for match in _EXPLICIT_FILE_PATTERN.finditer(text):
        candidate = str(match.group(1) or match.group(2) or "").strip()
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def _normalize_file_lookup_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", "", normalized).casefold()


def _match_explicit_file_ids(
    explicit_file_names: list[str],
    file_rows: list[tuple[Any, str]],
) -> list[str]:
    file_ids_by_name: dict[str, list[str]] = {}
    for file_id, file_name in file_rows:
        normalized_file_name = _normalize_file_lookup_name(file_name)
        if not file_id or not normalized_file_name:
            continue
        file_ids_by_name.setdefault(normalized_file_name, []).append(str(file_id))

    matched_file_ids: list[str] = []
    for explicit_name in explicit_file_names:
        normalized_explicit_name = _normalize_file_lookup_name(explicit_name)
        if not normalized_explicit_name:
            continue
        candidates: list[tuple[int, int, str]] = []
        for normalized_file_name in file_ids_by_name:
            start = normalized_explicit_name.find(normalized_file_name)
            while start >= 0:
                candidates.append(
                    (start, start + len(normalized_file_name), normalized_file_name)
                )
                start = normalized_explicit_name.find(
                    normalized_file_name,
                    start + 1,
                )

        selected_spans: list[tuple[int, int, str]] = []
        for start, end, normalized_file_name in sorted(
            candidates,
            key=lambda item: (-(item[1] - item[0]), item[0]),
        ):
            if any(start < kept_end and end > kept_start for kept_start, kept_end, _ in selected_spans):
                continue
            selected_spans.append((start, end, normalized_file_name))

        for _start, _end, normalized_file_name in sorted(selected_spans):
            for file_id in file_ids_by_name[normalized_file_name]:
                if file_id not in matched_file_ids:
                    matched_file_ids.append(file_id)
    return matched_file_ids


@dataclass(frozen=True)
class MappedSource:
    local_path: str
    filename: str
    content_type: Optional[str]
    cleanup: bool = False


class KnowledgeMcpError(ValueError):
    """Expected MCP-facing error with a user-safe message."""


class KnowledgeMcpService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.mcp_settings = self.settings.mcp

    def user_context(self) -> UserContext:
        return UserContext(
            user_id=self.mcp_settings.knowledge_user_id,
            workspace_id=self.mcp_settings.knowledge_workspace_id,
            allowed_tables=["*"],
            role=self.mcp_settings.knowledge_role,
            dept_id=self.mcp_settings.knowledge_dept_id,
            data_scope=1 if self.mcp_settings.knowledge_role == "admin" else 4,
        )

    async def upload_documents(
        self,
        documents: list[dict[str, Any]],
        *,
        wait_until_ready: bool = False,
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        if not documents:
            raise KnowledgeMcpError("documents must not be empty")
        if len(documents) > 20:
            raise KnowledgeMcpError("at most 20 documents can be uploaded in one call")

        ctx = self.user_context()
        set_current_workspace(ctx.workspace_id)
        results: list[dict[str, Any]] = []

        for item in documents:
            results.append(await self._upload_one(item, ctx))

        if wait_until_ready:
            task_ids = [r["task_id"] for r in results if r.get("task_id")]
            if task_ids:
                await self._wait_for_tasks(task_ids, ctx.workspace_id, timeout_seconds)
                status_payload = await self.get_task_status(task_ids=task_ids)
                by_task = {task["task_id"]: task for task in status_payload["tasks"]}
                for result in results:
                    task_id = result.get("task_id")
                    if task_id in by_task:
                        result["task"] = by_task[task_id]

        return {
            "workspace_id": ctx.workspace_id,
            "documents": results,
        }

    async def _upload_one(self, item: dict[str, Any], ctx: UserContext) -> dict[str, Any]:
        name = str(item.get("name") or "").strip()
        source = item.get("source")
        if not name:
            raise KnowledgeMcpError("document.name is required")
        if not isinstance(source, dict):
            raise KnowledgeMcpError(f"document.source is required for {name}")

        mapped = await self._materialize_source(name, source)
        storage_path = ""
        try:
            suffix = Path(mapped.filename).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                raise KnowledgeMcpError(
                    f"unsupported file type: {suffix or '(none)'}; allowed: {sorted(ALLOWED_EXTENSIONS)}"
                )

            doc_id = str(item.get("document_id") or uuid.uuid4())
            file_size = Path(mapped.local_path).stat().st_size
            storage_service = get_storage_service()
            object_key = storage_service.build_document_object_key(
                ctx.workspace_id,
                doc_id,
                mapped.filename,
            )
            storage_path = await storage_service.upload_file(
                mapped.local_path,
                object_key,
                content_type=mapped.content_type,
            )

            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            visibility = self._configured_visibility()
            dept_id = self.mcp_settings.knowledge_dept_id
            folder_id = str(item.get("folder_id") or self.mcp_settings.knowledge_folder_id or "").strip() or None

            db_manager = get_async_db_manager()
            async with db_manager.session_scope() as session:
                filesystem = FilesystemService(session, workspace_id=ctx.workspace_id)
                governance = WorkspaceKnowledgeGovernanceService(session)
                await governance.ensure_upload_allowed(
                    ctx.workspace_id,
                    incoming_bytes=file_size,
                    lock_workspace=True,
                )
                await filesystem.create_file_record(
                    {
                        "id": doc_id,
                        "name": name,
                        "description": str(item.get("description") or metadata.get("description") or ""),
                        "folder_id": folder_id,
                        "storage_path": normalize_storage_path(storage_path),
                        "file_type": suffix.lstrip("."),
                        "file_size": file_size,
                        "status": DocumentStatus.PROCESSING.value,
                        "user_id": ctx.user_id,
                        "workspace_id": ctx.workspace_id,
                        "visibility": visibility,
                        "dept_id": dept_id,
                        "owner_id": ctx.user_id,
                        "document_type": metadata.get("document_type"),
                        "business_domain": metadata.get("business_domain"),
                        "confidentiality_level": metadata.get("confidentiality_level"),
                        "effective_from": self._parse_datetime(metadata.get("effective_from")),
                        "effective_until": self._parse_datetime(metadata.get("effective_until")),
                        "external_ref": metadata.get("external_ref"),
                    },
                    commit=False,
                )

                task_id: Optional[str] = None
                if self.settings.rag.ingest_use_db_queue:
                    task = await TaskQueueService(session).enqueue_task(
                        file_id=doc_id,
                        workspace_id=ctx.workspace_id,
                        user_id=ctx.user_id,
                        payload_json={
                            "file_path": normalize_storage_path(storage_path),
                            "filename": name,
                            "target_dept_id": str(dept_id) if dept_id is not None else None,
                            "visibility": visibility,
                            "source": "mcp_knowledge_upload",
                        },
                        max_attempts=self.settings.rag.ingest_task_max_attempts,
                    )
                    task_id = task.id
                else:
                    asyncio.create_task(
                        self._process_inline(
                            doc_id=doc_id,
                            storage_path=storage_path,
                            filename=name,
                            user_context=ctx,
                            visibility=visibility,
                            target_dept_id=str(dept_id) if dept_id is not None else None,
                        )
                    )

            return {
                "document_id": doc_id,
                "name": name,
                "status": DocumentStatus.PROCESSING.value,
                "task_id": task_id,
                "file_size": file_size,
            }
        except Exception:
            if storage_path:
                await get_storage_service().delete(storage_path)
            raise
        finally:
            if mapped.cleanup:
                Path(mapped.local_path).unlink(missing_ok=True)

    async def _process_inline(
        self,
        *,
        doc_id: str,
        storage_path: str,
        filename: str,
        user_context: UserContext,
        visibility: str,
        target_dept_id: Optional[str],
    ) -> None:
        try:
            await IngestionService().process_document(
                doc_id=doc_id,
                file_path=storage_path,
                user_context=user_context,
                filename=filename,
                target_dept_id=target_dept_id,
                visibility=visibility,
            )
        except Exception:
            # IngestionService already marks the file error; keep the background
            # task from surfacing an unhandled exception in the MCP server.
            pass

    async def get_task_status(
        self,
        *,
        task_ids: Optional[list[str]] = None,
        document_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        ctx = self.user_context()
        set_current_workspace(ctx.workspace_id)
        tasks: list[dict[str, Any]] = []
        seen: set[str] = set()

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            if task_ids:
                stmt = select(IngestionTask).where(
                    IngestionTask.workspace_id == ctx.workspace_id,
                    IngestionTask.id.in_(task_ids),
                )
                for task in (await session.execute(stmt)).scalars().all():
                    tasks.append(self._task_to_dict(task))
                    seen.add(task.id)

            if document_ids:
                stmt = (
                    select(IngestionTask)
                    .where(
                        IngestionTask.workspace_id == ctx.workspace_id,
                        IngestionTask.file_id.in_(document_ids),
                    )
                    .order_by(IngestionTask.created_at.desc())
                )
                for task in (await session.execute(stmt)).scalars().all():
                    if task.id not in seen:
                        tasks.append(self._task_to_dict(task))
                        seen.add(task.id)

                file_stmt = select(File).where(
                    File.workspace_id == ctx.workspace_id,
                    File.id.in_(document_ids),
                    File.is_deleted.is_(False),
                )
                files = (await session.execute(file_stmt)).scalars().all()
                task_file_ids = {task["document_id"] for task in tasks}
                for file_record in files:
                    if file_record.id in task_file_ids:
                        continue
                    tasks.append(self._file_status_to_dict(file_record))

        missing_task_ids = [task_id for task_id in (task_ids or []) if task_id not in seen]
        return {
            "workspace_id": ctx.workspace_id,
            "tasks": tasks,
            "missing_task_ids": missing_task_ids,
        }

    async def retrieve(
        self,
        *,
        query: str,
        top_k: Optional[int] = None,
        deep_search: bool = False,
        include_images: bool = True,
        max_chars_per_chunk: int = 3000,
    ) -> dict[str, Any]:
        if not query or not query.strip():
            raise KnowledgeMcpError("query is required")

        ctx = self.user_context()
        set_current_workspace(ctx.workspace_id)
        safe_top_k = max(1, min(int(top_k or self.mcp_settings.knowledge_default_top_k), 20))
        chunks = await DocSkill().query_knowledge_base(
            query=query.strip(),
            user_context=ctx,
            top_k=safe_top_k,
            include_images=bool(include_images),
            deep_search=bool(deep_search),
        )

        file_ids = {
            str((getattr(chunk, "metadata", None) or {}).get("file_id") or "")
            for chunk in chunks
        }
        file_ids.discard("")
        file_names = await self._load_file_display_names(ctx.workspace_id, file_ids)

        results = []
        context_blocks = []
        for idx, chunk in enumerate(chunks, start=1):
            metadata = dict(getattr(chunk, "metadata", None) or {})
            content = str(getattr(chunk, "content", "") or "")
            if max_chars_per_chunk > 0:
                content = content[:max_chars_per_chunk]
            file_id = str(metadata.get("file_id") or "")
            stored_source_file = str(metadata.get("source_file") or getattr(chunk, "source_file", "") or "")
            original_file_name = str(
                file_names.get(file_id)
                or metadata.get("display_file_name")
                or stored_source_file
                or file_id
            )
            page_numbers = self._parse_page_numbers(metadata)
            reference = f"[{idx}]"
            result = {
                "rank": idx,
                "citation_number": idx,
                "reference": reference,
                "chunk_id": str(getattr(chunk, "chunk_id", "") or metadata.get("chunk_id") or ""),
                "document_id": file_id,
                "original_file_name": original_file_name,
                "source_file": original_file_name,
                "stored_source_file": stored_source_file,
                "page_numbers": page_numbers,
                "score": float(getattr(chunk, "score", 0.0) or 0.0),
                "rerank_score": getattr(chunk, "rerank_score", None),
                "content": content,
                "metadata": {
                    "header_path": metadata.get("header_path"),
                    "summary": metadata.get("summary"),
                    "type": metadata.get("type"),
                },
            }
            results.append(result)

            location = f"，页码：{','.join(map(str, page_numbers))}" if page_numbers else ""
            source = f"{original_file_name or file_id}{location}"
            context_blocks.append(
                f"{reference} 原始文件名：{source}\n"
                f"引用编号：{reference}\n"
                f"相关正文：\n{content}"
            )

        return {
            "workspace_id": ctx.workspace_id,
            "query": query,
            "answer_context": "\n\n".join(context_blocks),
            "answer_context_items": [
                {
                    "citation_number": item["citation_number"],
                    "reference": item["reference"],
                    "document_id": item["document_id"],
                    "original_file_name": item["original_file_name"],
                    "page_numbers": item["page_numbers"],
                    "chunk_id": item["chunk_id"],
                    "content": item["content"],
                }
                for item in results
            ],
            "results": results,
            "citations": [
                {
                    "citation_number": item["citation_number"],
                    "reference": item["reference"],
                    "id": f"source_{item['rank']}",
                    "document_id": item["document_id"],
                    "original_file_name": item["original_file_name"],
                    "source_file": item["source_file"],
                    "stored_source_file": item["stored_source_file"],
                    "page_numbers": item["page_numbers"],
                    "chunk_id": item["chunk_id"],
                }
                for item in results
            ],
        }

    async def ask(
        self,
        *,
        query: str,
        reply_model_key: Optional[str] = None,
        deep_search: bool = False,
        session_id: Optional[str] = None,
        top_k: int = 20,
        evidence_token_budget: int = MCP_DEFAULT_EVIDENCE_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        if not query or not query.strip():
            raise KnowledgeMcpError("query is required")

        normalized_model_key = str(reply_model_key or "").strip().lower() or None
        if normalized_model_key and normalized_model_key not in {"flash", "plus", "max"}:
            raise KnowledgeMcpError("reply_model_key must be one of flash, plus, or max")

        raw_top_k = int(top_k or 20)
        safe_top_k = 20 if raw_top_k < 1 else min(raw_top_k, 20)
        safe_evidence_token_budget = _safe_mcp_evidence_token_budget(
            evidence_token_budget
        )

        ctx = self.user_context()
        set_current_workspace(ctx.workspace_id)
        required_file_ids = await self._resolve_required_file_ids(
            ctx.workspace_id,
            _extract_explicit_file_names(query),
            query,
        )
        required_file_ids = required_file_ids[:safe_top_k]

        from app.services.chat_service import ChatService

        chat_service = ChatService()
        result = await chat_service.start_new_session(
            message=query.strip(),
            user_context=ctx,
            session_id=session_id,
            reply_model_key=normalized_model_key,
            execution_mode="auto",
            deep_search=bool(deep_search),
            mcp_ask_evidence_top_k=safe_top_k,
            mcp_ask_evidence_token_budget=safe_evidence_token_budget,
            mcp_ask_required_file_ids=required_file_ids,
            mcp_ask_preserve_related_candidates=bool(required_file_ids),
        )

        final_answer = str(result.get("message") or "")
        final_status = str(result.get("status") or "")
        if final_status != "completed":
            final_state = await chat_service.confirm_and_execute(
                str(result.get("session_id") or ""),
                str(result.get("plan_id") or ""),
                ctx,
                result.get("steps") or None,
            )
            if isinstance(final_state, dict):
                final_answer = str(final_state.get("final_answer") or final_answer)
                final_status = str(final_state.get("plan_status") or final_status)

        session_plan = await chat_service.get_session_plan(
            str(result.get("session_id") or ""),
            ctx,
        )
        execution_results = session_plan.get("execution_results") or []
        citations = self._extract_ask_citations(execution_results)

        return {
            "workspace_id": ctx.workspace_id,
            "query": query,
            "answer": final_answer,
            "status": final_status or session_plan.get("status"),
            "session_id": result.get("session_id"),
            "plan_id": result.get("plan_id"),
            "summary": result.get("summary"),
            "steps": session_plan.get("steps") or result.get("steps") or [],
            "selected_skill_name": result.get("selected_skill_name"),
            "citations": citations,
        }

    async def _resolve_required_file_ids(
        self,
        workspace_id: str,
        explicit_file_names: list[str],
        raw_query: str = "",
    ) -> list[str]:
        normalized_raw_query = str(raw_query or "")
        raw_query_mentions_file = bool(
            re.search(
                r"\.(?:pdf|docx?|xlsx?|pptx?|md|txt)(?=$|[^A-Za-z0-9])",
                normalized_raw_query,
                re.IGNORECASE,
            )
        )
        lookup_texts = list(explicit_file_names)
        if raw_query_mentions_file:
            lookup_texts.append(normalized_raw_query)
        if not workspace_id or not any(text.strip() for text in lookup_texts):
            return []

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            stmt = select(File.id, File.name).where(
                File.workspace_id == workspace_id,
                File.is_deleted.is_(False),
            )
            rows = (await session.execute(stmt)).all()
        return _match_explicit_file_ids(lookup_texts, list(rows))

    @staticmethod
    def _extract_ask_citations(execution_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        seen: set[tuple[str, str, tuple[int, ...], str]] = set()
        for execution_result in execution_results:
            if not isinstance(execution_result, dict):
                continue
            meta = execution_result.get("meta") or {}
            for item in meta.get("final_context") or []:
                if not isinstance(item, dict) or item.get("used") is False:
                    continue
                document_id = str(item.get("file_id") or "")
                file_name = str(item.get("file_name") or item.get("title") or document_id)
                if not document_id and not file_name:
                    continue
                raw_pages = item.get("page_numbers")
                if not isinstance(raw_pages, list):
                    raw_pages = [item.get("page_number")] if item.get("page_number") is not None else []
                page_numbers: list[int] = []
                for page in raw_pages:
                    try:
                        page_numbers.append(int(page))
                    except (TypeError, ValueError):
                        continue
                chunk_id = str(item.get("chunk_id") or "")
                dedupe_key = (document_id, file_name, tuple(page_numbers), chunk_id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                citation_number = len(citations) + 1
                citations.append(
                    {
                        "citation_number": citation_number,
                        "reference": f"[{citation_number}]",
                        "document_id": document_id,
                        "original_file_name": file_name,
                        "source_file": file_name,
                        "page_numbers": page_numbers,
                        "chunk_id": chunk_id,
                    }
                )
        return citations

    async def _load_file_display_names(
        self,
        workspace_id: str,
        file_ids: set[str],
    ) -> dict[str, str]:
        if not file_ids:
            return {}

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            stmt = select(File.id, File.name).where(
                File.workspace_id == workspace_id,
                File.id.in_(sorted(file_ids)),
            )
            rows = (await session.execute(stmt)).all()
        return {
            str(file_id): str(name or "")
            for file_id, name in rows
            if file_id and name
        }

    async def _materialize_source(self, name: str, source: dict[str, Any]) -> MappedSource:
        kind = str(source.get("kind") or "").strip().lower()
        raw_filename = self._safe_filename(str(source.get("filename") or name))
        content_type = source.get("content_type") or source.get("mime_type") or mimetypes.guess_type(raw_filename)[0]
        filename = self._filename_with_extension(
            raw_filename,
            content_type=content_type,
            kind=kind,
        )
        suffix = Path(filename).suffix or ".txt"
        content_type = content_type or mimetypes.guess_type(filename)[0]

        if kind in {"text", "plain_text"}:
            content = str(source.get("content") or source.get("text") or "")
            path = await self._write_temp(content.encode("utf-8"), suffix=suffix)
            return MappedSource(path, filename, content_type or "text/plain", cleanup=True)

        if kind == "base64":
            raw = str(source.get("base64") or source.get("content_base64") or "")
            if not raw:
                raise KnowledgeMcpError(f"base64 source is empty for {name}")
            path = await self._write_temp(base64.b64decode(raw), suffix=suffix)
            return MappedSource(path, filename, content_type, cleanup=True)

        if kind in {"http_url", "url"}:
            url = str(source.get("url") or "").strip()
            if not url:
                raise KnowledgeMcpError(f"http_url source.url is required for {name}")
            headers = source.get("headers") if isinstance(source.get("headers"), dict) else None
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                body = response.content
                content_type = content_type or response.headers.get("content-type")
            if not Path(raw_filename).suffix:
                filename = self._filename_with_extension(
                    raw_filename,
                    content_type=content_type,
                    kind=kind,
                )
                suffix = Path(filename).suffix or suffix
            path = await self._write_temp(body, suffix=suffix)
            return MappedSource(path, filename, content_type, cleanup=True)

        if kind in {"local_path", "path"}:
            if not self.mcp_settings.knowledge_allow_local_paths:
                raise KnowledgeMcpError(
                    "local_path sources are disabled; set MCP_KNOWLEDGE_ALLOW_LOCAL_PATHS=true to enable"
                )
            path = Path(str(source.get("path") or "")).resolve()
            self._ensure_local_path_allowed(path)
            if not path.exists() or not path.is_file():
                raise KnowledgeMcpError(f"local file not found: {path}")
            return MappedSource(str(path), filename or path.name, content_type, cleanup=False)

        raise KnowledgeMcpError(
            "source.kind must be one of text, base64, http_url, or local_path"
        )

    async def _write_temp(self, data: bytes, *, suffix: str) -> str:
        fd, path = tempfile.mkstemp(prefix="deludata_mcp_upload_", suffix=suffix)
        os.close(fd)
        async with aiofiles.open(path, "wb") as handle:
            await handle.write(data)
        return path

    def _ensure_local_path_allowed(self, path: Path) -> None:
        raw_dirs = str(self.mcp_settings.knowledge_allowed_local_dirs or "").strip()
        if not raw_dirs:
            return
        allowed_dirs = [Path(p).resolve() for p in raw_dirs.split(";") if p.strip()]
        for allowed_dir in allowed_dirs:
            try:
                path.relative_to(allowed_dir)
                return
            except ValueError:
                continue
        raise KnowledgeMcpError(f"local path is outside MCP_KNOWLEDGE_ALLOWED_LOCAL_DIRS: {path}")

    def _configured_visibility(self) -> str:
        visibility = str(self.mcp_settings.knowledge_visibility or "dept").strip().lower()
        if visibility not in {"public", "dept", "private"}:
            return "dept"
        return visibility

    @staticmethod
    def _safe_filename(name: str) -> str:
        filename = Path(name or "document.txt").name
        return filename.replace("..", "").replace("/", "_").replace("\\", "_").strip() or "document.txt"

    @staticmethod
    def _filename_with_extension(filename: str, *, content_type: Optional[str], kind: str) -> str:
        if Path(filename).suffix:
            return filename
        mime = str(content_type or "").split(";", 1)[0].strip().lower()
        ext_by_mime = {
            "application/pdf": ".pdf",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "text/markdown": ".md",
            "text/plain": ".txt",
        }
        if mime in ext_by_mime:
            return f"{filename}{ext_by_mime[mime]}"
        if kind in {"text", "plain_text"}:
            return f"{filename}.txt"
        return filename

    @staticmethod
    def _parse_datetime(raw: Any) -> Optional[datetime]:
        if not raw:
            return None
        if isinstance(raw, datetime):
            return raw
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _parse_page_numbers(metadata: dict[str, Any]) -> list[int]:
        raw = metadata.get("page_numbers") or metadata.get("page_number") or ""
        if isinstance(raw, int):
            return [raw]
        if isinstance(raw, list):
            values = raw
        else:
            values = str(raw).replace(";", ",").split(",")
        pages: list[int] = []
        for value in values:
            try:
                page = int(str(value).strip())
            except ValueError:
                continue
            if page > 0 and page not in pages:
                pages.append(page)
        return pages

    @staticmethod
    def _task_to_dict(task: IngestionTask) -> dict[str, Any]:
        return {
            "task_id": task.id,
            "document_id": task.file_id,
            "task_status": task.status,
            "file_status": None,
            "stage": task.stage,
            "progress": int(task.progress or 0),
            "detail": task.detail_json or {},
            "error_message": task.error_message,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        }

    @staticmethod
    def _file_status_to_dict(file_record: File) -> dict[str, Any]:
        return {
            "task_id": None,
            "document_id": file_record.id,
            "task_status": None,
            "file_status": file_record.status,
            "stage": "completed" if file_record.status == DocumentStatus.INDEXED.value else file_record.status,
            "progress": 100 if file_record.status == DocumentStatus.INDEXED.value else 0,
            "detail": {"chunk_count": int(file_record.chunk_count or 0)},
            "error_message": file_record.error_message,
            "created_at": file_record.created_at.isoformat() if file_record.created_at else None,
            "started_at": None,
            "updated_at": file_record.updated_at.isoformat() if file_record.updated_at else None,
            "finished_at": file_record.processed_at.isoformat() if file_record.processed_at else None,
        }

    async def _wait_for_tasks(self, task_ids: list[str], workspace_id: str, timeout_seconds: int) -> None:
        deadline = asyncio.get_running_loop().time() + max(1, timeout_seconds)
        terminal = {"succeeded", "failed", "cancelled"}
        while asyncio.get_running_loop().time() < deadline:
            db_manager = get_async_db_manager()
            async with db_manager.session_scope() as session:
                stmt = select(IngestionTask.status).where(
                    IngestionTask.workspace_id == workspace_id,
                    IngestionTask.id.in_(task_ids),
                )
                statuses = [row[0] for row in (await session.execute(stmt)).all()]
            if statuses and all(status in terminal for status in statuses):
                return
            await asyncio.sleep(1.5)
