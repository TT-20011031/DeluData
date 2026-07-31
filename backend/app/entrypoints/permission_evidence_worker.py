"""Database-leased worker for organization profiles and access evidence."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import signal
import socket
import uuid
from datetime import datetime, timedelta

from dotenv import load_dotenv
from sqlalchemy import func, or_, select

from app.core.db.database import get_async_db_manager
from app.models.config.permission_evidence import SemanticPermissionEvidenceRunModel
from app.services.organization_semantic_service import get_organization_semantic_service
from app.services.permission_evidence_service import get_permission_evidence_service


load_dotenv()
logger = logging.getLogger("permission_evidence_worker")


class PermissionEvidenceWorker:
    def __init__(self) -> None:
        self.worker_id = f"permission-evidence:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.stop_requested = False
        self.lease_timeout = int(os.getenv("PERMISSION_EVIDENCE_LEASE_TIMEOUT_SEC", "180"))
        self.idle_sleep = int(os.getenv("PERMISSION_EVIDENCE_IDLE_SLEEP_SEC", "3"))
        self.profile_service = get_organization_semantic_service()
        self.evidence_service = get_permission_evidence_service()

    def request_stop(self) -> None:
        self.stop_requested = True

    async def run(self) -> None:
        logger.info("permission evidence worker started: %s", self.worker_id)
        while not self.stop_requested:
            await self._recover_stale()
            run = await self._claim()
            if not run:
                await asyncio.sleep(self.idle_sleep)
                continue
            await self._process(run)
        logger.info("permission evidence worker stopped")

    async def _claim(self):
        db = get_async_db_manager()
        async with db.session_scope() as session:
            candidates = list((await session.execute(
                select(SemanticPermissionEvidenceRunModel).where(
                    SemanticPermissionEvidenceRunModel.status == "queued",
                ).order_by(SemanticPermissionEvidenceRunModel.created_at)
                .limit(20).with_for_update(skip_locked=True)
            )).scalars())
            for row in candidates:
                running = (await session.execute(select(func.count()).select_from(
                    SemanticPermissionEvidenceRunModel
                ).where(
                    SemanticPermissionEvidenceRunModel.workspace_id == row.workspace_id,
                    SemanticPermissionEvidenceRunModel.status == "running",
                    SemanticPermissionEvidenceRunModel.id != row.id,
                ))).scalar() or 0
                if running:
                    continue
                now = datetime.now()
                row.status = "running"
                row.stage = "claimed"
                row.attempt_count += 1
                row.lease_owner = self.worker_id
                row.lease_expires_at = now + timedelta(seconds=self.lease_timeout)
                row.heartbeat_at = now
                row.started_at = row.started_at or now
                await session.flush()
                return {"id": int(row.id), "kind": row.job_kind}
        return None

    async def _process(self, run):
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(run["id"], stop))
        try:
            if run["kind"] == "org_profile":
                await self.profile_service.process_profile_run(run["id"])
            elif run["kind"] == "access_evidence":
                await self.evidence_service.process_run(run["id"])
        except Exception:  # noqa: BLE001
            logger.exception("permission evidence run failed: %s", run["id"])
            db = get_async_db_manager()
            async with db.session_scope() as session:
                row = await session.get(SemanticPermissionEvidenceRunModel, run["id"])
                if row:
                    row.status, row.stage = "failed", "failed"
                    row.error_message = "worker execution failed; inspect server logs"
                    row.finished_at = datetime.now()
        finally:
            stop.set()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, run_id: int, stop: asyncio.Event):
        while not stop.is_set():
            db = get_async_db_manager()
            async with db.session_scope() as session:
                row = await session.get(SemanticPermissionEvidenceRunModel, run_id)
                if not row or row.status != "running" or row.lease_owner != self.worker_id:
                    return
                row.heartbeat_at = datetime.now()
                row.lease_expires_at = datetime.now() + timedelta(seconds=self.lease_timeout)
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(10, self.lease_timeout // 4))
            except asyncio.TimeoutError:
                continue

    async def _recover_stale(self):
        db = get_async_db_manager()
        async with db.session_scope() as session:
            rows = list((await session.execute(select(SemanticPermissionEvidenceRunModel).where(
                SemanticPermissionEvidenceRunModel.status == "running",
                or_(
                    SemanticPermissionEvidenceRunModel.lease_expires_at.is_(None),
                    SemanticPermissionEvidenceRunModel.lease_expires_at < datetime.now(),
                ),
            ).with_for_update(skip_locked=True))).scalars())
            for row in rows:
                row.status = "queued" if row.attempt_count < 3 else "failed"
                row.stage = "retry_queued" if row.status == "queued" else "lease_expired"
                row.lease_owner = None
                row.lease_expires_at = None
                if row.status == "failed":
                    row.finished_at = datetime.now()
                    row.error_message = "worker lease expired after retries"


def _install_signal_handlers(worker: PermissionEvidenceWorker) -> None:
    if platform.system() == "Windows":
        return
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:
            pass


async def _main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    worker = PermissionEvidenceWorker()
    _install_signal_handlers(worker)
    await worker.run()


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_main())
