"""Semantic governance worker entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import signal
import socket
import uuid
from typing import Any

from dotenv import load_dotenv

from app.services.semantic_auto_governance_service import (
    get_semantic_auto_governance_service,
)

load_dotenv()

logger = logging.getLogger("semantic_governance_worker")


class SemanticGovernanceWorker:
    def __init__(self) -> None:
        self.worker_id = f"semantic-governance:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.service = get_semantic_auto_governance_service()
        self.stop_requested = False
        self.lease_timeout_sec = int(os.getenv("SEMANTIC_GOVERNANCE_LEASE_TIMEOUT_SEC", "180"))
        self.heartbeat_sec = max(10, self.lease_timeout_sec // 4)
        self.idle_sleep_sec = int(os.getenv("SEMANTIC_GOVERNANCE_IDLE_SLEEP_SEC", "3"))
        self.workspace_max_running = int(os.getenv("SEMANTIC_GOVERNANCE_WORKSPACE_MAX_RUNNING", "1"))
        self.maintenance_stale_sec = int(os.getenv("SEMANTIC_GOVERNANCE_STALE_CHECK_SEC", "30"))

    def request_stop(self) -> None:
        self.stop_requested = True
        logger.info("semantic governance worker stop requested")

    async def run(self) -> None:
        logger.info(
            "semantic governance worker started: worker_id=%s heartbeat=%ss lease=%ss",
            self.worker_id,
            self.heartbeat_sec,
            self.lease_timeout_sec,
        )
        loop = asyncio.get_running_loop()
        last_stale_at = loop.time() - self.maintenance_stale_sec

        while not self.stop_requested:
            now = loop.time()
            if now - last_stale_at >= self.maintenance_stale_sec:
                await self._safe_mark_stale()
                last_stale_at = now

            run = await self.service.claim_next_run(
                worker_id=self.worker_id,
                lease_timeout_sec=self.lease_timeout_sec,
                workspace_max_running=self.workspace_max_running,
            )
            if run is None:
                await asyncio.sleep(self.idle_sleep_sec)
                continue

            try:
                await self._process_run(run)
            except Exception as exc:  # noqa: BLE001
                logger.error("semantic governance run failed unexpectedly: run=%s err=%s", run.get("run_id"), exc, exc_info=True)

        logger.info("semantic governance worker stopped")

    async def _safe_mark_stale(self) -> None:
        try:
            recovered = await self.service.mark_stale_runs_failed()
            if recovered:
                logger.info("semantic governance stale runs recovered: %s", recovered)
        except Exception as exc:  # noqa: BLE001
            logger.warning("semantic governance stale recovery failed: %s", exc)

    async def _process_run(self, run: dict[str, Any]) -> None:
        run_id = int(run["run_id"])
        run_token = str(run.get("run_token") or "")
        if not run_token:
            logger.error("claimed semantic governance run has no token: run_id=%s", run_id)
            return
        stop_event = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(run_id, run_token, stop_event))
        try:
            await self.service.execute_run(run_id)
        finally:
            stop_event.set()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _heartbeat_loop(self, run_id: int, run_token: str, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                ok = await self.service.heartbeat_run(
                    run_id,
                    worker_id=self.worker_id,
                    run_token=run_token,
                    lease_timeout_sec=self.lease_timeout_sec,
                )
                if not ok:
                    logger.warning("semantic governance heartbeat ownership lost: run_id=%s", run_id)
                    return
            except Exception as exc:  # noqa: BLE001
                logger.warning("semantic governance heartbeat failed: run_id=%s err=%s", run_id, exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.heartbeat_sec)
            except asyncio.TimeoutError:
                continue


def _install_signal_handlers(worker: SemanticGovernanceWorker) -> None:
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
    worker = SemanticGovernanceWorker()
    _install_signal_handlers(worker)
    await worker.run()


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_main())
