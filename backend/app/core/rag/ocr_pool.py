"""OCR 进程池管理器（全局单例）。"""

from __future__ import annotations

import inspect
import logging
import multiprocessing
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Any, Dict, Optional

from app.config import get_settings
from app.core.rag.ocr_worker import init_ocr_worker, ocr_pdf_page, prewarm_ocr_worker

logger = logging.getLogger("rag.ocr_pool")


class OCRProcessPool:
    """OCR 任务进程池封装。"""

    def __init__(self) -> None:
        self._executor: Optional[ProcessPoolExecutor] = None
        self._manager: Optional[Any] = None
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._executor is not None:
                return

            settings = get_settings()
            mode = str(settings.rag.ocr_pipeline_mode).strip().lower()
            if mode != "windowed_v2":
                logger.info("OCR 进程池未启动，当前模式=%s", mode)
                return

            max_workers = max(1, int(settings.rag.ocr_worker_processes))
            device = str(settings.rag.ocr_device).strip().lower()
            if device == "gpu" and bool(settings.rag.ocr_gpu_conservative_mode) and max_workers > 1:
                logger.info("OCR GPU 保守调度已启用: workers=%s -> 1", max_workers)
                max_workers = 1
            start_method = str(settings.rag.ocr_worker_start_method or "spawn").strip().lower()
            mp_context = multiprocessing.get_context(start_method)

            worker_config = self._build_worker_config()
            executor_kwargs: Dict[str, Any] = {
                "max_workers": max_workers,
                "mp_context": mp_context,
                "initializer": init_ocr_worker,
                "initargs": (worker_config,),
            }
            if "max_tasks_per_child" in inspect.signature(ProcessPoolExecutor).parameters:
                executor_kwargs["max_tasks_per_child"] = max(
                    1, int(settings.rag.ocr_worker_max_tasks_per_child)
                )

            if self._manager is None:
                self._manager = multiprocessing.Manager()

            self._executor = ProcessPoolExecutor(**executor_kwargs)
            logger.info(
                "OCR 进程池已启动: workers=%s method=%s max_tasks_per_child=%s",
                max_workers,
                start_method,
                executor_kwargs.get("max_tasks_per_child", "n/a"),
            )

            if bool(settings.rag.ocr_engine_prewarm):
                self._prewarm(max_workers)

    def submit_page_ocr(
        self,
        pdf_path: str,
        page_number: int,
        dpi: int,
        cancel_event: Optional[Any] = None,
    ) -> Future:
        self.start()
        with self._lock:
            if self._executor is None:
                raise RuntimeError("当前模式下 OCR 进程池不可用")
            return self._executor.submit(ocr_pdf_page, pdf_path, page_number, dpi, cancel_event)

    def create_cancel_event(self) -> Any:
        """
        创建跨进程可见的取消信号。

        返回值为 multiprocessing.Manager.Event 代理对象，可直接传给 worker。
        """
        self.start()
        with self._lock:
            if self._manager is None:
                raise RuntimeError("OCR 进程池取消管理器未初始化")
            return self._manager.Event()

    def shutdown(self) -> None:
        with self._lock:
            if self._executor is None and self._manager is None:
                return
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
            if self._manager is not None:
                self._manager.shutdown()
            self._executor = None
            self._manager = None
            logger.info("OCR 进程池已关闭")

    def _prewarm(self, workers: int) -> None:
        assert self._executor is not None
        futures = [self._executor.submit(prewarm_ocr_worker) for _ in range(workers)]
        ok = 0
        for future in futures:
            try:
                payload = future.result(timeout=120)
                if payload.get("ready"):
                    ok += 1
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("OCR worker 预热失败: %s", exc)
        logger.info("OCR 进程池预热完成: %s/%s worker 就绪", ok, workers)

    @staticmethod
    def _build_worker_config() -> Dict[str, Any]:
        settings = get_settings()
        max_ocr_image_height_px = max(
            512,
            int(settings.rag.ocr_worker_max_ocr_image_height_px),
        )
        return {
            "prewarm": bool(settings.rag.ocr_engine_prewarm),
            "ort_intra_threads": max(1, int(settings.rag.ocr_ort_intra_threads)),
            "ort_inter_threads": max(1, int(settings.rag.ocr_ort_inter_threads)),
            "mupdf_store_max_mb": max(1, int(settings.rag.ocr_mupdf_store_max_mb)),
            "max_ocr_image_height_px": max_ocr_image_height_px,
            "max_render_pixels": max(1, int(settings.rag.ocr_worker_max_render_pixels)),
            "source_tile_overlap_px": max(
                0, int(settings.rag.ocr_worker_source_tile_overlap_px)
            ),
            "det_db_box_thresh": float(settings.rag.ocr_det_db_box_thresh),
            "det_db_unclip_ratio": float(settings.rag.ocr_det_db_unclip_ratio),
            "device": str(settings.rag.ocr_device).strip().lower(),
            "gpu_fallback_to_cpu": bool(settings.rag.ocr_gpu_fallback_to_cpu),
            "tile_threshold": max_ocr_image_height_px,
            "tile_overlap_ratio": 0.05,
        }


_POOL: Optional[OCRProcessPool] = None
_POOL_LOCK = threading.Lock()


def get_ocr_pool() -> OCRProcessPool:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = OCRProcessPool()
    return _POOL


def initialize_ocr_pool() -> None:
    get_ocr_pool().start()


def shutdown_ocr_pool() -> None:
    global _POOL
    if _POOL is None:
        return
    _POOL.shutdown()
