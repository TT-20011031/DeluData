"""Wiki 编译 worker entrypoint。

职责：
- 轮询并 claim wiki_compile_tasks
- 通过 WikiService.compile_files / compile_workspace 执行编译
- 心跳维持租约，长任务失败时按 retry/failed 处理
- stale 接管：lease 过期的 running 任务回到 pending

特性（与 ingestion_worker 同款，但更精简）：
- 无 ownership_lost 监控（依靠 lease 过期机制）
- 无前置清理（compile 是幂等覆盖式）
- 软取消通道：worker 在每个候选开始前轮询 cancel_check（短期缓存），
  读到取消后不再启动新 LLM 调用；已落库的页保留，走 mark_cancelled 写终态
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import signal
import socket
import time
import uuid
from typing import Any, Optional

from dotenv import load_dotenv

from app.config import get_settings
from app.services.wiki_compile_queue_service import (
    WikiCompileQueueService,
    get_wiki_compile_queue,
)
from app.services.wiki_service import get_wiki_service

load_dotenv()

logger = logging.getLogger("wiki_compile_worker")


class WikiCompileWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.wiki_settings = self.settings.wiki
        self.worker_id = (
            f"wiki:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self.queue: WikiCompileQueueService = get_wiki_compile_queue()
        self.wiki_service = get_wiki_service()
        self.stop_requested = False

        self.lease_timeout_sec = max(
            60, int(self.wiki_settings.compile_task_lease_timeout_sec)
        )
        self.heartbeat_sec = max(15, self.lease_timeout_sec // 4)
        self.idle_sleep_sec = 5
        self.maintenance_stale_sec = 30
        self.maintenance_cleanup_sec = 24 * 60 * 60

    def request_stop(self) -> None:
        self.stop_requested = True
        logger.info("Wiki worker 收到停机信号；当前任务将执行完毕后退出。")

    async def run(self) -> None:
        if not self.wiki_settings.enabled:
            logger.warning("WIKI_ENABLED=false，wiki worker 不启动。")
            return

        logger.info(
            "wiki compile worker started: worker_id=%s heartbeat=%ss lease=%ss",
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
                await self._safe_mark_stale()
                last_stale_at = now
            if now - last_cleanup_at >= self.maintenance_cleanup_sec:
                await self._safe_cleanup_history()
                last_cleanup_at = now

            task = await self.queue.claim_task(
                worker_id=self.worker_id,
                lease_timeout_sec=self.lease_timeout_sec,
                workspace_max_running=int(
                    self.wiki_settings.compile_task_workspace_max_running
                ),
            )
            if task is None:
                await asyncio.sleep(self.idle_sleep_sec)
                continue

            try:
                await self._process_task(task)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "wiki 任务执行异常: task_id=%s err=%s",
                    getattr(task, "id", "-"),
                    exc,
                    exc_info=True,
                )

        logger.info("wiki compile worker 已停止。")

    async def _safe_mark_stale(self) -> None:
        try:
            recovered = await self.queue.mark_stale_tasks()
            if recovered:
                logger.info("wiki stale running->pending: %s", recovered)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wiki mark_stale 失败: %s", exc)

    async def _safe_cleanup_history(self) -> None:
        try:
            deleted = await self.queue.cleanup_history_tasks(retention_days=30)
            if deleted:
                logger.info("wiki history cleanup: %s rows", deleted)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wiki cleanup_history 失败: %s", exc)

    async def _process_task(self, task: Any) -> None:
        task_id = str(task.id)
        run_token = str(task.run_token or "")
        workspace_id = str(task.workspace_id)
        if not run_token:
            logger.error("wiki claim 后 run_token 缺失: task_id=%s", task_id)
            return

        payload = dict(task.payload_json or {})
        scope = str(payload.get("scope") or "files").lower()
        file_ids = list(payload.get("file_ids") or [])

        # 启动心跳协程
        stop_event = asyncio.Event()
        hb_task = asyncio.create_task(
            self._heartbeat_loop(task_id, run_token, stop_event)
        )

        # 进度严格不倒退：reclaim 场景下 task.progress 可能已是上一轮跑到的值（如 87%），
        # 不可被本轮起点 10% 覆盖；以 max(10, 既有 progress) 为本轮起点，
        # 后续 _on_progress 仅当 pct > last_reported_pct 时才上报。
        initial_progress = max(10, int(getattr(task, "progress", 0) or 0))
        try:
            await self.queue.update_progress(
                task_id=task_id,
                worker_id=self.worker_id,
                run_token=run_token,
                stage="compiling",
                progress=initial_progress,
                detail={
                    "scope": scope,
                    "file_count": len(file_ids),
                    "resumed_from": initial_progress if initial_progress > 10 else None,
                },
                refresh_lease=True,
                lease_timeout_sec=self.lease_timeout_sec,
            )

            # 进度映射：
            #   claim         = 10
            #   stage=extract = 10 + int(15 * done/total)  → 10..25
            #   stage=compile = 25 + int(65 * done/total)  → 25..90
            #   complete      = 100
            # 同一 (stage, pct) 不重复发 DB UPDATE；done==total 必发以保证终态。
            # reclaim 场景：last_reported_pct 取既有 progress，确保 UI 不退行。
            last_reported_pct = initial_progress
            loop = asyncio.get_running_loop()

            def _on_progress(stage: str, done: int, total: int) -> None:
                nonlocal last_reported_pct
                if total <= 0:
                    return
                if stage == "extract":
                    pct = 10 + int(15 * done / total)
                    pct = max(10, min(25, pct))
                    db_stage = "extracting"
                elif stage == "compile":
                    pct = 25 + int(65 * done / total)
                    pct = max(25, min(90, pct))
                    db_stage = "compiling"
                else:
                    return
                # 严格不倒退：本轮新计算的 pct 低于已上报最高值则忽略
                if pct < last_reported_pct:
                    return
                if pct == last_reported_pct and done != total:
                    return
                last_reported_pct = pct
                # 同 loop fire-and-forget 上报，不阻塞 compiler
                loop.create_task(
                    self.queue.update_progress(
                        task_id=task_id,
                        worker_id=self.worker_id,
                        run_token=run_token,
                        stage=db_stage,
                        progress=pct,
                        detail={
                            "scope": scope,
                            "file_count": len(file_ids),
                            "phase": stage,
                            "phase_done": done,
                            "phase_total": total,
                        },
                        refresh_lease=True,
                        lease_timeout_sec=self.lease_timeout_sec,
                    )
                )

            # 软取消检查点：worker 为 compiler 提供 cancel_check 回调。
            # 由独立协程 `_refresh_cancel_loop` 每 ~1.5s 查一次 DB 并写入缓存，
            # `_cancel_check` 仅读缓存，保证 compiler 内部高频调用零 DB 压力。
            cancel_cache: dict[str, Any] = {"value": False, "checked_at": 0.0}

            def _cancel_check() -> bool:
                return bool(cancel_cache.get("value"))

            async def _refresh_cancel_loop() -> None:
                # 独立协程：周期性刷新 cancel 状态，使 _cancel_check 能在同步调用中读取最新值。
                while not stop_event.is_set():
                    try:
                        cancel_cache["value"] = await self.queue.is_cancel_requested(
                            task_id=task_id
                        )
                        cancel_cache["checked_at"] = time.time()
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "is_cancel_requested 查询失败 task=%s", task_id
                        )
                    # 查询间隔：取消响应不超过 1.5s
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=1.5)
                    except asyncio.TimeoutError:
                        continue

            cancel_refresher = asyncio.create_task(_refresh_cancel_loop())

            try:
                if scope == "workspace":
                    result = await self.wiki_service.compile_workspace(
                        workspace_id=workspace_id,
                        triggered_by=f"worker:{task.trigger_type}",
                        user_id=task.user_id,
                        progress_cb=_on_progress,
                        cancel_check=_cancel_check,
                    )
                else:
                    if not file_ids:
                        await self.queue.fail_task(
                            task_id=task_id,
                            worker_id=self.worker_id,
                            run_token=run_token,
                            error_message="empty_file_ids_payload",
                        )
                        return
                    result = await self.wiki_service.compile_files(
                        workspace_id=workspace_id,
                        file_ids=file_ids,
                        triggered_by=f"worker:{task.trigger_type}",
                        user_id=task.user_id,
                        progress_cb=_on_progress,
                        cancel_check=_cancel_check,
                    )
            finally:
                # 停掉 cancel 刷新协程
                cancel_refresher.cancel()
                try:
                    await cancel_refresher
                except (asyncio.CancelledError, Exception):
                    pass

            if not result.get("success", True):
                await self.queue.fail_task(
                    task_id=task_id,
                    worker_id=self.worker_id,
                    run_token=run_token,
                    error_message=str(result.get("error") or "compile_failed"),
                )
                return

            # 软取消分支：outcome.cancelled 为 True 时走 mark_cancelled。
            # 已落库的页在 _apply_outcome 中已完成，这里只需写终态。
            if result.get("cancelled"):
                await self.queue.mark_cancelled(
                    task_id=task_id,
                    worker_id=self.worker_id,
                    run_token=run_token,
                    result_json=self._compact_result(result),
                )
                logger.info(
                    "wiki compile cancelled: task=%s ws=%s scope=%s files=%s kept=%s",
                    task_id,
                    workspace_id,
                    scope,
                    len(file_ids),
                    (result.get("applied") or {}).get("created", 0)
                    + (result.get("applied") or {}).get("updated", 0),
                )
                return

            await self.queue.complete_task(
                task_id=task_id,
                worker_id=self.worker_id,
                run_token=run_token,
                result_json=self._compact_result(result),
            )
            logger.info(
                "wiki compile done: task=%s ws=%s scope=%s files=%s applied=%s",
                task_id,
                workspace_id,
                scope,
                len(file_ids),
                (result.get("applied") or {}).get("created", 0)
                + (result.get("applied") or {}).get("updated", 0),
            )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "wiki compile failed: task=%s err=%s",
                task_id,
                exc,
                exc_info=True,
            )
            await self.queue.fail_task(
                task_id=task_id,
                worker_id=self.worker_id,
                run_token=run_token,
                error_message=str(exc)[:500],
            )
        finally:
            stop_event.set()
            try:
                await hb_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _heartbeat_loop(
        self,
        task_id: str,
        run_token: str,
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                await self.queue.heartbeat(
                    task_id=task_id,
                    worker_id=self.worker_id,
                    run_token=run_token,
                    lease_timeout_sec=self.lease_timeout_sec,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("wiki heartbeat 失败: task=%s err=%s", task_id, exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.heartbeat_sec)
            except asyncio.TimeoutError:
                continue

    @staticmethod
    def _compact_result(result: dict) -> dict:
        """裁剪 service 返回，落库时只保留关键摘要。"""
        applied = result.get("applied") or {}
        pages = applied.get("pages") or []

        total_prompt = 0
        total_completion = 0
        truncated_count = 0
        total_merge_count = 0
        for p in pages:
            cm = p.get("compile_meta") or {}
            total_prompt += int(cm.get("prompt_tokens") or 0)
            total_completion += int(cm.get("completion_tokens") or 0)
            if cm.get("truncated"):
                truncated_count += 1
            total_merge_count += int(cm.get("candidate_merge_count") or 1)

        return {
            "files": result.get("files"),
            "candidates": result.get("candidates"),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "created": applied.get("created", 0),
            "updated": applied.get("updated", 0),
            "links": applied.get("links", {}),
            "conflicts_added": applied.get("conflicts_added", 0),
            "skipped": len(result.get("skipped") or []),
            "errors": (result.get("errors") or [])[:5],
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "truncated_pages": truncated_count,
            "candidate_merges": total_merge_count - len(pages),
        }


def _install_signal_handlers(worker: WikiCompileWorker) -> None:
    loop = asyncio.get_event_loop()
    if platform.system() == "Windows":
        return  # Windows 无统一信号处理
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
    worker = WikiCompileWorker()
    _install_signal_handlers(worker)
    await worker.run()


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_main())
