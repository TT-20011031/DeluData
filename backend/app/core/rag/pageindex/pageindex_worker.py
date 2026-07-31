"""
PageIndex 后台构建 Worker（Phase 2A）

[DEPRECATED] 暂时废弃，功能已禁用
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from itertools import count
from typing import Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.events import emit_pageindex_status
from app.config import get_settings
from app.core.db.database import get_async_db_context
from app.core.rag.pageindex.tree_builder import get_tree_builder
from app.core.utils.storage_path import resolve_storage_path
from app.models.knowledge.graph import File
from app.services.filesystem_service import FilesystemService

logger = logging.getLogger(__name__)


RECOVERABLE_STATUSES: Tuple[str, ...] = ("queued", "building", "retrying")


class PageIndexTask(BaseModel):
    """PageIndex 构建任务
    
    [DEPRECATED] PageIndex 功能已禁用
    """

    file_id: str
    file_path: str
    workspace_id: str
    visibility: str
    owner_id: Optional[str] = None
    dept_id: Optional[int] = None
    priority: Optional[int] = Field(default=None, description="数值越小优先级越高")
    max_retries: Optional[int] = None
    source: Literal["upload", "reindex", "recover", "manual"] = "upload"


class PageIndexWorker:
    """
    PageIndex 后台 Worker
    
    [DEPRECATED] 暂时废弃，功能已禁用
    """
    def __init__(self):
        self._settings = get_settings().pageindex
        self._tree_builder = get_tree_builder()

        self._queue: asyncio.PriorityQueue[Tuple[int, int, PageIndexTask]] = asyncio.PriorityQueue()
        self._sequence = count()
        self._worker_id_sequence = count()
        self._worker_tasks: List[asyncio.Task] = []
        self._running = False

        self._queued_ids: Set[str] = set()
        self._running_ids: Set[str] = set()
        self._cancelled_ids: Set[str] = set()
        self._tasks_by_file_id: Dict[str, PageIndexTask] = {}
        self._state_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        worker_count = max(1, int(self._settings.max_concurrent_builds))
        for _ in range(worker_count):
            worker_index = next(self._worker_id_sequence)
            task = asyncio.create_task(self._worker_loop(worker_index))
            task.add_done_callback(self._on_worker_done)
            self._worker_tasks.append(task)

        logger.info("[PageIndexWorker] started with workers=%s", worker_count)

    async def stop(self) -> None:
        if not self._running and not self._worker_tasks:
            return

        self._running = False
        tasks_snapshot = list(self._worker_tasks)
        for task in tasks_snapshot:
            task.cancel()

        if tasks_snapshot:
            await asyncio.gather(*tasks_snapshot, return_exceptions=True)

        self._worker_tasks.clear()
        self._queue = asyncio.PriorityQueue()

        async with self._state_lock:
            self._queued_ids.clear()
            self._running_ids.clear()
            self._cancelled_ids.clear()
            self._tasks_by_file_id.clear()

        logger.info("[PageIndexWorker] stopped")

    async def enqueue(self, task: PageIndexTask) -> bool:
        task_obj = task if isinstance(task, PageIndexTask) else PageIndexTask.model_validate(task)
        task_obj = task_obj.model_copy(
            update={
                "priority": int(
                    task_obj.priority
                    if task_obj.priority is not None
                    else self._settings.default_task_priority
                ),
                "max_retries": int(
                    task_obj.max_retries
                    if task_obj.max_retries is not None
                    else self._settings.build_max_retries
                ),
            }
        )

        async with self._state_lock:
            if task_obj.file_id in self._queued_ids or task_obj.file_id in self._running_ids:
                logger.info("[PageIndexWorker] duplicate enqueue ignored: file_id=%s", task_obj.file_id)
                return False

            self._cancelled_ids.discard(task_obj.file_id)
            self._queued_ids.add(task_obj.file_id)
            self._tasks_by_file_id[task_obj.file_id] = task_obj
            await self._queue.put((task_obj.priority, next(self._sequence), task_obj))

        await self._mark_status(task_obj.file_id, "queued", None, task_obj.workspace_id)
        await self._emit_status(
            task_obj,
            status="queued",
            stage="queued",
            attempt=0,
            progress=0,
            message="任务已入队",
        )
        return True

    async def cancel(self, file_id: str, message: str = "任务已取消") -> bool:
        task_snapshot: Optional[PageIndexTask] = None
        async with self._state_lock:
            if file_id in self._queued_ids or file_id in self._running_ids:
                self._cancelled_ids.add(file_id)
                task_snapshot = self._tasks_by_file_id.get(file_id)
            else:
                return False

        workspace_id = task_snapshot.workspace_id if task_snapshot else await self._resolve_workspace_id(file_id)
        max_retries = int(task_snapshot.max_retries or 0) if task_snapshot else int(self._settings.build_max_retries)
        source = task_snapshot.source if task_snapshot else "manual"

        await self._mark_status(file_id, "cancelled", message, workspace_id)
        await self._emit_status_payload(
            file_id=file_id,
            workspace_id=workspace_id,
            source=source,
            status="cancelled",
            stage="cancelled",
            attempt=0,
            max_retries=max_retries,
            progress=100,
            message=message,
        )
        return True

    async def recover_stale_tasks(self) -> Dict[str, int]:
        stale_minutes = max(1, int(self._settings.building_stale_minutes))
        stale_before = datetime.now() - timedelta(minutes=stale_minutes)

        async with get_async_db_context() as session:
            stmt = select(File).where(
                File.pageindex_status.in_(RECOVERABLE_STATUSES),
                File.updated_at <= stale_before,
            )
            result = await session.execute(stmt)
            candidates = result.scalars().all()

        recovered_count = 0
        skipped_count = 0
        duplicate_count = 0
        for file_model in candidates:
            resolved_storage_path = resolve_storage_path(file_model.storage_path or "")
            if not resolved_storage_path or not await self._path_exists(resolved_storage_path):
                skipped_count += 1
                await self._mark_status(
                    file_model.id,
                    "skipped",
                    "恢复扫描跳过：源文件不存在",
                    file_model.workspace_id,
                )
                await self._emit_status_payload(
                    file_id=file_model.id,
                    workspace_id=file_model.workspace_id,
                    source="recover",
                    status="skipped",
                    stage="skipped",
                    attempt=0,
                    max_retries=0,
                    progress=100,
                    message="恢复扫描跳过：源文件不存在",
                )
                continue

            enqueued = await self.enqueue(
                PageIndexTask(
                    file_id=file_model.id,
                    file_path=resolved_storage_path,
                    workspace_id=file_model.workspace_id,
                    visibility=file_model.visibility or "dept",
                    owner_id=file_model.owner_id,
                    dept_id=file_model.dept_id,
                    priority=self._settings.default_task_priority,
                    max_retries=self._settings.build_max_retries,
                    source="recover",
                )
            )
            if enqueued:
                recovered_count += 1
            else:
                duplicate_count += 1

        logger.info(
            "[PageIndexWorker] recovery scan done: recovered=%s skipped=%s duplicated=%s",
            recovered_count,
            skipped_count,
            duplicate_count,
        )
        return {
            "recovered": recovered_count,
            "skipped": skipped_count,
            "duplicate": duplicate_count,
        }

    async def _worker_loop(self, worker_index: int) -> None:
        while self._running:
            try:
                _, _, task = await self._queue.get()
            except asyncio.CancelledError:
                break

            try:
                await self._run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "[PageIndexWorker] worker loop error: worker=%s file_id=%s err=%s",
                    worker_index,
                    task.file_id,
                    exc,
                )
            finally:
                self._queue.task_done()

    async def _run_task(self, task: PageIndexTask) -> None:
        async with self._state_lock:
            self._queued_ids.discard(task.file_id)
            if task.file_id in self._cancelled_ids:
                should_cancel = True
            else:
                should_cancel = False
                self._running_ids.add(task.file_id)

        if should_cancel:
            await self._mark_status(task.file_id, "cancelled", "任务在执行前已取消", task.workspace_id)
            await self._emit_status(
                task,
                status="cancelled",
                stage="cancelled",
                attempt=0,
                progress=100,
                message="任务在执行前已取消",
            )
            async with self._state_lock:
                self._cancelled_ids.discard(task.file_id)
                self._tasks_by_file_id.pop(task.file_id, None)
            return

        try:
            await self._execute_with_retry(task)
        finally:
            async with self._state_lock:
                self._running_ids.discard(task.file_id)
                self._tasks_by_file_id.pop(task.file_id, None)
                self._cancelled_ids.discard(task.file_id)

    async def _execute_with_retry(self, task: PageIndexTask) -> None:
        task.file_path = resolve_storage_path(task.file_path)
        if not await self._path_exists(task.file_path):
            await self._mark_status(task.file_id, "skipped", "源文件不存在，已跳过构建", task.workspace_id)
            await self._emit_status(
                task,
                status="skipped",
                stage="skipped",
                attempt=0,
                progress=100,
                message="源文件不存在，已跳过构建",
            )
            return

        max_retries = max(0, int(task.max_retries or 0))
        max_attempts = max_retries + 1

        for attempt_index in range(max_attempts):
            attempt = attempt_index + 1

            if await self._is_cancelled(task.file_id):
                await self._finalize_cancel(task)
                return

            await self._mark_status(task.file_id, "building", None, task.workspace_id)
            await self._emit_status(
                task,
                status="building",
                stage="building",
                attempt=attempt,
                progress=20,
                message="开始构建 PageIndex 树索引",
            )

            start_time = datetime.now()
            try:
                node_count = await self._tree_builder.build_and_store(
                    file_id=task.file_id,
                    workspace_id=task.workspace_id,
                    file_path=task.file_path,
                    visibility=task.visibility,
                    owner_id=task.owner_id,
                    dept_id=task.dept_id,
                )
            except Exception as exc:
                if await self._is_cancelled(task.file_id):
                    await self._finalize_cancel(task)
                    return

                if attempt < max_attempts:
                    await self._mark_status(task.file_id, "retrying", str(exc), task.workspace_id)
                    await self._emit_status(
                        task,
                        status="retrying",
                        stage="retrying",
                        attempt=attempt,
                        progress=40,
                        message=f"构建失败，准备重试: {exc}",
                    )
                    backoff_seconds = max(1, int(self._settings.build_retry_backoff_base_sec)) * (
                        2 ** (attempt - 1)
                    )
                    await asyncio.sleep(backoff_seconds)
                    continue

                await self._mark_status(task.file_id, "failed", str(exc), task.workspace_id)
                await self._emit_status(
                    task,
                    status="failed",
                    stage="failed",
                    attempt=attempt,
                    progress=100,
                    message=f"构建失败: {exc}",
                )
                logger.warning(
                    "[PageIndexWorker] build failed: file_id=%s attempt=%s err=%s",
                    task.file_id,
                    attempt,
                    exc,
                )
                return

            elapsed_seconds = int((datetime.now() - start_time).total_seconds())
            if node_count > 0:
                await self._mark_status(task.file_id, "ready", None, task.workspace_id)
                await self._emit_status(
                    task,
                    status="ready",
                    stage="ready",
                    attempt=attempt,
                    progress=100,
                    message=f"构建完成，nodes={node_count}，耗时={elapsed_seconds}s",
                )
            else:
                await self._mark_status(task.file_id, "skipped", None, task.workspace_id)
                await self._emit_status(
                    task,
                    status="skipped",
                    stage="skipped",
                    attempt=attempt,
                    progress=100,
                    message=f"构建完成但无可用节点，耗时={elapsed_seconds}s",
                )
            return

    async def _finalize_cancel(self, task: PageIndexTask) -> None:
        async with self._state_lock:
            self._cancelled_ids.discard(task.file_id)

        await self._mark_status(task.file_id, "cancelled", "任务执行期间被取消", task.workspace_id)
        await self._emit_status(
            task,
            status="cancelled",
            stage="cancelled",
            attempt=0,
            progress=100,
            message="任务执行期间被取消",
        )

    async def _is_cancelled(self, file_id: str) -> bool:
        async with self._state_lock:
            return file_id in self._cancelled_ids

    async def _mark_status(
        self,
        file_id: str,
        status: str,
        error: Optional[str],
        workspace_id: Optional[str] = None,
    ) -> None:
        # 系统内部组件更新状态；仍通过 FilesystemService 统一落库路径，
        # 并尽量带上 workspace_id 以保持租户隔离语义。
        try:
            async with get_async_db_context() as session:
                target_workspace_id = workspace_id
                if not target_workspace_id:
                    stmt = select(File.workspace_id).where(File.id == file_id)
                    result = await session.execute(stmt)
                    target_workspace_id = result.scalar_one_or_none()

                filesystem = FilesystemService(session, workspace_id=target_workspace_id)
                updated = await filesystem.update_pageindex_status(file_id, status, error)
                if not updated:
                    logger.debug(
                        "[PageIndexWorker] status update skipped: file_id=%s status=%s workspace_id=%s",
                        file_id,
                        status,
                        target_workspace_id,
                    )
        except Exception as exc:
            logger.error(
                "[PageIndexWorker] status update failed: file_id=%s status=%s err=%s",
                file_id,
                status,
                exc,
            )

    async def _emit_status(
        self,
        task: PageIndexTask,
        *,
        status: str,
        stage: str,
        attempt: int,
        progress: int,
        message: str,
    ) -> None:
        await self._emit_status_payload(
            file_id=task.file_id,
            workspace_id=task.workspace_id,
            source=task.source,
            status=status,
            stage=stage,
            attempt=attempt,
            max_retries=int(task.max_retries or 0),
            progress=progress,
            message=message,
        )

    async def _emit_status_payload(
        self,
        *,
        file_id: str,
        workspace_id: Optional[str],
        source: str,
        status: str,
        stage: str,
        attempt: int,
        max_retries: int,
        progress: int,
        message: str,
    ) -> None:
        await emit_pageindex_status(
            file_id=file_id,
            workspace_id=workspace_id,
            status=status,
            stage=stage,
            attempt=attempt,
            max_retries=max_retries,
            progress=progress,
            message=message,
            source=source,
        )

    async def _resolve_workspace_id(self, file_id: str) -> Optional[str]:
        try:
            async with get_async_db_context() as session:
                stmt = select(File.workspace_id).where(File.id == file_id)
                result = await session.execute(stmt)
                return result.scalar_one_or_none()
        except Exception as exc:
            logger.warning("[PageIndexWorker] resolve workspace failed: file_id=%s err=%s", file_id, exc)
            return None

    async def _path_exists(self, file_path: str) -> bool:
        try:
            return await asyncio.to_thread(os.path.exists, file_path)
        except Exception as exc:
            logger.warning("[PageIndexWorker] path check failed: path=%s err=%s", file_path, exc)
            return False

    def _on_worker_done(self, task: asyncio.Task) -> None:
        # done_callback 与 stop() 都在同一 asyncio event loop 执行，
        # 单线程模型下不存在真正并发写入；这里做防御性移除即可。
        if task in self._worker_tasks:
            self._worker_tasks.remove(task)

        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return

        if exc:
            logger.error("[PageIndexWorker] worker task exited with exception: %s", exc, exc_info=exc)

        if self._running:
            replacement_worker_index = next(self._worker_id_sequence)
            replacement = asyncio.create_task(self._worker_loop(replacement_worker_index))
            replacement.add_done_callback(self._on_worker_done)
            self._worker_tasks.append(replacement)
            logger.warning("[PageIndexWorker] worker restarted after unexpected exit")


_worker: Optional[PageIndexWorker] = None


def get_pageindex_worker() -> PageIndexWorker:
    global _worker
    if _worker is None:
        _worker = PageIndexWorker()
    return _worker
