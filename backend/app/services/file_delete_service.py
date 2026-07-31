"""File deletion workflow service.

Implements soft-delete state transitions and vector cleanup with retryable failure state.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage.service import get_storage_service
from app.models.common.enums import DeleteStatus
from app.models.knowledge.graph import DocumentImage, File

logger = logging.getLogger(__name__)


@dataclass
class FileDeleteResult:
    success: bool
    file_id: str
    delete_status: str
    delete_op_id: Optional[str]


async def purge_wiki_orphans_after_file_delete(
    *,
    workspace_id: Optional[str],
    file_id: str,
) -> None:
    """[治理 hook · 模块级] 文件软删后联动清孤儿 Wiki 页。

    幂等、失败仅 warning。设计成模块级函数，避免在单测中被
    `FileDeleteService` 类的顶层依赖（models→api→filesystem 循环 import）拖累。
    """
    if not workspace_id:
        return
    try:
        from app.services.wiki_service import get_wiki_service

        result = await get_wiki_service().purge_orphans_for_deleted_files(
            workspace_id=workspace_id,
            triggered_by=f"file_delete:{file_id}",
        )
        if result.get("purged_count"):
            logger.info(
                "auto wiki purge_orphans triggered by file=%s purged=%d",
                file_id,
                result["purged_count"],
            )
    except Exception as exc:  # pragma: no cover - 治理失败不阻断主流程
        logger.warning("auto wiki purge_orphans failed (file=%s): %s", file_id, exc)


class FileDeleteService:
    """Soft delete + vector delete state machine for File records."""

    def __init__(self, db: AsyncSession, doc_skill: Any):
        self.db = db
        self.doc_skill = doc_skill

    async def delete(self, file_record: File, user_context: Any) -> FileDeleteResult:
        if not file_record.is_deleted or file_record.delete_status == DeleteStatus.ACTIVE.value:
            await self._mark_pending(file_record)

        try:
            if user_context:
                await self.doc_skill.delete_document(file_record.id, user_context, raise_on_error=True)
        except Exception as exc:
            error_message = str(exc)
            await self._mark_failed(file_record, error_message)
            return FileDeleteResult(
                success=False,
                file_id=file_record.id,
                delete_status=file_record.delete_status,
                delete_op_id=file_record.delete_op_id,
            )

        await self._mark_vector_deleted(file_record)
        await self._cleanup_local_artifacts(file_record)
        await self.db.execute(delete(DocumentImage).where(DocumentImage.file_id == file_record.id))
        await self.db.commit()

        # [治理] 联动：文件软删成功后，把本工作区因此变成孤儿的 Wiki 实体页 archive 掉。
        # 使用 try/except 包裹，确保治理失败不影响主删除流程（最终一致性）。
        await self._purge_wiki_orphans_safe(file_record)

        return FileDeleteResult(
            success=True,
            file_id=file_record.id,
            delete_status=file_record.delete_status,
            delete_op_id=file_record.delete_op_id,
        )

    async def _purge_wiki_orphans_safe(self, file_record: File) -> None:
        """文件删除后联动清孤儿 Wiki 页；委托模块级函数便于单测。"""
        await purge_wiki_orphans_after_file_delete(
            workspace_id=file_record.workspace_id,
            file_id=file_record.id,
        )

    async def _mark_pending(self, file_record: File) -> None:
        file_record.is_deleted = True
        file_record.deleted_at = datetime.utcnow()
        file_record.delete_status = DeleteStatus.PENDING_VECTOR_DELETE.value
        file_record.delete_error = None
        file_record.delete_op_id = file_record.delete_op_id or str(uuid.uuid4())
        await self.db.commit()
        self._invalidate_soft_delete_cache(file_record.workspace_id)

    async def _mark_failed(self, file_record: File, error: str) -> None:
        file_record.is_deleted = True
        file_record.delete_status = DeleteStatus.DELETE_FAILED.value
        file_record.delete_error = (error or "")[:4000]
        file_record.delete_op_id = file_record.delete_op_id or str(uuid.uuid4())
        await self.db.commit()
        self._invalidate_soft_delete_cache(file_record.workspace_id)

    async def _mark_vector_deleted(self, file_record: File) -> None:
        file_record.is_deleted = True
        file_record.delete_status = DeleteStatus.VECTOR_DELETED.value
        file_record.delete_error = None
        file_record.deleted_at = file_record.deleted_at or datetime.utcnow()
        file_record.delete_op_id = file_record.delete_op_id or str(uuid.uuid4())
        await self.db.commit()
        self._invalidate_soft_delete_cache(file_record.workspace_id)

    async def _cleanup_local_artifacts(self, file_record: File) -> None:
        try:
            storage_service = get_storage_service()
            await storage_service.delete(file_record.storage_path or "")
        except Exception as exc:
            logger.error("Failed to delete source file %s: %s", file_record.storage_path, exc)

        try:
            from app.core.utils.image_service import get_image_service

            get_image_service().delete_file_images(file_record.id, workspace_id=file_record.workspace_id)
        except Exception as exc:
            logger.error("Failed to delete derived images for %s: %s", file_record.id, exc)

    def _invalidate_soft_delete_cache(self, workspace_id: Optional[str]) -> None:
        try:
            retriever = getattr(self.doc_skill, "retriever", None)
            if retriever and hasattr(retriever, "invalidate_soft_deleted_cache"):
                retriever.invalidate_soft_deleted_cache(workspace_id)
        except Exception as exc:
            logger.debug("invalidate soft-delete cache failed: %s", exc)
