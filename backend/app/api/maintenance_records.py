"""Public maintenance record capture APIs."""

from __future__ import annotations

import asyncio
import html
import os
import tempfile
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from PIL import Image, ImageOps
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.api.knowledge.documents import process_document_task
from app.api.voice.router import TranscribeResponse
from app.config import get_settings
from app.core.security.auth import get_user_by_username, user_model_to_user
from app.core.storage.service import get_storage_service
from app.core.utils.storage_path import normalize_storage_path
from app.core.voice.asr import ASRErrorCode, get_asr_service
from app.models.common.context import UserContext
from app.models.common.enums import DocumentStatus
from app.models.knowledge.graph import Folder
from app.services.filesystem_service import FilesystemService
from app.services.task_queue_service import TaskQueueService
from app.services.workspace_knowledge_governance_service import (
    WorkspaceKnowledgeGovernanceService,
)

router = APIRouter(prefix="/public/maintenance-records", tags=["Public Maintenance Records"])

MAINTENANCE_USERNAME = "test1"
MAINTENANCE_FOLDER_NAME = "维修记录"
MAX_IMAGE_COUNT = 9
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
TRANSCRIBE_LIMIT_PER_MINUTE = 10
SUBMIT_LIMIT_PER_MINUTE = 5

_memory_rate_limits: dict[str, deque[float]] = defaultdict(deque)


class MaintenanceRecordResponse(BaseModel):
    document_id: str
    name: str
    status: str
    message: str
    task_id: Optional[str] = None
    folder_id: str
    image_count: int


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or "unknown"
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


async def _check_rate_limit(request: Request, *, action: str, limit: int) -> None:
    ip = _client_ip(request)
    key = f"maintenance:{action}:{ip}:{int(time.time() // 60)}"
    settings = get_settings()

    try:
        import redis.asyncio as redis

        client = redis.from_url(settings.redis.connection_url, decode_responses=True)
        try:
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, 90)
            if count > limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="maintenance_rate_limited",
                )
            return
        finally:
            await client.aclose()
    except HTTPException:
        raise
    except Exception:
        now = time.time()
        memory_key = f"{action}:{ip}"
        bucket = _memory_rate_limits[memory_key]
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="maintenance_rate_limited",
            )
        bucket.append(now)


async def _get_service_user(db: AsyncSession):
    user_db = await get_user_by_username(db, MAINTENANCE_USERNAME)
    if user_db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="maintenance_service_user_missing",
        )
    if user_db.disabled:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="maintenance_service_user_disabled",
        )
    return user_model_to_user(user_db)


def _format_created_at(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M:%S")


async def _ensure_public_maintenance_folder(
    db: AsyncSession,
    *,
    workspace_id: str,
    user_id: str,
) -> Folder:
    stmt = (
        select(Folder)
        .where(
            Folder.workspace_id == workspace_id,
            Folder.parent_id.is_(None),
            Folder.name == MAINTENANCE_FOLDER_NAME,
            Folder.visibility == "public",
        )
        .order_by(Folder.created_at.asc())
        .limit(1)
    )
    folder = (await db.execute(stmt)).scalars().first()
    if folder is not None:
        return folder

    governance = WorkspaceKnowledgeGovernanceService(db)
    await governance.ensure_action_allowed(workspace_id, "create_folder")
    folder = Folder(
        id=str(uuid.uuid4()),
        name=MAINTENANCE_FOLDER_NAME,
        parent_id=None,
        user_id=user_id,
        visibility="public",
        dept_id=None,
        owner_id=user_id,
        workspace_id=workspace_id,
    )
    db.add(folder)
    await db.flush()
    return folder


async def _read_and_validate_images(images: list[UploadFile]) -> list[tuple[str, bytes]]:
    if not images:
        raise HTTPException(status_code=400, detail="maintenance_images_required")
    if len(images) > MAX_IMAGE_COUNT:
        raise HTTPException(status_code=400, detail="maintenance_images_too_many")

    payloads: list[tuple[str, bytes]] = []
    for image in images:
        content_type = (image.content_type or "").lower()
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="maintenance_image_type_unsupported")

        data = await image.read()
        if not data:
            raise HTTPException(status_code=400, detail="maintenance_image_empty")
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="maintenance_image_too_large")

        try:
            from io import BytesIO

            with Image.open(BytesIO(data)) as source:
                source.verify()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="maintenance_image_invalid") from exc

        payloads.append((image.filename or "维修图片", data))
    return payloads


def _image_to_pdf_bytes(raw_bytes: bytes) -> tuple[bytes, int, int]:
    from io import BytesIO

    with Image.open(BytesIO(raw_bytes)) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.getchannel("A"))
            image = background

        output = BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)
        return output.getvalue(), image.width, image.height


def _append_html(page, cursor_y: float, html_text: str) -> float:
    import fitz

    rect = fitz.Rect(42, cursor_y, 553, 790)
    leftover, scale = page.insert_htmlbox(rect, html_text, scale_low=0.75)
    used_height = (rect.height - leftover) * scale
    return min(790, cursor_y + max(24, used_height) + 8)


def _generate_pdf_sync(
    *,
    output_path: str,
    created_at: datetime,
    description: str,
    image_payloads: list[tuple[str, bytes]],
) -> int:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    cursor_y = 42
    escaped_description = html.escape(description).replace("\n", "<br>")
    header_html = f"""
    <style>
      body {{ font-family: sans-serif; color: #111827; }}
      h1 {{ font-size: 28px; margin: 0 0 14px; }}
      p {{ font-size: 13px; line-height: 1.55; margin: 0 0 7px; }}
      .label {{ color: #4b5563; }}
      .section {{ font-size: 18px; font-weight: 700; margin-top: 18px; }}
      .desc {{ font-size: 14px; line-height: 1.7; white-space: pre-wrap; }}
    </style>
    <body>
      <h1>维修记录</h1>
      <p><span class="label">创建时间：</span>{html.escape(_format_created_at(created_at))}</p>
      <p><span class="label">提交来源：</span>公开维修采集页</p>
      <p><span class="label">图片数量：</span>{len(image_payloads)}</p>
      <p class="section">维修说明</p>
      <p class="desc">{escaped_description}</p>
      <p class="section">维修图片</p>
    </body>
    """
    cursor_y = _append_html(page, cursor_y, header_html)

    for index, (filename, raw_bytes) in enumerate(image_payloads, start=1):
        image_bytes, width, height = _image_to_pdf_bytes(raw_bytes)
        if cursor_y > 470:
            page = doc.new_page(width=595, height=842)
            cursor_y = 42

        caption = f"<p style='font-family:sans-serif;font-size:13px;color:#374151;'>维修图片 {index}：{html.escape(filename)}</p>"
        cursor_y = _append_html(page, cursor_y, caption)

        max_width = 511
        max_height = 300
        scale = min(max_width / max(width, 1), max_height / max(height, 1), 1.0)
        display_width = width * scale
        display_height = height * scale
        left = 42 + (max_width - display_width) / 2
        rect = fitz.Rect(left, cursor_y, left + display_width, cursor_y + display_height)
        page.insert_image(rect, stream=image_bytes, keep_proportion=True)
        cursor_y = rect.y1 + 30

    doc.save(output_path, deflate=True, garbage=4)
    doc.close()
    return os.path.getsize(output_path)


async def _write_temp_pdf(
    *,
    created_at: datetime,
    description: str,
    image_payloads: list[tuple[str, bytes]],
    document_id: str,
) -> tuple[str, int]:
    temp_fd, temp_path = tempfile.mkstemp(suffix=".pdf", prefix=f"maintenance_{document_id}_")
    os.close(temp_fd)
    try:
        pdf_size = await asyncio.to_thread(
            _generate_pdf_sync,
            output_path=temp_path,
            created_at=created_at,
            description=description,
            image_payloads=image_payloads,
        )
        return temp_path, pdf_size
    except Exception as exc:
        Path(temp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"maintenance_pdf_create_failed: {exc}") from exc


@router.post("/transcribe", response_model=TranscribeResponse, summary="公开维修语音转文字")
async def transcribe_public_maintenance_audio(
    request: Request,
    audio: UploadFile = File(..., description="音频文件 (WebM/WAV/MP3)"),
):
    """Transcribe maintenance audio from the public mobile capture page."""
    await _check_rate_limit(request, action="transcribe", limit=TRANSCRIBE_LIMIT_PER_MINUTE)

    audio_content = await audio.read()
    content_type = audio.content_type or ""
    if len(audio_content) == 0:
        return TranscribeResponse(code=ASRErrorCode.EMPTY_AUDIO, text="", msg="音频文件为空")
    if len(audio_content) > MAX_AUDIO_BYTES:
        return TranscribeResponse(
            code=ASRErrorCode.AUDIO_TOO_LARGE,
            text="",
            msg="音频文件过大，最大支持 10MB",
        )

    try:
        result = await get_asr_service().transcribe_file(audio_content, content_type)
        if result.success:
            return TranscribeResponse(code=0, text=result.text, msg="")
        return TranscribeResponse(
            code=result.error_code,
            text="",
            msg=result.user_message,
            error_detail=result.error_message,
        )
    except Exception as exc:
        return TranscribeResponse(
            code=ASRErrorCode.UNKNOWN_ERROR,
            text="",
            msg="语音识别失败",
            error_detail=str(exc),
        )


@router.post("", response_model=MaintenanceRecordResponse)
async def create_public_maintenance_record(
    request: Request,
    description: str = Form(...),
    images: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a public knowledge-base PDF maintenance record as the test1 service user."""
    await _check_rate_limit(request, action="submit", limit=SUBMIT_LIMIT_PER_MINUTE)

    clean_description = str(description or "").strip()
    if not clean_description:
        raise HTTPException(status_code=400, detail="maintenance_description_required")

    service_user = await _get_service_user(db)
    image_payloads = await _read_and_validate_images(images)
    settings = get_settings()
    storage_service = get_storage_service()
    filesystem = FilesystemService(db, workspace_id=service_user.workspace_id)
    governance = WorkspaceKnowledgeGovernanceService(db)
    document_id = str(uuid.uuid4())
    created_at = datetime.now()
    short_id = document_id.split("-")[0]
    external_ref = f"maintenance-{created_at.strftime('%Y%m%d%H%M%S')}-{short_id}"
    filename = f"维修记录-{created_at.strftime('%Y%m%d-%H%M%S')}-{short_id}.pdf"
    temp_pdf_path, pdf_size = await _write_temp_pdf(
        created_at=created_at,
        description=clean_description,
        image_payloads=image_payloads,
        document_id=document_id,
    )
    storage_path = ""

    try:
        await governance.ensure_upload_allowed(
            service_user.workspace_id,
            incoming_bytes=pdf_size,
            lock_workspace=False,
        )
        folder = await _ensure_public_maintenance_folder(
            db,
            workspace_id=service_user.workspace_id,
            user_id=service_user.id,
        )

        object_key = storage_service.build_document_object_key(
            service_user.workspace_id,
            document_id,
            filename,
        )
        storage_path = await storage_service.upload_file(
            temp_pdf_path,
            object_key,
            content_type="application/pdf",
        )

        await filesystem.create_file_record(
            {
                "id": document_id,
                "name": filename,
                "description": clean_description[:1000],
                "folder_id": folder.id,
                "storage_path": normalize_storage_path(storage_path),
                "file_type": "pdf",
                "file_size": pdf_size,
                "status": DocumentStatus.PROCESSING.value,
                "user_id": service_user.id,
                "workspace_id": service_user.workspace_id,
                "visibility": "public",
                "dept_id": None,
                "owner_id": service_user.id,
                "document_type": "maintenance_record",
                "business_domain": "maintenance",
                "external_ref": external_ref,
            },
            commit=False,
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        if storage_path:
            await storage_service.delete(storage_path)
        raise
    except Exception as exc:
        await db.rollback()
        if storage_path:
            await storage_service.delete(storage_path)
        raise HTTPException(status_code=400, detail=f"maintenance_record_create_failed: {exc}") from exc
    finally:
        Path(temp_pdf_path).unlink(missing_ok=True)

    task_id: Optional[str] = None
    if settings.rag.ingest_use_db_queue:
        task_queue = TaskQueueService(db)
        task = await task_queue.enqueue_task(
            file_id=document_id,
            workspace_id=service_user.workspace_id,
            user_id=service_user.id,
            payload_json={
                "file_path": normalize_storage_path(storage_path),
                "filename": filename,
                "target_dept_id": None,
                "visibility": "public",
            },
            max_attempts=settings.rag.ingest_task_max_attempts,
        )
        task_id = task.id
    else:
        user_context = UserContext(
            user_id=service_user.id,
            workspace_id=service_user.workspace_id,
            allowed_tables=["*"],
            role=service_user.role,
            dept_id=service_user.department_id,
            data_scope=service_user.data_scope,
        )
        asyncio.create_task(
            process_document_task(
                doc_id=document_id,
                file_path=storage_path,
                user_context=user_context,
                filename=filename,
                target_dept_id=None,
                visibility="public",
                cancel_event=None,
            )
        )

    return MaintenanceRecordResponse(
        document_id=document_id,
        name=filename,
        status=DocumentStatus.PROCESSING.value,
        message="maintenance_record_created",
        task_id=task_id,
        folder_id=folder.id,
        image_count=len(image_payloads),
    )
