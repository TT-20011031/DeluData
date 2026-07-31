"""
Ingestion task queue service.

提供 MySQL 持久化队列能力：
- enqueue / claim（含租户公平调度）
- CAS 心跳/进度/完成/失败/取消
- stale 接管与历史清理
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Optional, Tuple

from sqlalchemy import and_, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.models.knowledge.ingestion_task import IngestionTask

logger = logging.getLogger(__name__)


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
RUNNING_STATUSES = {"running", "cancel_requested"}
ACTIVE_STATUSES = {"pending", "running", "cancel_requested"}


class TaskQueueService:
    def __init__(self, db: Optional[AsyncSession] = None):
        self.db = db

    @asynccontextmanager
    async def _session_scope(self):
        if self.db is not None:
            yield self.db, False
            return

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            yield session, True

    @staticmethod
    def _now() -> datetime:
        return datetime.utcnow()

    async def enqueue_task(
        self,
        *,
        file_id: str,
        workspace_id: str,
        user_id: str,
        payload_json: Optional[dict[str, Any]] = None,
        max_attempts: Optional[int] = None,
    ) -> IngestionTask:
        settings = get_settings()
        max_attempts = max(1, int(max_attempts or settings.rag.ingest_task_max_attempts))

        async with self._session_scope() as (session, managed):
            existing_stmt = (
                select(IngestionTask)
                .where(
                    IngestionTask.file_id == file_id,
                    IngestionTask.workspace_id == workspace_id,
                    IngestionTask.status.in_(list(ACTIVE_STATUSES)),
                )
                .order_by(IngestionTask.created_at.desc())
                .limit(1)
            )
            existing = (await session.execute(existing_stmt)).scalars().first()
            if existing is not None:
                return existing

            task = IngestionTask(
                id=str(uuid.uuid4()),
                file_id=file_id,
                workspace_id=workspace_id,
                user_id=user_id,
                status="pending",
                stage="queued",
                progress=0,
                attempt=0,
                max_attempts=max_attempts,
                detail_json=None,
                payload_json=payload_json or {},
            )
            session.add(task)
            if not managed:
                await session.commit()
            return task

    async def get_task(
        self,
        task_id: str,
        workspace_id: Optional[str] = None,
    ) -> Optional[IngestionTask]:
        async with self._session_scope() as (session, _):
            stmt = select(IngestionTask).where(IngestionTask.id == task_id)
            if workspace_id:
                stmt = stmt.where(IngestionTask.workspace_id == workspace_id)
            return (await session.execute(stmt)).scalars().first()

    async def get_active_task_for_file(
        self,
        *,
        file_id: str,
        workspace_id: Optional[str] = None,
    ) -> Optional[IngestionTask]:
        async with self._session_scope() as (session, _):
            stmt = (
                select(IngestionTask)
                .where(
                    IngestionTask.file_id == file_id,
                    IngestionTask.status.in_(list(ACTIVE_STATUSES)),
                )
                .order_by(IngestionTask.created_at.desc())
                .limit(1)
            )
            if workspace_id:
                stmt = stmt.where(IngestionTask.workspace_id == workspace_id)
            return (await session.execute(stmt)).scalars().first()

    async def claim_task_with_fairness(
        self,
        *,
        worker_id: str,
        lease_timeout_sec: int,
        fair_scheduling: bool = True,
        workspace_max_running: int = 1,
    ) -> Optional[IngestionTask]:
        workspace_max_running = max(1, int(workspace_max_running or 1))
        lease_timeout_sec = max(30, int(lease_timeout_sec or 120))

        async with self._session_scope() as (session, managed):
            now = self._now()
            workspace_id = None
            if fair_scheduling:
                workspace_id = await self._pick_fair_workspace(
                    session=session,
                    now=now,
                    workspace_max_running=workspace_max_running,
                )

            try:
                task = await self._claim_task_with_lock(
                    session=session,
                    worker_id=worker_id,
                    now=now,
                    workspace_id=workspace_id,
                    lease_timeout_sec=lease_timeout_sec,
                )
            except Exception as exc:
                logger.warning("claim skip-locked failed, fallback optimistic: %s", exc)
                task = await self._claim_task_optimistic(
                    session=session,
                    worker_id=worker_id,
                    now=now,
                    workspace_id=workspace_id,
                    lease_timeout_sec=lease_timeout_sec,
                )

            if task is not None and not managed:
                await session.commit()
            return task

    async def _pick_fair_workspace(
        self,
        *,
        session: AsyncSession,
        now: datetime,
        workspace_max_running: int,
    ) -> Optional[str]:
        running_subq = (
            select(
                IngestionTask.workspace_id.label("workspace_id"),
                func.count(IngestionTask.id).label("running_count"),
            )
            .where(
                IngestionTask.status.in_(list(RUNNING_STATUSES)),
                IngestionTask.lease_expires_at.is_not(None),
                IngestionTask.lease_expires_at > now,
            )
            .group_by(IngestionTask.workspace_id)
            .subquery()
        )

        stmt = (
            select(
                IngestionTask.workspace_id,
                func.coalesce(running_subq.c.running_count, 0).label("running_count"),
                func.min(IngestionTask.created_at).label("oldest_pending"),
            )
            .outerjoin(
                running_subq,
                running_subq.c.workspace_id == IngestionTask.workspace_id,
            )
            .where(IngestionTask.status == "pending")
            .group_by(IngestionTask.workspace_id, running_subq.c.running_count)
            .having(func.coalesce(running_subq.c.running_count, 0) < workspace_max_running)
            .order_by(text("running_count ASC"), text("oldest_pending ASC"))
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return str(row.workspace_id)

    async def _claim_task_with_lock(
        self,
        *,
        session: AsyncSession,
        worker_id: str,
        now: datetime,
        workspace_id: Optional[str],
        lease_timeout_sec: int,
    ) -> Optional[IngestionTask]:
        task = await self._select_pending_with_lock(
            session=session,
            workspace_id=workspace_id,
        )
        if task is None and workspace_id is not None:
            task = await self._select_pending_with_lock(session=session, workspace_id=None)
        if task is None:
            return None

        run_token = str(uuid.uuid4())
        task.status = "running"
        task.stage = "preclean"
        task.progress = max(1, int(task.progress or 0))
        task.worker_id = worker_id
        task.run_token = run_token
        task.heartbeat_at = now
        task.lease_expires_at = now + timedelta(seconds=lease_timeout_sec)
        task.updated_at = now
        if task.started_at is None:
            task.started_at = now
        task.error_message = None
        return task

    async def _select_pending_with_lock(
        self,
        *,
        session: AsyncSession,
        workspace_id: Optional[str],
    ) -> Optional[IngestionTask]:
        stmt = (
            select(IngestionTask)
            .where(IngestionTask.status == "pending")
            .order_by(IngestionTask.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if workspace_id:
            stmt = stmt.where(IngestionTask.workspace_id == workspace_id)
        return (await session.execute(stmt)).scalars().first()

    async def _claim_task_optimistic(
        self,
        *,
        session: AsyncSession,
        worker_id: str,
        now: datetime,
        workspace_id: Optional[str],
        lease_timeout_sec: int,
    ) -> Optional[IngestionTask]:
        for _ in range(8):
            stmt = (
                select(IngestionTask.id)
                .where(IngestionTask.status == "pending")
                .order_by(IngestionTask.created_at.asc())
                .limit(1)
            )
            if workspace_id:
                stmt = stmt.where(IngestionTask.workspace_id == workspace_id)
            row = (await session.execute(stmt)).first()
            if row is None and workspace_id is not None:
                workspace_id = None
                continue
            if row is None:
                return None

            task_id = str(row.id)
            run_token = str(uuid.uuid4())
            result = await session.execute(
                update(IngestionTask)
                .where(IngestionTask.id == task_id, IngestionTask.status == "pending")
                .values(
                    status="running",
                    stage="preclean",
                    progress=1,
                    worker_id=worker_id,
                    run_token=run_token,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=lease_timeout_sec),
                    started_at=func.coalesce(IngestionTask.started_at, now),
                    updated_at=now,
                    error_message=None,
                )
            )
            if (result.rowcount or 0) <= 0:
                continue

            task_stmt = select(IngestionTask).where(IngestionTask.id == task_id)
            return (await session.execute(task_stmt)).scalars().first()
        return None

    async def heartbeat(
        self,
        *,
        task_id: str,
        worker_id: str,
        run_token: str,
        lease_timeout_sec: int,
    ) -> bool:
        now = self._now()
        async with self._session_scope() as (session, managed):
            result = await session.execute(
                update(IngestionTask)
                .where(
                    IngestionTask.id == task_id,
                    IngestionTask.worker_id == worker_id,
                    IngestionTask.run_token == run_token,
                    IngestionTask.status == "running",
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=max(30, lease_timeout_sec)),
                    updated_at=now,
                )
            )
            ok = (result.rowcount or 0) > 0
            if ok and not managed:
                await session.commit()
            return ok

    async def update_progress(
        self,
        *,
        task_id: str,
        worker_id: str,
        run_token: str,
        stage: str,
        progress: int,
        detail: Optional[dict[str, Any]] = None,
        refresh_lease: bool = False,
        lease_timeout_sec: int = 120,
    ) -> bool:
        now = self._now()
        values: dict[str, Any] = {
            "stage": stage,
            "progress": max(0, min(100, int(progress))),
            "updated_at": now,
        }
        if detail is not None:
            values["detail_json"] = detail
        if refresh_lease:
            values["heartbeat_at"] = now
            values["lease_expires_at"] = now + timedelta(seconds=max(30, lease_timeout_sec))

        async with self._session_scope() as (session, managed):
            result = await session.execute(
                update(IngestionTask)
                .where(
                    IngestionTask.id == task_id,
                    IngestionTask.worker_id == worker_id,
                    IngestionTask.run_token == run_token,
                    IngestionTask.status == "running",
                )
                .values(**values)
            )
            ok = (result.rowcount or 0) > 0
            if ok and not managed:
                await session.commit()
            return ok

    async def complete_task(
        self,
        *,
        task_id: str,
        worker_id: str,
        run_token: str,
        detail: Optional[dict[str, Any]] = None,
    ) -> bool:
        now = self._now()
        values: dict[str, Any] = {
            "status": "succeeded",
            "stage": "completed",
            "progress": 100,
            "updated_at": now,
            "finished_at": now,
            "worker_id": None,
            "run_token": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "error_message": None,
        }
        if detail is not None:
            values["detail_json"] = detail

        async with self._session_scope() as (session, managed):
            result = await session.execute(
                update(IngestionTask)
                .where(
                    IngestionTask.id == task_id,
                    IngestionTask.worker_id == worker_id,
                    IngestionTask.run_token == run_token,
                    IngestionTask.status == "running",
                )
                .values(**values)
            )
            ok = (result.rowcount or 0) > 0
            if ok and not managed:
                await session.commit()
            return ok

    async def fail_task(
        self,
        *,
        task_id: str,
        worker_id: str,
        run_token: str,
        error_message: str,
    ) -> str:
        """
        返回值：
        - retry: 已回到 pending（可重试）
        - failed: 已终态失败
        - lost: CAS 未命中（已被接管或状态漂移）
        """
        now = self._now()
        async with self._session_scope() as (session, managed):
            stmt = select(IngestionTask).where(
                IngestionTask.id == task_id,
                IngestionTask.worker_id == worker_id,
                IngestionTask.run_token == run_token,
                IngestionTask.status == "running",
            )
            task = (await session.execute(stmt)).scalars().first()
            if task is None:
                return "lost"

            next_attempt = int(task.attempt or 0) + 1
            can_retry = next_attempt < int(task.max_attempts or 1)
            task.attempt = next_attempt
            task.error_message = error_message
            task.updated_at = now

            if can_retry:
                task.status = "pending"
                task.stage = "queued"
                task.progress = 0
                task.worker_id = None
                task.run_token = None
                task.lease_expires_at = None
                task.heartbeat_at = None
                task.detail_json = {"last_error": error_message}
                outcome = "retry"
            else:
                task.status = "failed"
                task.stage = "failed"
                task.finished_at = now
                task.worker_id = None
                task.run_token = None
                task.lease_expires_at = None
                task.heartbeat_at = None
                outcome = "failed"

            if not managed:
                await session.commit()
            return outcome

    async def request_cancel(
        self,
        *,
        task_id: str,
        workspace_id: Optional[str] = None,
    ) -> bool:
        now = self._now()
        async with self._session_scope() as (session, managed):
            stmt = select(IngestionTask).where(IngestionTask.id == task_id)
            if workspace_id:
                stmt = stmt.where(IngestionTask.workspace_id == workspace_id)
            task = (await session.execute(stmt)).scalars().first()
            if task is None:
                return False

            if task.status == "pending":
                task.status = "cancelled"
                task.stage = "failed"
                task.finished_at = now
                task.progress = max(0, min(100, int(task.progress or 0)))
                task.updated_at = now
                task.error_message = "cancel_requested"
                ok = True
            elif task.status == "running":
                task.status = "cancel_requested"
                task.updated_at = now
                task.error_message = "cancel_requested"
                ok = True
            elif task.status == "cancel_requested":
                ok = True
            else:
                ok = False

            if ok and not managed:
                await session.commit()
            return ok

    async def is_cancel_requested(
        self,
        *,
        task_id: str,
        worker_id: str,
        run_token: str,
    ) -> bool:
        async with self._session_scope() as (session, _):
            stmt = select(IngestionTask.id).where(
                IngestionTask.id == task_id,
                IngestionTask.worker_id == worker_id,
                IngestionTask.run_token == run_token,
                IngestionTask.status == "cancel_requested",
            )
            row = (await session.execute(stmt)).first()
            return row is not None

    async def cancel_task(
        self,
        *,
        task_id: str,
        worker_id: str,
        run_token: str,
        message: str = "cancelled",
    ) -> bool:
        now = self._now()
        async with self._session_scope() as (session, managed):
            result = await session.execute(
                update(IngestionTask)
                .where(
                    IngestionTask.id == task_id,
                    IngestionTask.worker_id == worker_id,
                    IngestionTask.run_token == run_token,
                    IngestionTask.status.in_(["running", "cancel_requested"]),
                )
                .values(
                    status="cancelled",
                    stage="failed",
                    finished_at=now,
                    updated_at=now,
                    worker_id=None,
                    run_token=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    error_message=message,
                )
            )
            ok = (result.rowcount or 0) > 0
            if ok and not managed:
                await session.commit()
            return ok

    async def mark_stale_tasks(self) -> Tuple[int, int]:
        """
        返回 (running_to_pending, cancel_requested_to_cancelled)
        """
        now = self._now()
        async with self._session_scope() as (session, managed):
            running_result = await session.execute(
                update(IngestionTask)
                .where(
                    IngestionTask.status == "running",
                    IngestionTask.lease_expires_at.is_not(None),
                    IngestionTask.lease_expires_at < now,
                )
                .values(
                    status="pending",
                    stage="queued",
                    worker_id=None,
                    run_token=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    updated_at=now,
                    detail_json={"recover_reason": "lease_expired"},
                )
            )
            cancelled_result = await session.execute(
                update(IngestionTask)
                .where(
                    IngestionTask.status == "cancel_requested",
                    IngestionTask.lease_expires_at.is_not(None),
                    IngestionTask.lease_expires_at < now,
                )
                .values(
                    status="cancelled",
                    stage="failed",
                    worker_id=None,
                    run_token=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    updated_at=now,
                    finished_at=now,
                    error_message="cancelled_after_lease_expired",
                )
            )
            running_count = int(running_result.rowcount or 0)
            cancelled_count = int(cancelled_result.rowcount or 0)
            if (running_count or cancelled_count) and not managed:
                await session.commit()
            return running_count, cancelled_count

    async def cleanup_history_tasks(
        self,
        *,
        succeeded_days: int,
        failed_days: int,
        batch_size: int,
    ) -> Tuple[int, int]:
        """
        返回 (deleted_succeeded, deleted_failed_or_cancelled)
        """
        succeeded_days = max(1, int(succeeded_days))
        failed_days = max(1, int(failed_days))
        batch_size = max(1, int(batch_size))
        now = self._now()
        succeeded_before = now - timedelta(days=succeeded_days)
        failed_before = now - timedelta(days=failed_days)

        async with self._session_scope() as (session, managed):
            del_succeeded = await session.execute(
                text(
                    """
                    DELETE FROM ingestion_tasks
                    WHERE status = 'succeeded'
                      AND finished_at IS NOT NULL
                      AND finished_at < :succeeded_before
                    LIMIT :batch_size
                    """
                ),
                {"succeeded_before": succeeded_before, "batch_size": batch_size},
            )
            del_failed = await session.execute(
                text(
                    """
                    DELETE FROM ingestion_tasks
                    WHERE status IN ('failed', 'cancelled')
                      AND finished_at IS NOT NULL
                      AND finished_at < :failed_before
                    LIMIT :batch_size
                    """
                ),
                {"failed_before": failed_before, "batch_size": batch_size},
            )

            succeeded_count = int(del_succeeded.rowcount or 0)
            failed_count = int(del_failed.rowcount or 0)
            if (succeeded_count or failed_count) and not managed:
                await session.commit()
            return succeeded_count, failed_count
