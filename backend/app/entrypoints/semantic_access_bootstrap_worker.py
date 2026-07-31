"""Database-leased worker for AI semantic access bootstrap runs."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import signal
import socket
import uuid

from dotenv import load_dotenv

from app.services.semantic_access_bootstrap_service import (
    get_semantic_access_bootstrap_service,
)


load_dotenv()
logger = logging.getLogger("semantic_access_bootstrap_worker")


class SemanticAccessBootstrapWorker:
    def __init__(self) -> None:
        self.worker_id = f"semantic-access-bootstrap:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.service = get_semantic_access_bootstrap_service()
        self.stop_requested = False
        self.lease_timeout_sec = int(os.getenv("SEMANTIC_ACCESS_BOOTSTRAP_LEASE_TIMEOUT_SEC", "180"))
        self.heartbeat_sec = max(10, self.lease_timeout_sec // 4)
        self.idle_sleep_sec = int(os.getenv("SEMANTIC_ACCESS_BOOTSTRAP_IDLE_SLEEP_SEC", "3"))
        self.workspace_max_running = int(os.getenv("SEMANTIC_ACCESS_BOOTSTRAP_WORKSPACE_MAX_RUNNING", "1"))
        self.stale_check_sec = int(os.getenv("SEMANTIC_ACCESS_BOOTSTRAP_STALE_CHECK_SEC", "30"))

    def request_stop(self) -> None:
        self.stop_requested = True
        logger.info("semantic access bootstrap worker stop requested")

    async def run(self) -> None:
        logger.info("semantic access bootstrap worker started: %s", self.worker_id)
        loop = asyncio.get_running_loop()
        last_stale_at = loop.time() - self.stale_check_sec
        while not self.stop_requested:
            now = loop.time()
            if now - last_stale_at >= self.stale_check_sec:
                try:
                    recovered = await self.service.mark_stale_runs_failed()
                    if recovered:
                        logger.info("recovered %s stale access bootstrap runs", recovered)
                except Exception:  # noqa: BLE001
                    logger.warning("access bootstrap stale recovery failed", exc_info=True)
                last_stale_at = now
            run = await self.service.claim_next_run(
                worker_id=self.worker_id,
                lease_timeout_sec=self.lease_timeout_sec,
                workspace_max_running=self.workspace_max_running,
            )
            if run is None:
                await asyncio.sleep(self.idle_sleep_sec)
                continue
            await self._process_run(run)
        logger.info("semantic access bootstrap worker stopped")

    async def _process_run(self, run: dict) -> None:
        run_id = int(run["run_id"])
        run_token = str(run.get("run_token") or "")
        if not run_token:
            logger.error("claimed access bootstrap run has no token: %s", run_id)
            return
        stop_event = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(run_id, run_token, stop_event))
        try:
            await self.service.execute_run(
                run_id,
                worker_id=self.worker_id,
                run_token=run_token,
            )
        finally:
            stop_event.set()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, run_id: int, run_token: str, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                valid = await self.service.heartbeat_run(
                    run_id,
                    worker_id=self.worker_id,
                    run_token=run_token,
                    lease_timeout_sec=self.lease_timeout_sec,
                )
                if not valid:
                    return
            except Exception:  # noqa: BLE001
                logger.warning("access bootstrap heartbeat failed: %s", run_id, exc_info=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.heartbeat_sec)
            except asyncio.TimeoutError:
                continue


def _install_signal_handlers(worker: SemanticAccessBootstrapWorker) -> None:
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
    worker = SemanticAccessBootstrapWorker()
    _install_signal_handlers(worker)
    await worker.run()


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_main())
