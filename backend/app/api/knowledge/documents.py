"""
知识库 API - 文档路由

处理文档的 CRUD、上传、编辑、下载
"""
import os
import uuid
import asyncio
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from typing import Literal, Optional

import aiofiles

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from app.config import get_settings
from app.core.storage.service import get_storage_service
from app.core.security.auth import User
from app.core.security.rbac_deps import CheckPerm
from app.core.db.database import get_async_db_context, get_async_db_manager
get_current_admin = CheckPerm("knowledge:manage")
from app.core.utils.storage_path import is_oss_path, normalize_storage_path, resolve_storage_path
from app.models.common.context import UserContext
from app.services.filesystem_service import FilesystemService
from app.services.ingestion_service import IngestionService
from app.services.graph_service import GraphService
from app.services.task_queue_service import TaskQueueService
from app.services.workspace_knowledge_governance_service import (
    WorkspaceKnowledgeGovernanceService,
)

from .deps import get_user_context, get_filesystem_service, get_ingestion_service, get_graph_service
from .schemas import (
    BatchDeleteRequest,
    DescriptionUpdate,
    DocumentStatus,
    FileAccessUrlResponse,
    FileEditRequest,
    FileMetadataUpdate,
    FileResponse as FileInfoResponse,
    MoveRequest,
    RenameRequest,
    UploadResponse,
)

router = APIRouter(tags=["documents"])
logger = logging.getLogger(__name__)

_INGESTION_SEMAPHORE: Optional[asyncio.Semaphore] = None
_CANCEL_REQUEST_MARKER = "__cancel_requested__"
_CANCEL_POLL_INTERVAL_SEC = 1.0


def _format_storage_upload_error(exc: Exception) -> str:
    raw = str(exc)
    lowered = raw.lower()
    if any(
        marker in lowered
        for marker in (
            "nameresolutionerror",
            "failed to resolve",
            "temporary failure",
            "max retries exceeded",
            "connection",
            "connect timeout",
            "read timeout",
            "timed out",
        )
    ):
        return "对象存储连接失败，请稍后重试；如果连续失败，请检查服务器 DNS/网络或 OSS Endpoint 配置"
    if "invalid" in lowered and ("access" in lowered or "token" in lowered or "key" in lowered):
        return "对象存储凭证无效或已过期，请更新 OSS 访问凭证"
    if "securitytoken" in lowered or "sts" in lowered:
        return "对象存储临时凭证无效或已过期，请更新 OSS Security Token"
    return f"对象存储上传失败: {raw}"


def _get_ingestion_semaphore() -> asyncio.Semaphore:
    """
    获取文档入库并发限制器。
    默认值来自 RAG_INGEST_MAX_CONCURRENT_DOCS，避免 OCR/向量化阶段并发过高。
    """
    global _INGESTION_SEMAPHORE
    if _INGESTION_SEMAPHORE is None:
        limit = max(1, int(get_settings().rag.ingest_max_concurrent_docs))
        _INGESTION_SEMAPHORE = asyncio.Semaphore(limit)
    return _INGESTION_SEMAPHORE


async def _load_workspace_governance_config_snapshot(workspace_id: str):
    """
    在独立只读 Session 中读取治理配置，避免污染当前写事务的 Session 状态。

    SQLAlchemy 2.x 在首次 SELECT 后会自动进入事务态；如果随后在同一 Session 上再调用
    begin()，会触发 ``A transaction is already begun on this Session``。
    """
    async with get_async_db_context() as governance_db:
        governance_service = WorkspaceKnowledgeGovernanceService(governance_db)
        return await governance_service.get_workspace_config(workspace_id)


async def _mark_cancel_requested(file_id: str) -> None:
    """
    写入跨进程可见的取消标记。

    说明：不再依赖进程内字典，避免多 worker 场景下“取消请求不可见”问题。
    """
    db_manager = get_async_db_manager()
    try:
        async with db_manager.session_scope() as session:
            filesystem = FilesystemService(session)
            file_record = await filesystem.get_file(file_id)
            if file_record is None:
                return
            if file_record.status == DocumentStatus.PROCESSING.value:
                await filesystem.update_file_status(
                    file_id,
                    DocumentStatus.PROCESSING.value,
                    error=_CANCEL_REQUEST_MARKER,
                )
    except Exception as e:
        logger.warning("写入取消标记失败: file_id=%s err=%s", file_id, e)


async def _watch_cancel_requested(file_id: str, cancel_event: Event) -> None:
    """
    轮询数据库取消标记并同步到本进程 Event。

    用于让正在运行的 OCR/入库任务在多进程部署下也能及时感知取消。
    """
    db_manager = get_async_db_manager()
    while not cancel_event.is_set():
        try:
            async with db_manager.session_scope() as session:
                filesystem = FilesystemService(session)
                file_record = await filesystem.get_file(file_id)
                if file_record is None:
                    # 文档记录已被删除，立即中止本地任务。
                    cancel_event.set()
                    return
                if (file_record.error_message or "").strip() == _CANCEL_REQUEST_MARKER:
                    cancel_event.set()
                    logger.info("检测到跨进程取消标记: %s", file_id)
                    return
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("轮询取消标记失败: file_id=%s err=%s", file_id, e)
        await asyncio.sleep(_CANCEL_POLL_INTERVAL_SEC)


async def _cancel_ingestion_task(file_id: str, workspace_id: Optional[str]) -> None:
    settings = get_settings()
    if settings.rag.ingest_use_db_queue:
        task_queue = TaskQueueService()
        active_task = await task_queue.get_active_task_for_file(
            file_id=file_id,
            workspace_id=workspace_id,
        )
        if active_task is not None:
            await task_queue.request_cancel(
                task_id=active_task.id,
                workspace_id=workspace_id,
            )
        return

    await _mark_cancel_requested(file_id)


# ========== 后台任务 ==========

async def process_document_task(
    doc_id: str,
    file_path: str,
    user_context: UserContext,
    filename: str,
    target_dept_id: Optional[str] = None,
    visibility: str = "dept",
    cancel_event: Optional[Event] = None,
):
    """
    后台文档处理任务
    
    [v2.2] 添加全局异常处理，确保错误被正确捕获并更新文档状态
    """
    db_manager = get_async_db_manager()
    semaphore = _get_ingestion_semaphore()
    runtime_cancel_event = cancel_event or Event()
    cancel_watch_task = asyncio.create_task(
        _watch_cancel_requested(doc_id, runtime_cancel_event)
    )
    try:
        if runtime_cancel_event.is_set():
            logger.info("后台文档处理已取消（开始前）: %s", doc_id)
            return

        # [稳态优化] 不在 OCR/向量化全过程持有 DB session，改为短事务编排。
        async with semaphore:
            ingestion = IngestionService()
            await ingestion.process_document(
                doc_id=doc_id,
                file_path=file_path,
                user_context=user_context,
                filename=filename,
                target_dept_id=target_dept_id,
                visibility=visibility,
                cancel_event=runtime_cancel_event,
            )
    except asyncio.CancelledError:
        runtime_cancel_event.set()
        logger.info("后台文档处理任务被取消: %s", doc_id)
        return
    except Exception as e:
        # [v2.2] 全局异常捕获：记录错误日志并更新文档状态
        logger.error(f"后台文档处理失败 [{doc_id}]: {e}", exc_info=True)
        try:
            async with db_manager.session_scope() as session:
                filesystem = FilesystemService(session)
                await filesystem.update_file_status(
                    doc_id, 
                    DocumentStatus.ERROR.value, 
                    error=str(e)
                )
        except Exception as update_err:
            logger.error(f"更新文档状态失败 [{doc_id}]: {update_err}")
    finally:
        cancel_watch_task.cancel()
        try:
            await cancel_watch_task
        except (Exception, asyncio.CancelledError):
            pass


async def update_document_vectors(
    file_id: str,
    file_path: str,
    user_context: UserContext,
    regenerate_description: bool = False,
    visibility: str = "dept",
    target_dept_id: Optional[str] = None,
    cancel_event: Optional[Event] = None,
):
    """更新文档向量（后台任务）"""
    runtime_cancel_event = cancel_event or Event()
    cancel_watch_task = asyncio.create_task(
        _watch_cancel_requested(file_id, runtime_cancel_event)
    )
    try:
        if runtime_cancel_event.is_set():
            logger.info("文档向量更新已取消（开始前）: %s", file_id)
            return

        semaphore = _get_ingestion_semaphore()
        async with semaphore:
            ingestion = IngestionService()
            await ingestion.update_document_vectors(
                file_id=file_id,
                file_path=file_path,
                user_context=user_context,
                regenerate_description=regenerate_description,
                visibility=visibility,
                target_dept_id=target_dept_id,
                cancel_event=runtime_cancel_event,
            )
    finally:
        cancel_watch_task.cancel()
        try:
            await cancel_watch_task
        except (Exception, asyncio.CancelledError):
            pass


# ========== 辅助函数 ==========

# [v2.3] 抽取到公共模块，遵循 DRY 原则
from app.core.utils.file_utils import detect_encoding


# ========== Range Request 支持 ==========

def _resolve_record_path(storage_path: Optional[str]) -> str:
    return resolve_storage_path(storage_path or "")


def _is_remote_record_path(storage_path: Optional[str]) -> bool:
    return is_oss_path(storage_path or "")


def _get_file_media_type(file_type: Optional[str]) -> str:
    mime_types = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc": "application/msword",
        "txt": "text/plain; charset=utf-8",
        "md": "text/markdown; charset=utf-8",
    }
    return mime_types.get((file_type or "").lower(), "application/octet-stream")


async def _stream_materialized_storage_file(storage_path: str, suffix: str, filename: str):
    storage_service = get_storage_service()
    async with storage_service.materialize(storage_path, suffix=suffix, filename=filename) as local_path:
        async with aiofiles.open(local_path, "rb") as source:
            while chunk := await source.read(1024 * 1024):
                yield chunk


async def _build_storage_redirect(
    storage_path: str,
    *,
    filename: str,
    inline: bool,
    cache_control: Optional[str] = None,
) -> RedirectResponse:
    storage_service = get_storage_service()
    signed_url = await storage_service.generate_signed_url(
        storage_path,
        filename=filename,
        inline=inline,
        cache_control=cache_control,
    )
    if not signed_url:
        raise HTTPException(status_code=404, detail="文件访问地址生成失败")
    return RedirectResponse(url=signed_url, status_code=307)


def _build_backend_file_url(request: Request, file_id: str, kind: Literal["raw", "download"]) -> str:
    """构建文件 URL，正确处理 HTTPS 代理。"""
    base = str(request.base_url).rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    if forwarded_proto == "https" and base.startswith("http://"):
        base = "https://" + base[7:]
    return f"{base}/api/knowledge/files/{file_id}/{kind}"


def parse_range_header(range_header: str, file_size: int) -> tuple[int, int] | None:
    """
    解析 HTTP Range 请求头
    
    Args:
        range_header: 如 "bytes=0-1023"
        file_size: 文件总大小
    
    Returns:
        (start, end) 元组，或加果解析失败则返回 None
    """
    if not range_header or not range_header.startswith('bytes='):
        return None
    
    try:
        range_spec = range_header[6:]  # 移除 "bytes=" 前缀
        
        if range_spec.startswith('-'):
            # 后缀范围："-500" 表示最后 500 字节
            suffix_length = int(range_spec[1:])
            start = max(0, file_size - suffix_length)
            end = file_size - 1
        elif range_spec.endswith('-'):
            # 前缀范围："500-" 表示从 500 字节开始到文件末尾
            start = int(range_spec[:-1])
            end = file_size - 1
        else:
            # 完整范围："0-1023"
            parts = range_spec.split('-')
            start = int(parts[0])
            end = int(parts[1])
        
        # 检检范围有效性
        if start < 0 or end < start or start >= file_size:
            return None
        
        # 确保 end 不超过文件大小
        end = min(end, file_size - 1)
        
        return (start, end)
    except (ValueError, IndexError):
        return None


async def stream_file_range(file_path: str, start: int, end: int, chunk_size: int = 1024 * 64):
    """
    异步流式读取文件指定范围
    
    Args:
        file_path: 文件路径
        start: 起始字节
        end: 结束字节（包含）
        chunk_size: 每次读取的块大小
    """
    async with aiofiles.open(file_path, 'rb') as f:
        await f.seek(start)
        remaining = end - start + 1
        
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            data = await f.read(read_size)
            if not data:
                break
            yield data
            remaining -= len(data)


PDF_PREVIEW_FULL_MAX_BYTES = 50 * 1024 * 1024
PDF_PREVIEW_SINGLE_PAGE_MAX_BYTES = 150 * 1024 * 1024
DOCX_PREVIEW_FULL_MAX_BYTES = 30 * 1024 * 1024


def resolve_preview_policy(file_type: Optional[str], file_size: int) -> str:
    normalized_type = (file_type or "").lower()
    if normalized_type == "pdf":
        if file_size > PDF_PREVIEW_SINGLE_PAGE_MAX_BYTES:
            return "download_only"
        if file_size > PDF_PREVIEW_FULL_MAX_BYTES:
            return "single_page"
        return "full"
    if normalized_type == "docx":
        if file_size > DOCX_PREVIEW_FULL_MAX_BYTES:
            return "download_only"
        return "full"
    return "full"


# ========== 路由 ==========

@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(""),
    folder_id: Optional[str] = Form(None),
    target_dept_id: Optional[str] = Form(None),
    visibility: str = Form("dept"),
    document_type: Optional[str] = Form(None),
    business_domain: Optional[str] = Form(None),
    confidentiality_level: Optional[str] = Form(None),
    effective_from: Optional[datetime] = Form(None),
    effective_until: Optional[datetime] = Form(None),
    external_ref: Optional[str] = Form(None),
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """上传文档"""
    # 权限校验
    final_dept_id = None
    if target_dept_id:
        if not any(code in current_user.permissions for code in ('*', 'knowledge:manage')):
            final_dept_id = str(current_user.department_id) if current_user.department_id else None
        else:
            final_dept_id = target_dept_id
    
    allowed_extensions = {".pdf", ".doc", ".docx", ".md", ".txt"}
    file_ext = os.path.splitext(file.filename or "")[1].lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="不支持的文件类型")

    doc_id = str(uuid.uuid4())
    
    settings = get_settings()
    storage_service = get_storage_service()
    governance_service = WorkspaceKnowledgeGovernanceService(filesystem.db)
    governance_config = await _load_workspace_governance_config_snapshot(
        current_user.workspace_id
    )
    temp_fd, temp_upload_path = tempfile.mkstemp(suffix=file_ext, prefix=f"upload_{doc_id}_")
    os.close(temp_fd)
    object_key = storage_service.build_document_object_key(
        current_user.workspace_id,
        doc_id,
        file.filename or f"{doc_id}{file_ext}",
    )
    storage_path = ""
    file_size = 0

    def ensure_stream_upload_size_allowed(current_size: int) -> None:
        decision = governance_service.evaluate_max_upload_file_size_allowed(
            governance_config.max_upload_file_size_bytes,
            incoming_bytes=current_size,
        )
        if decision.allowed:
            return
        raise HTTPException(
            status_code=413,
            detail=decision.message or decision.code or "forbidden",
        )

    effective_dept_id = None
    if final_dept_id:
        try:
            effective_dept_id = int(final_dept_id)
        except ValueError:
            pass
    elif current_user.department_id:
        effective_dept_id = current_user.department_id
    
    try:
        known_file_size = getattr(file, "size", None)
        if isinstance(known_file_size, int):
            ensure_stream_upload_size_allowed(known_file_size)

        # [v2.3] 流式写入，避免大文件一次性读入内存导致 OOM
        CHUNK_SIZE = 1024 * 1024  # 1MB
        async with aiofiles.open(temp_upload_path, 'wb') as out_file:
            while content := await file.read(CHUNK_SIZE):
                file_size += len(content)
                ensure_stream_upload_size_allowed(file_size)
                await out_file.write(content)

        async with filesystem.db.begin():
            await governance_service.ensure_upload_allowed(
                current_user.workspace_id,
                incoming_bytes=file_size,
                lock_workspace=True,
            )
            try:
                storage_path = await storage_service.upload_file(
                    temp_upload_path,
                    object_key,
                    content_type=file.content_type,
                )
            except Exception as storage_exc:
                raise HTTPException(
                    status_code=502,
                    detail=_format_storage_upload_error(storage_exc),
                ) from storage_exc
            await filesystem.create_file_record(
                {
                    "id": doc_id,
                    "name": name,
                    "description": description,
                    "folder_id": folder_id if folder_id else None,
                    "storage_path": normalize_storage_path(storage_path),
                    "file_type": file_ext.lstrip("."),
                    "file_size": file_size,
                    "status": DocumentStatus.PROCESSING.value,
                    "user_id": current_user.id,
                    "workspace_id": current_user.workspace_id,
                    "visibility": visibility,
                    "dept_id": effective_dept_id,
                    "owner_id": current_user.id,
                    "document_type": document_type,
                    "business_domain": business_domain,
                    "confidentiality_level": confidentiality_level,
                    "effective_from": effective_from,
                    "effective_until": effective_until,
                    "external_ref": external_ref,
                },
                commit=False,
            )
    except HTTPException:
        if storage_path:
            await storage_service.delete(storage_path)
        raise
    except Exception as e:
        if storage_path:
            await storage_service.delete(storage_path)
        if os.path.exists(temp_upload_path):
            await asyncio.to_thread(os.remove, temp_upload_path)
        raise HTTPException(status_code=400, detail=f"数据库记录创建失败: {str(e)}")
    finally:
        Path(temp_upload_path).unlink(missing_ok=True)

    task_id: Optional[str] = None
    if settings.rag.ingest_use_db_queue:
        task_queue = TaskQueueService(filesystem.db)
        task = await task_queue.enqueue_task(
            file_id=doc_id,
            workspace_id=current_user.workspace_id,
            user_id=current_user.id,
            payload_json={
                "file_path": normalize_storage_path(storage_path),
                "filename": name,
                "target_dept_id": final_dept_id,
                "visibility": visibility,
            },
            max_attempts=settings.rag.ingest_task_max_attempts,
        )
        task_id = task.id
    else:
        user_context = UserContext(
            user_id=current_user.id,
            workspace_id=current_user.workspace_id,
            allowed_tables=["*"],
            role=current_user.role,
            is_workspace_admin=current_user.is_workspace_admin,
        )
        asyncio.create_task(
            process_document_task(
                doc_id=doc_id,
                file_path=storage_path,
                user_context=user_context,
                filename=name,
                target_dept_id=final_dept_id,
                visibility=visibility,
                cancel_event=None,
            )
        )

    return UploadResponse(
        document_id=doc_id,
        name=name,
        status=DocumentStatus.PROCESSING.value,
        message="文档上传成功，正在处理",
        task_id=task_id,
    )


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """删除文档"""
    user_context = UserContext(
        user_id=current_user.id,
        workspace_id=current_user.workspace_id,
        allowed_tables=["*"],
        role=current_user.role,
        is_workspace_admin=current_user.is_workspace_admin,
    )

    await _cancel_ingestion_task(document_id, workspace_id=current_user.workspace_id)
    delete_result = await filesystem.delete_file(document_id, user_context)
    if not delete_result.get("success") and delete_result.get("delete_status") == "not_found":
        raise HTTPException(status_code=404, detail="文档不存在或删除失败")
    
    return {
        "success": bool(delete_result.get("success")),
        "delete_status": delete_result.get("delete_status"),
        "delete_op_id": delete_result.get("delete_op_id"),
    }


@router.post("/files/batch-delete")
async def batch_delete_files(
    request: BatchDeleteRequest,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """批量删除文件"""
    user_context = UserContext(
        user_id=current_user.id,
        workspace_id=current_user.workspace_id,
        allowed_tables=["*"],
        role=current_user.role,
        is_workspace_admin=current_user.is_workspace_admin,
    )

    await asyncio.gather(
        *[
            _cancel_ingestion_task(file_id, workspace_id=current_user.workspace_id)
            for file_id in request.ids
        ],
        return_exceptions=True,
    )
    deleted_count = await filesystem.delete_files_batch(request.ids, user_context)
    failed_count = max(0, len(request.ids) - deleted_count)
    
    return {
        "success": True, 
        "deleted_count": deleted_count,
        "failed_count": failed_count,
        "requested_count": len(request.ids)
    }


@router.get("/files/{file_id}/info", response_model=FileInfoResponse, summary="获取文件元数据")
async def get_file_info(
    file_id: str,
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """
    获取文件基本信息（名称、类型、状态等）

    用于前端在文件类型未知时（如 citation 缺少文件名）解析真实元数据。
    """
    file = await filesystem.get_file(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="文件不存在")

    file_size = file.file_size or 0
    if not file_size:
        storage_service = get_storage_service()
        file_size = await storage_service.get_size(file.storage_path or "")

    active_task_id: Optional[str] = None
    processing_stage: Optional[str] = None
    processing_progress: Optional[int] = None
    if get_settings().rag.ingest_use_db_queue:
        task_queue = TaskQueueService(filesystem.db)
        active_task = await task_queue.get_active_task_for_file(
            file_id=file.id,
            workspace_id=user_context.workspace_id,
        )
        if active_task is not None:
            active_task_id = active_task.id
            processing_stage = active_task.stage
            processing_progress = int(active_task.progress or 0)

    return FileInfoResponse(
        id=file.id,
        name=file.name,
        description=file.description,
        file_type=file.file_type,
        file_size=file_size,
        status=file.status,
        is_deleted=bool(getattr(file, "is_deleted", False)),
        delete_status=getattr(file, "delete_status", "active") or "active",
        delete_error=getattr(file, "delete_error", None),
        delete_op_id=getattr(file, "delete_op_id", None),
        chunk_count=file.chunk_count or 0,
        visibility=file.visibility or "dept",
        dept_id=file.dept_id,
        owner_id=file.owner_id,
        document_type=file.document_type,
        business_domain=file.business_domain,
        confidentiality_level=file.confidentiality_level,
        effective_from=file.effective_from,
        effective_until=file.effective_until,
        external_ref=file.external_ref,
        pageindex_status=file.pageindex_status,
        active_task_id=active_task_id,
        processing_stage=processing_stage,
        processing_progress=processing_progress,
        preview_policy=resolve_preview_policy(file.file_type, file_size),
        created_at=file.created_at,
        updated_at=file.updated_at,
    )


@router.put("/files/{file_id}/metadata", response_model=FileInfoResponse)
async def update_file_metadata(
    file_id: str,
    payload: FileMetadataUpdate,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service),
):
    """更新企业知识元数据。"""
    file = await filesystem.get_file(file_id)
    if not file or file.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=404, detail="文件不存在")

    if payload.visibility is not None:
        if payload.visibility not in {"public", "dept", "private"}:
            raise HTTPException(status_code=400, detail="visibility_invalid")
        file.visibility = payload.visibility
    if payload.department_id is not None:
        file.dept_id = payload.department_id
    if payload.owner_id is not None:
        file.owner_id = payload.owner_id
    for field in (
        "document_type",
        "business_domain",
        "confidentiality_level",
        "effective_from",
        "effective_until",
        "external_ref",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(file, field, value)

    await filesystem.db.commit()
    await filesystem.db.refresh(file)

    file_size = file.file_size or 0
    return FileInfoResponse(
        id=file.id,
        name=file.name,
        description=file.description,
        file_type=file.file_type,
        file_size=file_size,
        status=file.status,
        is_deleted=bool(getattr(file, "is_deleted", False)),
        delete_status=getattr(file, "delete_status", "active") or "active",
        delete_error=getattr(file, "delete_error", None),
        delete_op_id=getattr(file, "delete_op_id", None),
        chunk_count=file.chunk_count or 0,
        visibility=file.visibility or "dept",
        dept_id=file.dept_id,
        owner_id=file.owner_id,
        document_type=file.document_type,
        business_domain=file.business_domain,
        confidentiality_level=file.confidentiality_level,
        effective_from=file.effective_from,
        effective_until=file.effective_until,
        external_ref=file.external_ref,
        pageindex_status=file.pageindex_status,
        active_task_id=None,
        processing_stage=None,
        processing_progress=None,
        preview_policy=resolve_preview_policy(file.file_type, file_size),
        created_at=file.created_at,
        updated_at=file.updated_at,
    )


@router.get("/files/{file_id}/access-url", response_model=FileAccessUrlResponse)
async def get_file_access_url(
    file_id: str,
    request: Request,
    kind: Literal["raw", "download"] = Query("raw"),
    inline: bool = Query(True),
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service),
):
    file_record = await filesystem.get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")

    storage_path = file_record.storage_path or ""
    if kind == "raw":
        return {
            "url": _build_backend_file_url(request, file_id, kind),
            "requires_auth": True,
            "kind": kind,
            "expires_at": None,
        }
    if _is_remote_record_path(storage_path):
        storage_service = get_storage_service()
        expires_sec = storage_service.signed_url_ttl_sec
        cache_control = f"public, max-age={expires_sec}" if kind == "raw" else None
        signed_url = await storage_service.generate_signed_url(
            storage_path,
            filename=file_record.name if kind == "download" else None,
            inline=inline if kind == "raw" else False,
            expires=expires_sec,
            cache_control=cache_control,
        )
        if not signed_url:
            raise HTTPException(status_code=404, detail="文件访问地址生成失败")
        return {
            "url": signed_url,
            "requires_auth": False,
            "kind": kind,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_sec),
        }

    resolved_path = _resolve_record_path(storage_path)
    if not resolved_path or not os.path.exists(resolved_path):
        raise HTTPException(status_code=404, detail="文件源文件丢失")

    return {
        "url": _build_backend_file_url(request, file_id, kind),
        "requires_auth": True,
        "kind": kind,
        "expires_at": None,
    }


@router.get("/files/{file_id}/raw")
async def get_file_raw(
    file_id: str,
    request: Request,
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """
    获取文件原始内容（用于 DOCX/PDF 预览）
    
    直接返回二进制文件，前端使用 docx-preview 或 PDF.js 渲染
    支持 Range Request 用于大文件
    """
    file_record = await filesystem.get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    if _is_remote_record_path(file_record.storage_path):
        from urllib.parse import quote

        encoded_name = quote(file_record.name)
        return StreamingResponse(
            _stream_materialized_storage_file(
                file_record.storage_path or "",
                suffix=f".{file_record.file_type}" if file_record.file_type else "",
                filename=file_record.name,
            ),
            media_type=_get_file_media_type(file_record.file_type),
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
                "Cache-Control": "private, max-age=300",
            },
        )

    resolved_path = _resolve_record_path(file_record.storage_path)
    if not resolved_path or not os.path.exists(resolved_path):
        raise HTTPException(status_code=404, detail="文件源文件丢失")

    # 获取文件大小
    file_size = os.path.getsize(resolved_path)

    # MIME 类型映射
    mime_types = {
        'pdf': 'application/pdf',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'doc': 'application/msword',
        'txt': 'text/plain; charset=utf-8',
        'md': 'text/markdown; charset=utf-8'
    }
    media_type = mime_types.get(file_record.file_type, 'application/octet-stream')
    
    # 检查 Range 请求头
    range_header = request.headers.get('Range')
    
    if range_header:
        range_result = parse_range_header(range_header, file_size)
        
        if range_result is None:
            return Response(
                status_code=416,
                headers={'Content-Range': f'bytes */{file_size}'}
            )
        
        start, end = range_result
        content_length = end - start + 1
        
        from urllib.parse import quote
        encoded_name = quote(file_record.name)
        
        return StreamingResponse(
            stream_file_range(resolved_path, start, end),
            status_code=206,
            media_type=media_type,
            headers={
                'Content-Range': f'bytes {start}-{end}/{file_size}',
                'Content-Length': str(content_length),
                'Accept-Ranges': 'bytes',
                'Content-Disposition': f"inline; filename*=UTF-8''{encoded_name}"
            }
        )
    
    # 完整文件响应
    return FileResponse(
        resolved_path,
        filename=file_record.name,
        media_type=media_type,
        content_disposition_type='inline',
        headers={'Accept-Ranges': 'bytes'}
    )


@router.get("/files/{file_id}/content")
async def get_file_content(
    file_id: str,
    request: Request,
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service),
    ingestion: IngestionService = Depends(get_ingestion_service)
):
    """
    获取文件内容用于编辑（仅支持 txt/md）
    
    注意：DOCX/PDF 使用 /raw 端点获取原始文件进行预览
    """
    file_record = await filesystem.get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # [v2.5] DOCX/PDF 应使用 /raw 端点，此端点仅支持文本文件编辑
    if file_record.file_type in ('docx', 'pdf'):
        raise HTTPException(
            status_code=400, 
            detail=f"{file_record.file_type.upper()} 文件请使用 /raw 端点获取原始内容"
        )

    if file_record.file_type == 'doc':
        raise HTTPException(
            status_code=400,
            detail="不支持 .doc 格式，请另存为 .docx"
        )

    if _is_remote_record_path(file_record.storage_path):
        storage_service = get_storage_service()
        suffix = Path(file_record.name).suffix or f".{file_record.file_type or 'txt'}"
        try:
            async with storage_service.materialize(file_record.storage_path or "", suffix=suffix) as content_path:
                encoding = await asyncio.to_thread(detect_encoding, content_path)
                async with aiofiles.open(content_path, "r", encoding=encoding, errors="ignore") as f:
                    text = await f.read()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="file source missing")
        except Exception as exc:
            logger.warning("Failed to read remote text file: file_id=%s err=%s", file_id, exc)
            raise HTTPException(status_code=502, detail="failed to read file content")

        media_type = "text/markdown" if file_record.file_type == "md" else "text/plain"
        return PlainTextResponse(text, media_type=media_type)

    resolved_path = _resolve_record_path(file_record.storage_path)
    if not resolved_path or not os.path.exists(resolved_path):
        raise HTTPException(status_code=404, detail="文件源文件丢失")

    # 获取文件大小
    file_size = os.path.getsize(resolved_path)
    
    # 检查 Range 请求头
    range_header = request.headers.get('Range')
    
    if range_header:
        # 解析 Range 头
        range_result = parse_range_header(range_header, file_size)
        
        if range_result is None:
            # Range 无效，返回 416
            return Response(
                status_code=416,
                headers={'Content-Range': f'bytes */{file_size}'}
            )
        
        start, end = range_result
        content_length = end - start + 1
        
        # 返回 206 Partial Content
        return StreamingResponse(
            stream_file_range(resolved_path, start, end),
            status_code=206,
            media_type='application/pdf' if file_record.file_type == 'pdf' else 'application/octet-stream',
            headers={
                'Content-Range': f'bytes {start}-{end}/{file_size}',
                'Content-Length': str(content_length),
                'Accept-Ranges': 'bytes',
                'Content-Disposition': f'inline; filename="{file_record.name}"'
            }
        )
    
    # 没有 Range 请求，返回完整文件
    return FileResponse(
        resolved_path,
        filename=file_record.name,
        content_disposition_type='inline',
        headers={'Accept-Ranges': 'bytes'}
    )


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: str,
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """下载文件"""
    file_record = await filesystem.get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    if _is_remote_record_path(file_record.storage_path):
        return await _build_storage_redirect(
            file_record.storage_path or "",
            filename=file_record.name,
            inline=False,
        )

    resolved_path = _resolve_record_path(file_record.storage_path)
    if not resolved_path or not os.path.exists(resolved_path):
        raise HTTPException(status_code=404, detail="文件源文件丢失")

    return FileResponse(
        resolved_path,
        filename=file_record.name,
        media_type='application/octet-stream'
    )


@router.put("/files/{file_id}/description")
async def update_description(
    file_id: str,
    update: DescriptionUpdate,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """更新文件描述"""
    success = await filesystem.update_file_description(file_id, update.description)
    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"success": True}


@router.put("/files/{file_id}/name")
async def rename_file(
    file_id: str,
    request: RenameRequest,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """重命名文件"""
    success = await filesystem.rename_file(file_id, request.name)
    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"success": True}


@router.put("/files/{file_id}/move")
async def move_file(
    file_id: str,
    request: MoveRequest,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """移动文件"""
    success = await filesystem.move_file(file_id, request.parent_id)
    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"success": True}


@router.post("/files/{file_id}/reindex")
async def reindex_file(
    file_id: str,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service),
):
    """Rebuild knowledge chunks for one uploaded file."""
    settings = get_settings()
    file_record = await filesystem.get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="file_not_found")
    if file_record.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=404, detail="file_not_found")
    if file_record.is_deleted:
        raise HTTPException(status_code=404, detail="file_deleted")
    if not file_record.storage_path:
        raise HTTPException(status_code=404, detail="source_file_missing")

    await filesystem.update_file_status(file_id, DocumentStatus.PROCESSING.value)

    if settings.rag.ingest_use_db_queue:
        task_queue = TaskQueueService(filesystem.db)
        task = await task_queue.enqueue_task(
            file_id=file_id,
            workspace_id=current_user.workspace_id,
            user_id=current_user.id,
            payload_json={
                "task_kind": "reindex",
                "regenerate_description": True,
                "visibility": file_record.visibility or "dept",
                "target_dept_id": str(file_record.dept_id) if file_record.dept_id is not None else None,
                "source": "manual_reindex",
            },
            max_attempts=settings.rag.ingest_task_max_attempts,
        )
        return {
            "success": True,
            "message": "reindex_queued",
            "task_id": task.id,
        }

    user_context = UserContext(
        user_id=current_user.id,
        workspace_id=current_user.workspace_id,
        allowed_tables=["*"],
        role=current_user.role,
        is_workspace_admin=current_user.is_workspace_admin,
    )
    asyncio.create_task(
        update_document_vectors(
            file_id=file_id,
            file_path=file_record.storage_path or "",
            user_context=user_context,
            regenerate_description=True,
            visibility=file_record.visibility or "dept",
            target_dept_id=str(file_record.dept_id) if file_record.dept_id is not None else None,
            cancel_event=None,
        )
    )
    return {"success": True, "message": "reindex_started"}


@router.put("/files/{file_id}/edit")
async def edit_file(
    file_id: str,
    request: FileEditRequest,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service),
    ingestion: IngestionService = Depends(get_ingestion_service)
):
    """编辑文件并更新向量库"""
    settings = get_settings()
    file_record = await filesystem.get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    if file_record.file_type not in ['txt', 'md', 'docx']:
        raise HTTPException(status_code=400, detail="仅支持编辑 txt/md/docx 文件")
    
    storage_service = get_storage_service()
    resolved_path = _resolve_record_path(file_record.storage_path)
    if not _is_remote_record_path(file_record.storage_path) and (not resolved_path or not os.path.exists(resolved_path)):
        raise HTTPException(status_code=404, detail="文件源文件丢失")

    await filesystem.update_file_status(file_id, DocumentStatus.PROCESSING.value)
    
    try:
        suffix = Path(file_record.name).suffix or f".{file_record.file_type or 'txt'}"
        async with storage_service.materialize(file_record.storage_path or "", suffix=suffix) as editable_path:
            if file_record.file_type == 'docx':
                await ingestion.save_markdown_to_docx(editable_path, request.content)
            else:
                # [v2.3] 使用异步文件写入，避免阻塞事件循环
                from app.core.utils.file_utils import detect_encoding_async, write_file_async
                original_encoding = await detect_encoding_async(editable_path)
                await write_file_async(editable_path, request.content, original_encoding)
            await storage_service.replace_from_local_file(
                file_record.storage_path or "",
                editable_path,
            )
    except Exception as e:
        await filesystem.update_file_status(file_id, DocumentStatus.ERROR.value, error=str(e))
        raise HTTPException(status_code=500, detail=f"文件写入失败: {str(e)}")

    if settings.rag.ingest_use_db_queue:
        task_queue = TaskQueueService(filesystem.db)
        task = await task_queue.enqueue_task(
            file_id=file_id,
            workspace_id=current_user.workspace_id,
            user_id=current_user.id,
            payload_json={
                "task_kind": "reindex",
                "regenerate_description": True,
                "visibility": file_record.visibility or "dept",
                "target_dept_id": str(file_record.dept_id) if file_record.dept_id is not None else None,
                "source": "edit_file",
            },
            max_attempts=settings.rag.ingest_task_max_attempts,
        )
        return {
            "success": True,
            "message": "文件已更新，已进入重新索引队列",
            "task_id": task.id,
        }

    user_context = UserContext(
        user_id=current_user.id,
        workspace_id=current_user.workspace_id,
        allowed_tables=["*"],
        role=current_user.role,
        is_workspace_admin=current_user.is_workspace_admin,
    )

    asyncio.create_task(
        update_document_vectors(
            file_id=file_id,
            file_path=file_record.storage_path or "",
            user_context=user_context,
            regenerate_description=True,
            visibility=file_record.visibility or "dept",
            target_dept_id=str(file_record.dept_id) if file_record.dept_id is not None else None,
            cancel_event=None,
        )
    )

    return {"success": True, "message": "文件已更新，正在重新索引"}
