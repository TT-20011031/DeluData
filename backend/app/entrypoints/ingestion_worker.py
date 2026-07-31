"""
Ingestion queue worker entrypoint.

职责：
- 轮询并 claim ingestion_tasks
- 通过 CAS + run_token 安全执行任务
- 心跳独立 DB Session，避免与主流程事务耦合
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import signal
import socket
import uuid
from threading import Event
from typing import Any, Optional

from dotenv import load_dotenv

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.core.utils.storage_path import resolve_storage_path
from app.models.common.context import UserContext
from app.models.common.enums import DocumentStatus
from app.services.filesystem_service import FilesystemService
from app.services.ingestion_service import IngestionService
from app.services.task_queue_service import TaskQueueService

load_dotenv()

logger = logging.getLogger("ingestion_worker")


class IngestionWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.worker_id = (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self.task_queue = TaskQueueService()
        self.stop_requested = False
        self.is_processing_task = False

        self.heartbeat_sec = max(3, int(self.settings.rag.ingest_task_heartbeat_sec))
        self.lease_timeout_sec = max(
            30, int(self.settings.rag.ingest_task_lease_timeout_sec)
        )
        self.idle_sleep_sec = 2
        self.maintenance_stale_sec = 15
        self.maintenance_cleanup_sec = 24 * 60 * 60

    def request_stop(self) -> None:
        self.stop_requested = True
        logger.info(
            "收到停机信号：停止拉取新任务，当前任务将执行到安全结束点后退出。"
        )

    async def run(self) -> None:
        if not self.settings.rag.ingest_use_db_queue:
            logger.warning("RAG_INGEST_USE_DB_QUEUE=false，worker 不启动。")
            return

        logger.info(
            "ingestion worker started: worker_id=%s heartbeat=%ss lease_timeout=%ss",
            self.worker_id,
            self.heartbeat_sec,
            self.lease_timeout_sec,
        )

        loop = asyncio.get_running_loop()
        last_stale_at = loop.time() - self.maintenance_stale_sec
        last_cleanup_at = loop.time() - self.maintenance_cleanup_sec

        while not self.stop_requested:
            now = loop.time()

            if now - last_stale_at >= self.maintenance_stale_sec:
                await self._safe_mark_stale_tasks()
                last_stale_at = now

            if now - last_cleanup_at >= self.maintenance_cleanup_sec:
                await self._safe_cleanup_history_tasks()
                last_cleanup_at = now

            task = await self.task_queue.claim_task_with_fairness(
                worker_id=self.worker_id,
                lease_timeout_sec=self.lease_timeout_sec,
                fair_scheduling=bool(self.settings.rag.ingest_task_fair_scheduling),
                workspace_max_running=int(
                    self.settings.rag.ingest_task_workspace_max_running
                ),
            )
            if task is None:
                await asyncio.sleep(self.idle_sleep_sec)
                continue

            self.is_processing_task = True
            try:
                await self._process_claimed_task(task)
            except Exception as exc:
                logger.error(
                    "任务执行异常: task_id=%s err=%s",
                    getattr(task, "id", "-"),
                    exc,
                    exc_info=True,
                )
            finally:
                self.is_processing_task = False

        logger.info("ingestion worker 已停止（不再拉取新任务）。")

    async def _safe_mark_stale_tasks(self) -> None:
        try:
            running_to_pending, cancel_to_cancelled = await self.task_queue.mark_stale_tasks()
            if running_to_pending or cancel_to_cancelled:
                logger.info(
                    "stale task recovered: running->pending=%s cancel_requested->cancelled=%s",
                    running_to_pending,
                    cancel_to_cancelled,
                )
        except Exception as exc:
            logger.warning("mark_stale_tasks 失败: %s", exc)

    async def _safe_cleanup_history_tasks(self) -> None:
        try:
            deleted_succeeded, deleted_failed = await self.task_queue.cleanup_history_tasks(
                succeeded_days=int(self.settings.rag.ingest_task_retention_succeeded_days),
                failed_days=int(self.settings.rag.ingest_task_retention_failed_days),
                batch_size=int(self.settings.rag.ingest_task_retention_batch_size),
            )
            if deleted_succeeded or deleted_failed:
                logger.info(
                    "history cleanup: succeeded=%s failed_or_cancelled=%s",
                    deleted_succeeded,
                    deleted_failed,
                )
        except Exception as exc:
            logger.warning("cleanup_history_tasks 失败: %s", exc)

    async def _process_claimed_task(self, task: Any) -> None:
        task_id = str(task.id)
        run_token = str(task.run_token or "")
        if not run_token:
            logger.error("claim 后 run_token 缺失: task_id=%s", task_id)
            return

        runtime_queue = TaskQueueService()
        cancel_event = Event()
        ownership_lost = asyncio.Event()
        monitor_stop = asyncio.Event()

        file_record = await self._get_file_record(
            file_id=task.file_id,
            workspace_id=task.workspace_id,
        )
        if file_record is None:
            await runtime_queue.cancel_task(
                task_id=task_id,
                worker_id=self.worker_id,
                run_token=run_token,
                message="file_not_found",
            )
            return

        # 每次真实执行前先回到 processing，避免保留历史 error 干扰前端展示。
        await self._update_file_status(
            file_id=task.file_id,
            workspace_id=task.workspace_id,
            status=DocumentStatus.PROCESSING.value,
            error=None,
        )

        monitor_task = asyncio.create_task(
            self._monitor_heartbeat_and_cancel(
                task_id=task_id,
                worker_id=self.worker_id,
                run_token=run_token,
                cancel_event=cancel_event,
                ownership_lost=ownership_lost,
                stop_event=monitor_stop,
            )
        )

        try:
            # stage: preclean
            preclean_ok = await runtime_queue.update_progress(
                task_id=task_id,
                worker_id=self.worker_id,
                run_token=run_token,
                stage="preclean",
                progress=3,
                detail={"preclean": {"status": "started"}},
                refresh_lease=True,
                lease_timeout_sec=self.lease_timeout_sec,
            )
            if not preclean_ok:
                await self._handle_update_cas_miss(
                    runtime_queue=runtime_queue,
                    task_id=task_id,
                    run_token=run_token,
                    cancel_event=cancel_event,
                    ownership_lost=ownership_lost,
                )
                return

            user_context = UserContext(
                user_id=task.user_id,
                workspace_id=task.workspace_id,
                allowed_tables=["*"],
                role="admin",
            )

            ingestion_service = IngestionService()
            try:
                # 强约束：preclean 失败不得进入 parsing。
                await ingestion_service.pre_cleanup_document_data(
                    file_id=task.file_id,
                    user_context=user_context,
                )
            except Exception as exc:
                await self._handle_task_failure(
                    runtime_queue=runtime_queue,
                    task=task,
                    run_token=run_token,
                    error_message=f"preclean_failed: {exc}",
                )
                return

            preclean_done = await runtime_queue.update_progress(
                task_id=task_id,
                worker_id=self.worker_id,
                run_token=run_token,
                stage="preclean",
                progress=8,
                detail={"preclean": {"status": "completed"}},
                refresh_lease=True,
                lease_timeout_sec=self.lease_timeout_sec,
            )
            if not preclean_done:
                await self._handle_update_cas_miss(
                    runtime_queue=runtime_queue,
                    task_id=task_id,
                    run_token=run_token,
                    cancel_event=cancel_event,
                    ownership_lost=ownership_lost,
                )
                return

            if cancel_event.is_set():
                await self._finalize_cancel(
                    runtime_queue=runtime_queue,
                    task=task,
                    run_token=run_token,
                    message="cancelled",
                )
                return

            async def progress_callback(
                stage: str,
                progress: int,
                detail: Optional[dict[str, Any]] = None,
            ) -> None:
                if ownership_lost.is_set() or cancel_event.is_set():
                    return

                updated = await runtime_queue.update_progress(
                    task_id=task_id,
                    worker_id=self.worker_id,
                    run_token=run_token,
                    stage=stage,
                    progress=progress,
                    detail=detail,
                    refresh_lease=True,
                    lease_timeout_sec=self.lease_timeout_sec,
                )
                if updated:
                    return

                await self._handle_update_cas_miss(
                    runtime_queue=runtime_queue,
                    task_id=task_id,
                    run_token=run_token,
                    cancel_event=cancel_event,
                    ownership_lost=ownership_lost,
                )

            payload = task.payload_json if isinstance(task.payload_json, dict) else {}
            task_kind = str(payload.get("task_kind") or "ingest").lower()

            if task_kind == "reindex":
                target_dept_id = payload.get("target_dept_id")
                if target_dept_id in ("", None) and file_record.dept_id:
                    target_dept_id = str(file_record.dept_id)

                await ingestion_service.update_document_vectors(
                    file_id=task.file_id,
                    file_path=resolve_storage_path(file_record.storage_path or ""),
                    user_context=user_context,
                    regenerate_description=bool(
                        payload.get("regenerate_description", True)
                    ),
                    visibility=payload.get("visibility") or file_record.visibility or "dept",
                    target_dept_id=target_dept_id,
                    cancel_event=cancel_event,
                    progress_callback=progress_callback,
                )
            else:
                target_dept_id = payload.get("target_dept_id")
                if target_dept_id in ("", None) and file_record.dept_id:
                    target_dept_id = str(file_record.dept_id)

                await ingestion_service.process_document(
                    doc_id=task.file_id,
                    file_path=resolve_storage_path(file_record.storage_path or ""),
                    user_context=user_context,
                    filename=payload.get("filename") or file_record.name,
                    target_dept_id=target_dept_id,
                    visibility=payload.get("visibility") or file_record.visibility or "dept",
                    cancel_event=cancel_event,
                    progress_callback=progress_callback,
                )

            if ownership_lost.is_set():
                logger.warning("任务归属丢失，停止后续写入: task_id=%s", task_id)
                return

            cancel_requested = cancel_event.is_set() or await runtime_queue.is_cancel_requested(
                task_id=task_id,
                worker_id=self.worker_id,
                run_token=run_token,
            )
            if cancel_requested:
                await self._finalize_cancel(
                    runtime_queue=runtime_queue,
                    task=task,
                    run_token=run_token,
                    message="cancelled",
                )
                return

            latest_file = await self._get_file_record(
                file_id=task.file_id,
                workspace_id=task.workspace_id,
            )
            file_status = (latest_file.status if latest_file else "") if latest_file else ""
            if file_status == DocumentStatus.INDEXED.value:
                completed = await runtime_queue.complete_task(
                    task_id=task_id,
                    worker_id=self.worker_id,
                    run_token=run_token,
                )
                if not completed:
                    logger.warning("complete CAS 未命中: task_id=%s", task_id)
                return

            error_message = "ingestion_failed"
            if latest_file is None:
                error_message = "file_not_found_after_ingest"
            elif latest_file.status == DocumentStatus.ERROR.value:
                error_message = latest_file.error_message or "ingestion_failed"
            else:
                error_message = f"unexpected_file_status:{latest_file.status}"

            await self._handle_task_failure(
                runtime_queue=runtime_queue,
                task=task,
                run_token=run_token,
                error_message=error_message,
            )
        finally:
            monitor_stop.set()
            monitor_task.cancel()
            try:
                await monitor_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _handle_update_cas_miss(
        self,
        *,
        runtime_queue: TaskQueueService,
        task_id: str,
        run_token: str,
        cancel_event: Event,
        ownership_lost: asyncio.Event,
    ) -> None:
        cancel_requested = await runtime_queue.is_cancel_requested(
            task_id=task_id,
            worker_id=self.worker_id,
            run_token=run_token,
        )
        if cancel_requested:
            cancel_event.set()
            return
        ownership_lost.set()
        cancel_event.set()

    async def _monitor_heartbeat_and_cancel(
        self,
        *,
        task_id: str,
        worker_id: str,
        run_token: str,
        cancel_event: Event,
        ownership_lost: asyncio.Event,
        stop_event: asyncio.Event,
    ) -> None:
        """
        独立会话心跳与取消监控：
        - 心跳：每 heartbeat_sec 刷新 lease
        - 取消：每 2s 检查 cancel_requested
        """
        queue = TaskQueueService()
        loop = asyncio.get_running_loop()
        next_heartbeat_at = loop.time() + self.heartbeat_sec
        cancel_poll_sec = 2

        while not stop_event.is_set():
            try:
                cancel_requested = await queue.is_cancel_requested(
                    task_id=task_id,
                    worker_id=worker_id,
                    run_token=run_token,
                )
                if cancel_requested:
                    cancel_event.set()
            except Exception as exc:
                logger.warning("取消检查失败: task_id=%s err=%s", task_id, exc)

            now = loop.time()
            if now >= next_heartbeat_at and not stop_event.is_set():
                try:
                    hb_ok = await queue.heartbeat(
                        task_id=task_id,
                        worker_id=worker_id,
                        run_token=run_token,
                        lease_timeout_sec=self.lease_timeout_sec,
                    )
                    if not hb_ok:
                        cancel_requested = await queue.is_cancel_requested(
                            task_id=task_id,
                            worker_id=worker_id,
                            run_token=run_token,
                        )
                        if cancel_requested:
                            cancel_event.set()
                            return
                        ownership_lost.set()
                        cancel_event.set()
                        return
                except Exception as exc:
                    logger.warning("heartbeat 失败: task_id=%s err=%s", task_id, exc)
                finally:
                    next_heartbeat_at = loop.time() + self.heartbeat_sec

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=cancel_poll_sec)
            except asyncio.TimeoutError:
                continue

    async def _handle_task_failure(
        self,
        *,
        runtime_queue: TaskQueueService,
        task: Any,
        run_token: str,
        error_message: str,
    ) -> None:
        outcome = await runtime_queue.fail_task(
            task_id=task.id,
            worker_id=self.worker_id,
            run_token=run_token,
            error_message=error_message[:2000],
        )
        if outcome == "retry":
            await self._update_file_status(
                file_id=task.file_id,
                workspace_id=task.workspace_id,
                status=DocumentStatus.PROCESSING.value,
                error=None,
            )
            return
        if outcome == "failed":
            await self._update_file_status(
                file_id=task.file_id,
                workspace_id=task.workspace_id,
                status=DocumentStatus.ERROR.value,
                error=error_message[:2000],
            )
            return
        logger.warning("fail_task CAS 未命中: task_id=%s", task.id)

    async def _finalize_cancel(
        self,
        *,
        runtime_queue: TaskQueueService,
        task: Any,
        run_token: str,
        message: str,
    ) -> None:
        cancelled = await runtime_queue.cancel_task(
            task_id=task.id,
            worker_id=self.worker_id,
            run_token=run_token,
            message=message,
        )
        if cancelled:
            await self._update_file_status(
                file_id=task.file_id,
                workspace_id=task.workspace_id,
                status=DocumentStatus.ERROR.value,
                error=message,
            )
        else:
            logger.warning("cancel_task CAS 未命中: task_id=%s", task.id)

    async def _get_file_record(
        self,
        *,
        file_id: str,
        workspace_id: str,
    ) -> Optional[Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            filesystem = FilesystemService(session, workspace_id=workspace_id)
            return await filesystem.get_file(file_id)

    async def _update_file_status(
        self,
        *,
        file_id: str,
        workspace_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            filesystem = FilesystemService(session, workspace_id=workspace_id)
            await filesystem.update_file_status(
                file_id=file_id,
                status=status,
                error=error,
            )


def _configure_logging() -> None:
    settings = get_settings()
    level_name = str(getattr(settings.log, "level", "INFO")).strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
    root_logger.setLevel(level)


async def _run_worker() -> None:
    worker = IngestionWorker()

    def _signal_handler(signum, _frame):
        logger.info("signal received: %s", signum)
        worker.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except Exception:
            pass

    try:
        await worker.run()
    finally:
        try:
            await get_async_db_manager().dispose()
        except Exception:
            pass


if __name__ == "__main__":
    _configure_logging()
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_run_worker())
