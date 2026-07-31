"""Wiki 编译任务队列服务。

设计原则（与 TaskQueueService 同款语义，但更简化）：
- 任务实体：`wiki_compile_tasks`（每条任务一次"编译批次"）
- 调度：按 workspace 公平 + skip-locked 抢占，与 IngestionTask 同模式
- 软取消：用户在编译过程中可取消；语义为"不再启动新的 LLM 调用、保留已落库的页"
  · pending 任务：直接转 `cancelled` 终态
  · running 任务：先转 `cancel_requested` 中间态，worker 在每次候选/批次开始前轮询，
    检测到取消后中断后续候选，等当前批次完成 → 走 mark_cancelled 写终态
- 入队幂等：同一 (workspace, scope=workspace) 已存在 pending/running 时不重复入队
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Optional, Tuple

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.models.wiki.wiki_compile_task import WikiCompileTask

logger = logging.getLogger(__name__)


# 终态：cancelled 与 succeeded/failed 同列，避免重新入队 / 心跳 / 重抢占。
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
# 仍在 worker 视野中的活跃状态。cancel_requested 是 running 的子态，
# 形式上仍属 "占用 lease 的运行中任务"，所以包含进 ACTIVE。
ACTIVE_STATUSES = {"pending", "running", "cancel_requested"}


class WikiCompileQueueService:
    """Wiki 编译任务队列。"""

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

    # ------------------------------------------------------------------
    # 入队
    # ------------------------------------------------------------------

    async def enqueue_task(
        self,
        *,
        workspace_id: str,
        trigger_type: str,
        user_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        max_attempts: Optional[int] = None,
        deduplicate: bool = True,
    ) -> WikiCompileTask:
        """入队一个编译任务。

        deduplicate=True 时，若同一 workspace 已有 pending/running 的相同 trigger_type
        + scope 任务，直接返回已存在的（避免暴量入队 + 重复编译）。
        """
        settings = get_settings()
        max_attempts = max(
            1, int(max_attempts or settings.wiki.compile_task_max_attempts)
        )
        payload = payload or {}

        async with self._session_scope() as (session, managed):
            if deduplicate:
                stmt = (
                    select(WikiCompileTask)
                    .where(
                        WikiCompileTask.workspace_id == workspace_id,
                        WikiCompileTask.trigger_type == trigger_type,
                        WikiCompileTask.status.in_(list(ACTIVE_STATUSES)),
                    )
                    .order_by(WikiCompileTask.created_at.desc())
                    .limit(1)
                )
                existing = (await session.execute(stmt)).scalars().first()
                if existing is not None:
                    # file_ids 合并入已有任务（拿最大公约数）
                    new_file_ids = list(payload.get("file_ids") or [])
                    if new_file_ids:
                        existing_payload = dict(existing.payload_json or {})
                        merged = list(
                            {*(existing_payload.get("file_ids") or []), *new_file_ids}
                        )
                        existing_payload["file_ids"] = merged
                        existing.payload_json = existing_payload
                        if not managed:
                            await session.commit()
                    return existing

            task = WikiCompileTask(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                trigger_type=trigger_type,
                user_id=user_id,
                payload_json=payload,
                status="pending",
                stage="queued",
                progress=0,
                attempt=0,
                max_attempts=max_attempts,
            )
            session.add(task)
            if not managed:
                await session.commit()
            return task

    # ------------------------------------------------------------------
    # 抢占（公平调度 + skip-locked）
    # ------------------------------------------------------------------

    async def claim_task(
        self,
        *,
        worker_id: str,
        lease_timeout_sec: int,
        workspace_max_running: int = 1,
    ) -> Optional[WikiCompileTask]:
        workspace_max_running = max(1, int(workspace_max_running or 1))
        lease_timeout_sec = max(30, int(lease_timeout_sec or 300))

        async with self._session_scope() as (session, managed):
            now = self._now()
            workspace_id = await self._pick_fair_workspace(
                session=session,
                now=now,
                workspace_max_running=workspace_max_running,
            )
            try:
                task = await self._claim_with_lock(
                    session=session,
                    worker_id=worker_id,
                    now=now,
                    workspace_id=workspace_id,
                    lease_timeout_sec=lease_timeout_sec,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "wiki claim with-lock 失败，降级 optimistic: %s", exc
                )
                task = await self._claim_optimistic(
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
                WikiCompileTask.workspace_id.label("workspace_id"),
                func.count(WikiCompileTask.id).label("running_count"),
            )
            .where(
                WikiCompileTask.status == "running",
                WikiCompileTask.lease_expires_at.is_not(None),
                WikiCompileTask.lease_expires_at > now,
            )
            .group_by(WikiCompileTask.workspace_id)
            .subquery()
        )
        stmt = (
            select(
                WikiCompileTask.workspace_id,
                func.coalesce(running_subq.c.running_count, 0).label("running_count"),
                func.min(WikiCompileTask.created_at).label("oldest_pending"),
            )
            .outerjoin(
                running_subq,
                running_subq.c.workspace_id == WikiCompileTask.workspace_id,
            )
            .where(WikiCompileTask.status == "pending")
            .group_by(WikiCompileTask.workspace_id, running_subq.c.running_count)
            .having(
                func.coalesce(running_subq.c.running_count, 0)
                < workspace_max_running
            )
            .order_by(text("running_count ASC"), text("oldest_pending ASC"))
            .limit(1)
        )
        row = (await session.execute(stmt)).first()
        return str(row.workspace_id) if row else None

    async def _claim_with_lock(
        self,
        *,
        session: AsyncSession,
        worker_id: str,
        now: datetime,
        workspace_id: Optional[str],
        lease_timeout_sec: int,
    ) -> Optional[WikiCompileTask]:
        task = await self._select_pending_with_lock(
            session=session, workspace_id=workspace_id
        )
        if task is None and workspace_id is not None:
            task = await self._select_pending_with_lock(session=session, workspace_id=None)
        if task is None:
            return None

        run_token = str(uuid.uuid4())
        task.status = "running"
        task.stage = "compiling"
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
    ) -> Optional[WikiCompileTask]:
        stmt = (
            select(WikiCompileTask)
            .where(WikiCompileTask.status == "pending")
            .order_by(WikiCompileTask.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if workspace_id:
            stmt = stmt.where(WikiCompileTask.workspace_id == workspace_id)
        return (await session.execute(stmt)).scalars().first()

    async def _claim_optimistic(
        self,
        *,
        session: AsyncSession,
        worker_id: str,
        now: datetime,
        workspace_id: Optional[str],
        lease_timeout_sec: int,
    ) -> Optional[WikiCompileTask]:
        for _ in range(8):
            stmt = (
                select(WikiCompileTask.id)
                .where(WikiCompileTask.status == "pending")
                .order_by(WikiCompileTask.created_at.asc())
                .limit(1)
            )
            if workspace_id:
                stmt = stmt.where(WikiCompileTask.workspace_id == workspace_id)
            row = (await session.execute(stmt)).first()
            if row is None and workspace_id is not None:
                workspace_id = None
                continue
            if row is None:
                return None

            task_id = str(row.id)
            run_token = str(uuid.uuid4())
            result = await session.execute(
                update(WikiCompileTask)
                .where(
                    WikiCompileTask.id == task_id,
                    WikiCompileTask.status == "pending",
                )
                .values(
                    status="running",
                    stage="compiling",
                    progress=1,
                    worker_id=worker_id,
                    run_token=run_token,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=lease_timeout_sec),
                    started_at=func.coalesce(WikiCompileTask.started_at, now),
                    updated_at=now,
                    error_message=None,
                )
            )
            if (result.rowcount or 0) <= 0:
                continue
            return (
                await session.execute(
                    select(WikiCompileTask).where(WikiCompileTask.id == task_id)
                )
            ).scalars().first()
        return None

    # ------------------------------------------------------------------
    # 心跳 / 进度
    # ------------------------------------------------------------------

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
                update(WikiCompileTask)
                .where(
                    WikiCompileTask.id == task_id,
                    WikiCompileTask.worker_id == worker_id,
                    WikiCompileTask.run_token == run_token,
                    # 允许在 cancel_requested 中间态下继续心跳，
                    # 使 worker 能顺利走完 "处理完当前批次 → mark_cancelled" 路径
                    WikiCompileTask.status.in_(["running", "cancel_requested"]),
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now
                    + timedelta(seconds=max(30, lease_timeout_sec)),
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
        refresh_lease: bool = True,
        lease_timeout_sec: int = 300,
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
            values["lease_expires_at"] = now + timedelta(
                seconds=max(30, lease_timeout_sec)
            )
        async with self._session_scope() as (session, managed):
            result = await session.execute(
                update(WikiCompileTask)
                .where(
                    WikiCompileTask.id == task_id,
                    WikiCompileTask.worker_id == worker_id,
                    WikiCompileTask.run_token == run_token,
                    # 同 heartbeat：允许 cancel_requested 中继续上报进度（例如 "正在取消..."）
                    WikiCompileTask.status.in_(["running", "cancel_requested"]),
                )
                .values(**values)
            )
            ok = (result.rowcount or 0) > 0
            if ok and not managed:
                await session.commit()
            return ok

    # ------------------------------------------------------------------
    # 终态
    # ------------------------------------------------------------------

    async def complete_task(
        self,
        *,
        task_id: str,
        worker_id: str,
        run_token: str,
        result_json: Optional[dict[str, Any]] = None,
    ) -> bool:
        now = self._now()
        async with self._session_scope() as (session, managed):
            result = await session.execute(
                update(WikiCompileTask)
                .where(
                    WikiCompileTask.id == task_id,
                    WikiCompileTask.worker_id == worker_id,
                    WikiCompileTask.run_token == run_token,
                    WikiCompileTask.status == "running",
                )
                .values(
                    status="succeeded",
                    stage="completed",
                    progress=100,
                    finished_at=now,
                    updated_at=now,
                    worker_id=None,
                    run_token=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    error_message=None,
                    result_json=result_json or None,
                )
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
        now = self._now()
        async with self._session_scope() as (session, managed):
            stmt = select(WikiCompileTask).where(
                WikiCompileTask.id == task_id,
                WikiCompileTask.worker_id == worker_id,
                WikiCompileTask.run_token == run_token,
                WikiCompileTask.status == "running",
            )
            task = (await session.execute(stmt)).scalars().first()
            if task is None:
                return "lost"

            next_attempt = int(task.attempt or 0) + 1
            can_retry = next_attempt < int(task.max_attempts or 1)
            task.attempt = next_attempt
            task.error_message = error_message[:1000]
            task.updated_at = now
            if can_retry:
                task.status = "pending"
                task.stage = "queued"
                task.progress = 0
                task.worker_id = None
                task.run_token = None
                task.lease_expires_at = None
                task.heartbeat_at = None
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

    # ------------------------------------------------------------------
    # 取消（软取消）
    # ------------------------------------------------------------------

    async def request_cancel(
        self,
        *,
        task_id: str,
        workspace_id: str,
    ) -> Tuple[str, Optional[str]]:
        """请求取消一个任务。

        Returns:
            (outcome, previous_status)
            outcome:
              - "cancelled_immediately" : pending 任务直接转 cancelled 终态
              - "cancel_requested"      : running 任务转 cancel_requested 等 worker 处理
              - "already_cancelling"    : 已是 cancel_requested
              - "already_terminal"      : 已是 succeeded/failed/cancelled
              - "not_found"             : 任务不存在或不属于该 workspace
        """
        now = self._now()
        async with self._session_scope() as (session, managed):
            task = (
                await session.execute(
                    select(WikiCompileTask).where(
                        WikiCompileTask.id == task_id,
                        WikiCompileTask.workspace_id == workspace_id,
                    )
                )
            ).scalars().first()
            if task is None:
                return ("not_found", None)

            prev_status = str(task.status)
            if prev_status in TERMINAL_STATUSES:
                return ("already_terminal", prev_status)
            if prev_status == "cancel_requested":
                return ("already_cancelling", prev_status)

            if prev_status == "pending":
                # 直接置终态，不需 worker 介入
                task.status = "cancelled"
                task.stage = "cancelled"
                task.finished_at = now
                task.updated_at = now
                task.worker_id = None
                task.run_token = None
                task.lease_expires_at = None
                task.heartbeat_at = None
                if not managed:
                    await session.commit()
                return ("cancelled_immediately", prev_status)

            if prev_status == "running":
                # 转中间态；worker 在下一次 cancel_check 时看到并 mark_cancelled
                task.status = "cancel_requested"
                task.updated_at = now
                if not managed:
                    await session.commit()
                return ("cancel_requested", prev_status)

            # 未知状态：保守不动
            return ("already_terminal", prev_status)

    async def is_cancel_requested(self, *, task_id: str) -> bool:
        """worker 在主循环里轻量轮询：当前任务是否已被请求取消。

        只读单字段；用于 cancel_check 回调，建议在 worker 端做短期缓存避免 DB 压力。
        """
        async with self._session_scope() as (session, _):
            row = (
                await session.execute(
                    select(WikiCompileTask.status).where(WikiCompileTask.id == task_id)
                )
            ).first()
            if row is None:
                return False
            return str(row[0]) == "cancel_requested"

    async def mark_cancelled(
        self,
        *,
        task_id: str,
        worker_id: str,
        run_token: str,
        result_json: Optional[dict[str, Any]] = None,
    ) -> bool:
        """worker 处理完当前批次（已落库本可保留的页）后写终态 cancelled。

        允许从 'running' 或 'cancel_requested' 进入 cancelled，兼容
        "用户在 worker 已经写过 mark_cancelled 之前手动取消" 的极小概率竞态。
        """
        now = self._now()
        async with self._session_scope() as (session, managed):
            result = await session.execute(
                update(WikiCompileTask)
                .where(
                    WikiCompileTask.id == task_id,
                    WikiCompileTask.worker_id == worker_id,
                    WikiCompileTask.run_token == run_token,
                    WikiCompileTask.status.in_(["running", "cancel_requested"]),
                )
                .values(
                    status="cancelled",
                    stage="cancelled",
                    finished_at=now,
                    updated_at=now,
                    worker_id=None,
                    run_token=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    error_message=None,
                    result_json=result_json or None,
                )
            )
            ok = (result.rowcount or 0) > 0
            if ok and not managed:
                await session.commit()
            return ok

    # ------------------------------------------------------------------
    # 维护
    # ------------------------------------------------------------------

    async def mark_stale_tasks(self) -> int:
        """把租约过期的 running 任务回到 pending；cancel_requested 直接置 cancelled。

        语义说明：
        - running 任务 lease 过期 → 回到 pending 重跑（保留进度，依靠 progress 字段防倒退）
        - cancel_requested 任务 lease 过期 → 用户已表达取消意图，不应再重跑，
          直接置 cancelled 终态；已落库的页保留（"软取消"承诺）
        """
        now = self._now()
        total = 0
        async with self._session_scope() as (session, managed):
            # cancel_requested + 过期 → cancelled
            r1 = await session.execute(
                update(WikiCompileTask)
                .where(
                    WikiCompileTask.status == "cancel_requested",
                    WikiCompileTask.lease_expires_at.is_not(None),
                    WikiCompileTask.lease_expires_at < now,
                )
                .values(
                    status="cancelled",
                    stage="cancelled",
                    finished_at=now,
                    worker_id=None,
                    run_token=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    updated_at=now,
                    detail_json={"recover_reason": "cancel_requested_lease_expired"},
                )
            )
            total += int(r1.rowcount or 0)

            # running + 过期 → pending（原行为）
            r2 = await session.execute(
                update(WikiCompileTask)
                .where(
                    WikiCompileTask.status == "running",
                    WikiCompileTask.lease_expires_at.is_not(None),
                    WikiCompileTask.lease_expires_at < now,
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
            total += int(r2.rowcount or 0)

            if total and not managed:
                await session.commit()
            return total

    async def cleanup_history_tasks(
        self,
        *,
        retention_days: int = 30,
        batch_size: int = 1000,
    ) -> int:
        retention_days = max(1, int(retention_days))
        batch_size = max(1, int(batch_size))
        cutoff = self._now() - timedelta(days=retention_days)
        async with self._session_scope() as (session, managed):
            result = await session.execute(
                text(
                    """
                    DELETE FROM wiki_compile_tasks
                    WHERE status IN ('succeeded', 'failed')
                      AND finished_at IS NOT NULL
                      AND finished_at < :cutoff
                    LIMIT :batch_size
                    """
                ),
                {"cutoff": cutoff, "batch_size": batch_size},
            )
            count = int(result.rowcount or 0)
            if count and not managed:
                await session.commit()
            return count


_singleton: Optional[WikiCompileQueueService] = None


def get_wiki_compile_queue() -> WikiCompileQueueService:
    global _singleton
    if _singleton is None:
        _singleton = WikiCompileQueueService()
    return _singleton
