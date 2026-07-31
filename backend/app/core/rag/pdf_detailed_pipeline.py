"""
PDF detailed parsing pipeline.

职责：
- 负责 PDF 文本提取 + OCR 调度 + 页级结果组装
- 与 DocumentParser 解耦，避免解析入口堆叠大量执行细节
"""
import logging
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, Optional, Tuple

from app.config import get_settings
from app.core.rag.document_parse_errors import (
    DocumentParseError,
    EncryptedFileError,
    FileTooLargeError,
    UnsupportedFormatError,
    WATERMARK_PATTERNS,
)
from app.core.rag.ingestion_progress import emit_progress


class PdfDetailedPipeline:
    def __init__(
        self,
        *,
        max_file_size: int,
        ocr_text_len_threshold: int,
        ocr_dpi: int,
        ocr_runtime_config: dict[str, Any],
        logger: logging.Logger,
    ) -> None:
        self.max_file_size = max_file_size
        self.ocr_text_len_threshold = ocr_text_len_threshold
        self.ocr_dpi = ocr_dpi
        self._ocr_runtime_config = ocr_runtime_config
        self.logger = logger
    def parse_windowed_sync(
        self,
        file_path: Path,
        cancel_event: Optional[Event] = None,
        progress_callback: Optional[Callable[[str, int, Optional[dict[str, Any]]], None]] = None,
    ) -> Dict[str, Any]:
        """
        PDF 详细解析（窗口化稳态版，worker 自渲染）。

        核心目标：
        1. 主进程只做文本提取与调度，不传 png_bytes，避免进程间大对象拷贝。
        2. OCR 任务通过进程池执行，并限制 in-flight 页数，控制内存峰值。
        3. 结果按页码归并，确保输出顺序与原 PDF 一致。
        """
        import fitz
        import time as _time

        from app.core.rag.ocr_pool import get_ocr_pool

        file_path = Path(file_path)
        if not file_path.exists():
            raise DocumentParseError(f"文件不存在: {file_path}")
        if file_path.suffix.lower() != ".pdf":
            raise UnsupportedFormatError(file_path.name, file_path.suffix.lower())

        file_size = file_path.stat().st_size
        if file_size > self.max_file_size:
            raise FileTooLargeError(file_size, self.max_file_size)
        if cancel_event is not None and cancel_event.is_set():
            raise DocumentParseError("文档处理已取消")

        settings = get_settings()
        page_template = settings.rag.page_marker_template
        ocr_timeout = max(10, int(settings.rag.ocr_page_timeout))
        max_inflight_pages = max(1, int(settings.rag.ocr_max_inflight_pages))
        batch_submit_size = max(1, int(settings.rag.ocr_batch_submit_size))
        ocr_device = str(settings.rag.ocr_device).strip().lower()
        if ocr_device == "gpu" and bool(settings.rag.ocr_gpu_conservative_mode):
            if max_inflight_pages > 1 or batch_submit_size > 1:
                self.logger.info(
                    "OCR GPU 保守调度已启用: max_inflight=%s->1 batch_submit=%s->1",
                    max_inflight_pages,
                    batch_submit_size,
                )
            max_inflight_pages = 1
            batch_submit_size = 1
        self.logger.info(
            "OCR 调度参数: file=%s device=%s max_inflight=%s batch_submit=%s timeout=%ss",
            file_path.name,
            ocr_device,
            max_inflight_pages,
            batch_submit_size,
            ocr_timeout,
        )

        ocr_pool = get_ocr_pool()
        worker_cancel_event = ocr_pool.create_cancel_event()
        page_data: list[dict] = []
        ocr_results: dict[int, tuple[str, str | None]] = {}
        futures_by_page: dict[int, Any] = {}
        page_by_future: dict[Any, int] = {}
        submitted_ocr_pages = 0
        logged_completed_ocr_pages = -1
        started_at = _time.monotonic()

        try:
            with fitz.open(str(file_path)) as doc:
                if doc.is_encrypted:
                    raise EncryptedFileError(file_path.name)

                total_pages = len(doc)
                emit_progress(
                    progress_callback,
                    "parsing",
                    5,
                    {"pdf": {"scanned_pages": 0, "total_pages": total_pages}},
                )
                for page_number, page in enumerate(doc, start=1):
                    if cancel_event is not None and cancel_event.is_set():
                        self.cancel_ocr_futures(
                            futures_by_page,
                            page_by_future,
                            worker_cancel_event=worker_cancel_event,
                        )
                        raise DocumentParseError("文档处理已取消")

                    raw_text = ""
                    extract_failed = False
                    try:
                        raw_text = page.get_text() or ""
                    except Exception as e:
                        extract_failed = True
                        self.logger.warning(
                            "PDF 页文本提取失败: file=%s page=%s err=%s",
                            file_path.name, page_number, e,
                        )

                    cleaned_text = self.clean_page_text(raw_text)
                    image_area_ratio = self.estimate_image_area_ratio(page)
                    needs_ocr = self.should_run_ocr(
                        extract_failed=extract_failed,
                        cleaned_text_length=len(cleaned_text),
                        image_area_ratio=image_area_ratio,
                    )
                    page_data.append({
                        "page_number": page_number,
                        "raw_text": raw_text,
                        "extract_failed": extract_failed,
                        "needs_ocr": needs_ocr,
                        "image_area_ratio": image_area_ratio,
                    })
                    self.logger.info(
                        "PDF 解析进度: file=%s page=%s/%s needs_ocr=%s",
                        file_path.name,
                        page_number,
                        total_pages,
                        needs_ocr,
                    )
                    emit_progress(
                        progress_callback,
                        "parsing",
                        min(25, int((page_number / max(1, total_pages)) * 25)),
                        {
                            "pdf": {
                                "scanned_pages": page_number,
                                "total_pages": total_pages,
                                "needs_ocr": needs_ocr,
                            }
                        },
                    )

                    if not needs_ocr:
                        continue

                    # 达到窗口上限前先收割一个结果，保证 in-flight 不超限。
                    while len(futures_by_page) >= max_inflight_pages:
                        if cancel_event is not None and cancel_event.is_set():
                            self.cancel_ocr_futures(
                                futures_by_page,
                                page_by_future,
                                worker_cancel_event=worker_cancel_event,
                            )
                            raise DocumentParseError("文档处理已取消")
                        self.wait_one_ocr_future(
                            futures_by_page=futures_by_page,
                            page_by_future=page_by_future,
                            ocr_results=ocr_results,
                            ocr_timeout=ocr_timeout,
                        )
                        completed_ocr_pages = len(ocr_results)
                        if completed_ocr_pages != logged_completed_ocr_pages:
                            self.logger.info(
                                "OCR 进度: file=%s completed=%s/%s inflight=%s",
                                file_path.name,
                                completed_ocr_pages,
                                max(1, submitted_ocr_pages),
                                len(futures_by_page),
                            )
                            emit_progress(
                                progress_callback,
                                "ocr",
                                25 + int(
                                    (completed_ocr_pages / max(1, submitted_ocr_pages)) * 25
                                ),
                                {
                                    "ocr": {
                                        "completed_pages": completed_ocr_pages,
                                        "total_pages": total_pages,
                                        "current_page": page_number,
                                    }
                                },
                            )
                            logged_completed_ocr_pages = completed_ocr_pages

                    try:
                        future = ocr_pool.submit_page_ocr(
                            str(file_path),
                            page_number,
                            self.ocr_dpi,
                            worker_cancel_event,
                        )
                    except Exception as e:
                        # 提交失败时回退到原文本，避免整份文档因 OCR 子系统异常而失败。
                        ocr_results[page_number] = ("", f"ocr_submit_failed:{e}")
                        self.logger.warning(
                            "OCR 提交失败，回退原文本: file=%s page=%s err=%s",
                            file_path.name,
                            page_number,
                            e,
                        )
                        continue

                    futures_by_page[page_number] = future
                    page_by_future[future] = page_number
                    submitted_ocr_pages += 1
                    self.logger.info(
                        "OCR 提交进度: file=%s page=%s/%s submitted=%s inflight=%s",
                        file_path.name,
                        page_number,
                        total_pages,
                        submitted_ocr_pages,
                        len(futures_by_page),
                    )

                    # 分批提交后做一次无阻塞收割，降低队列堆积。
                    if submitted_ocr_pages % batch_submit_size == 0:
                        self.collect_done_ocr_futures(
                            futures_by_page=futures_by_page,
                            page_by_future=page_by_future,
                            ocr_results=ocr_results,
                        )
                        completed_ocr_pages = len(ocr_results)
                        if completed_ocr_pages != logged_completed_ocr_pages:
                            self.logger.info(
                                "OCR 进度: file=%s completed=%s/%s inflight=%s",
                                file_path.name,
                                completed_ocr_pages,
                                max(1, submitted_ocr_pages),
                                len(futures_by_page),
                            )
                            emit_progress(
                                progress_callback,
                                "ocr",
                                25 + int(
                                    (completed_ocr_pages / max(1, submitted_ocr_pages)) * 25
                                ),
                                {
                                    "ocr": {
                                        "completed_pages": completed_ocr_pages,
                                        "total_pages": total_pages,
                                        "current_page": page_number,
                                    }
                                },
                            )
                            logged_completed_ocr_pages = completed_ocr_pages
                        self.logger.debug(
                            "OCR 调度进度: file=%s page=%s/%s submitted=%s inflight=%s",
                            file_path.name,
                            page_number,
                            total_pages,
                            submitted_ocr_pages,
                            len(futures_by_page),
                        )

            while futures_by_page:
                if cancel_event is not None and cancel_event.is_set():
                    self.cancel_ocr_futures(
                        futures_by_page,
                        page_by_future,
                        worker_cancel_event=worker_cancel_event,
                    )
                    raise DocumentParseError("文档处理已取消")
                self.wait_one_ocr_future(
                    futures_by_page=futures_by_page,
                    page_by_future=page_by_future,
                    ocr_results=ocr_results,
                    ocr_timeout=ocr_timeout,
                )
                completed_ocr_pages = len(ocr_results)
                if completed_ocr_pages != logged_completed_ocr_pages:
                    self.logger.info(
                        "OCR 进度: file=%s completed=%s/%s inflight=%s",
                        file_path.name,
                        completed_ocr_pages,
                        max(1, submitted_ocr_pages),
                        len(futures_by_page),
                    )
                    emit_progress(
                        progress_callback,
                        "ocr",
                        25 + int((completed_ocr_pages / max(1, submitted_ocr_pages)) * 25),
                        {
                            "ocr": {
                                "completed_pages": completed_ocr_pages,
                                "total_pages": total_pages,
                                "current_page": completed_ocr_pages,
                            }
                        },
                    )
                    logged_completed_ocr_pages = completed_ocr_pages
        except fitz.FileDataError as e:
            self.cancel_ocr_futures(
                futures_by_page,
                page_by_future,
                worker_cancel_event=worker_cancel_event,
            )
            raise DocumentParseError(f"PDF 文件损坏或格式错误: {e}")
        except Exception:
            self.cancel_ocr_futures(
                futures_by_page,
                page_by_future,
                worker_cancel_event=worker_cancel_event,
            )
            raise

        elapsed = _time.monotonic() - started_at
        if submitted_ocr_pages > 0:
            self.logger.info(
                "窗口化 OCR 完成: file=%s pages=%s ocr_pages=%s in_flight<=%s 耗时 %.1fs",
                file_path.name,
                len(page_data),
                submitted_ocr_pages,
                max_inflight_pages,
                elapsed,
            )

        pages_text: list[str] = []
        pages_meta: list[dict] = []
        for d in page_data:
            if cancel_event is not None and cancel_event.is_set():
                raise DocumentParseError("文档处理已取消")

            pn = d["page_number"]
            final_text = (d["raw_text"] or "").strip()
            used_ocr = False
            ocr_error = None

            if d["needs_ocr"]:
                ocr_text, ocr_error = ocr_results.get(pn, ("", "ocr_missing_result"))
                if ocr_text.strip():
                    final_text = self.merge_native_and_ocr_text(final_text, ocr_text)
                    used_ocr = True

            pages_text.append(f"{page_template.format(pn)}\n{final_text}".rstrip())
            pages_meta.append({
                "page_number": pn,
                "text_length": len(final_text),
                "used_ocr": used_ocr,
                "extract_failed": d["extract_failed"],
                "ocr_error": ocr_error,
                "image_area_ratio": d.get("image_area_ratio", 0.0),
            })

        return {
            "text": "\n\n".join(pages_text),
            "pages": pages_meta,
            "total_pages": len(pages_meta),
        }


    def parse_legacy_sync(
        self,
        file_path: Path,
        cancel_event: Optional[Event] = None,
        progress_callback: Optional[Callable[[str, int, Optional[dict[str, Any]]], None]] = None,
        ocr_from_image_bytes: Optional[Callable[[bytes], Tuple[str, Optional[str]]]] = None,
    ) -> Dict[str, Any]:
        """
        PDF 详细解析（三阶段：文本提取 → 并行 OCR → 按页组装，legacy 线程池版）。

        Phase 1: 主线程提取文本、渲染需要 OCR 的页面为 PNG bytes（fitz 不线程安全）
        Phase 2: ThreadPoolExecutor 并行 OCR（从 PNG bytes，绕过 fitz 线程限制）
        Phase 3: 按页码顺序组装最终文本
        """
        import fitz
        import time as _time

        file_path = Path(file_path)
        if not file_path.exists():
            raise DocumentParseError(f"文件不存在: {file_path}")
        if file_path.suffix.lower() != ".pdf":
            raise UnsupportedFormatError(file_path.name, file_path.suffix.lower())

        file_size = file_path.stat().st_size
        if file_size > self.max_file_size:
            raise FileTooLargeError(file_size, self.max_file_size)
        if cancel_event is not None and cancel_event.is_set():
            raise DocumentParseError("文档处理已取消")

        settings = get_settings()
        page_template = settings.rag.page_marker_template
        ocr_parallel = max(1, settings.rag.ocr_parallel_pages)
        ocr_timeout = max(10, settings.rag.ocr_page_timeout)
        ocr_device = str(settings.rag.ocr_device).strip().lower()
        if ocr_device == "gpu" and bool(settings.rag.ocr_gpu_conservative_mode):
            if ocr_parallel > 1:
                self.logger.info("OCR GPU 保守调度已启用: parallel=%s->1", ocr_parallel)
            ocr_parallel = 1

        # ---- Phase 1: 提取文本 + 渲染 OCR 页面（主线程） ----
        page_data: list[dict] = []
        try:
            with fitz.open(str(file_path)) as doc:
                if doc.is_encrypted:
                    raise EncryptedFileError(file_path.name)

                total_pages = len(doc)
                emit_progress(
                    progress_callback,
                    "parsing",
                    5,
                    {"pdf": {"scanned_pages": 0, "total_pages": total_pages}},
                )
                for page_number, page in enumerate(doc, start=1):
                    if cancel_event is not None and cancel_event.is_set():
                        raise DocumentParseError("文档处理已取消")

                    raw_text = ""
                    extract_failed = False
                    try:
                        raw_text = page.get_text() or ""
                    except Exception as e:
                        extract_failed = True
                        self.logger.warning(
                            "PDF 页文本提取失败: file=%s page=%s err=%s",
                            file_path.name, page_number, e,
                        )

                    cleaned_text = self.clean_page_text(raw_text)
                    image_area_ratio = self.estimate_image_area_ratio(page)
                    needs_ocr = self.should_run_ocr(
                        extract_failed=extract_failed,
                        cleaned_text_length=len(cleaned_text),
                        image_area_ratio=image_area_ratio,
                    )
                    self.logger.info(
                        "PDF 解析进度: file=%s page=%s/%s needs_ocr=%s",
                        file_path.name,
                        page_number,
                        total_pages,
                        needs_ocr,
                    )
                    emit_progress(
                        progress_callback,
                        "parsing",
                        min(25, int((page_number / max(1, total_pages)) * 25)),
                        {
                            "pdf": {
                                "scanned_pages": page_number,
                                "total_pages": total_pages,
                                "needs_ocr": needs_ocr,
                            }
                        },
                    )

                    png_bytes = None
                    if needs_ocr:
                        self.logger.info(
                            "渲染 OCR 页面: file=%s page=%s/%s (dpi=%s)",
                            file_path.name, page_number, total_pages, self.ocr_dpi,
                        )
                        pixmap = page.get_pixmap(dpi=self.ocr_dpi, alpha=False)
                        png_bytes = pixmap.tobytes("png")

                    page_data.append({
                        "page_number": page_number,
                        "raw_text": raw_text,
                        "extract_failed": extract_failed,
                        "needs_ocr": needs_ocr,
                        "image_area_ratio": image_area_ratio,
                        "png_bytes": png_bytes,
                    })
        except fitz.FileDataError as e:
            raise DocumentParseError(f"PDF 文件损坏或格式错误: {e}")

        # ---- Phase 2: 并行 OCR ----
        ocr_pages = [
            (d["page_number"], d["png_bytes"])
            for d in page_data
            if d["needs_ocr"] and d["png_bytes"]
        ]
        ocr_results: dict[int, tuple[str, str | None]] = {}

        if ocr_pages:
            if cancel_event is not None and cancel_event.is_set():
                raise DocumentParseError("文档处理已取消")
            emit_progress(
                progress_callback,
                "ocr",
                25,
                {
                    "ocr": {
                        "completed_pages": 0,
                        "total_pages": total_pages,
                        "current_page": 0,
                        "ocr_target_pages": len(ocr_pages),
                    }
                },
            )

            # 复用统一 OCR 运行时配置，避免 parser 与 worker 双份实现。
            from app.core.rag.ocr_worker import ensure_ocr_runtime
            ensure_ocr_runtime(self._ocr_runtime_config)

            from concurrent.futures import ThreadPoolExecutor
            from concurrent.futures import TimeoutError as FuturesTimeout

            self.logger.info(
                "开始并行 OCR: %s 页, 并发=%s, 超时=%ss/页",
                len(ocr_pages), ocr_parallel, ocr_timeout,
            )
            t0 = _time.monotonic()

            executor = ThreadPoolExecutor(max_workers=ocr_parallel)
            futures_by_pn: dict[int, object] = {}
            ocr_callable = ocr_from_image_bytes or self.ocr_from_image_bytes
            cancelled = False
            try:
                futures_by_pn = {
                    pn: executor.submit(ocr_callable, png)
                    for pn, png in ocr_pages
                }
                for pn, future in futures_by_pn.items():
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        self.logger.info("OCR 收集阶段检测到取消信号，中止后续页面: page=%s", pn)
                        break
                    try:
                        ocr_text, ocr_err = future.result(timeout=ocr_timeout)
                        ocr_results[pn] = (ocr_text, ocr_err)
                        self.logger.info(
                            "OCR 进度: file=%s completed=%s/%s page=%s",
                            file_path.name,
                            len(ocr_results),
                            len(ocr_pages),
                            pn,
                        )
                        emit_progress(
                            progress_callback,
                            "ocr",
                            25 + int((len(ocr_results) / max(1, len(ocr_pages))) * 25),
                            {
                                "ocr": {
                                    "completed_pages": len(ocr_results),
                                    "total_pages": total_pages,
                                    "current_page": pn,
                                    "ocr_target_pages": len(ocr_pages),
                                }
                            },
                        )
                    except FuturesTimeout:
                        future.cancel()
                        ocr_results[pn] = ("", f"OCR 超时 ({ocr_timeout}s)")
                        self.logger.warning("OCR 超时: page=%s", pn)
                        self.logger.info(
                            "OCR 进度: file=%s completed=%s/%s page=%s(timeout)",
                            file_path.name,
                            len(ocr_results),
                            len(ocr_pages),
                            pn,
                        )
                        emit_progress(
                            progress_callback,
                            "ocr",
                            25 + int((len(ocr_results) / max(1, len(ocr_pages))) * 25),
                            {
                                "ocr": {
                                    "completed_pages": len(ocr_results),
                                    "total_pages": total_pages,
                                    "current_page": pn,
                                    "ocr_target_pages": len(ocr_pages),
                                }
                            },
                        )
                    except Exception as e:
                        ocr_results[pn] = ("", str(e))
                        self.logger.warning("OCR 异常: page=%s err=%s", pn, e)
                        self.logger.info(
                            "OCR 进度: file=%s completed=%s/%s page=%s(error)",
                            file_path.name,
                            len(ocr_results),
                            len(ocr_pages),
                            pn,
                        )
                        emit_progress(
                            progress_callback,
                            "ocr",
                            25 + int((len(ocr_results) / max(1, len(ocr_pages))) * 25),
                            {
                                "ocr": {
                                    "completed_pages": len(ocr_results),
                                    "total_pages": total_pages,
                                    "current_page": pn,
                                    "ocr_target_pages": len(ocr_pages),
                                }
                            },
                        )
            finally:
                for future in futures_by_pn.values():
                    if not future.done():
                        future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)

            if cancelled:
                raise DocumentParseError("文档处理已取消")

            elapsed = _time.monotonic() - t0
            self.logger.info(
                "并行 OCR 完成: %s 页, 耗时 %.1fs", len(ocr_pages), elapsed,
            )
        else:
            emit_progress(
                progress_callback,
                "ocr",
                50,
                {
                    "ocr": {
                        "completed_pages": 0,
                        "total_pages": len(page_data),
                        "current_page": 0,
                        "ocr_target_pages": 0,
                    }
                },
            )

        # ---- Phase 3: 按页码顺序组装 ----
        pages_text: list[str] = []
        pages_meta: list[dict] = []

        for d in page_data:
            if cancel_event is not None and cancel_event.is_set():
                raise DocumentParseError("文档处理已取消")
            pn = d["page_number"]
            final_text = (d["raw_text"] or "").strip()
            used_ocr = False
            ocr_error = None

            if d["needs_ocr"] and pn in ocr_results:
                ocr_text, ocr_error = ocr_results[pn]
                if ocr_text.strip():
                    final_text = self.merge_native_and_ocr_text(final_text, ocr_text)
                    used_ocr = True

            pages_text.append(f"{page_template.format(pn)}\n{final_text}".rstrip())
            pages_meta.append({
                "page_number": pn,
                "text_length": len(final_text),
                "used_ocr": used_ocr,
                "extract_failed": d["extract_failed"],
                "ocr_error": ocr_error,
                "image_area_ratio": d.get("image_area_ratio", 0.0),
            })

        return {
            "text": "\n\n".join(pages_text),
            "pages": pages_meta,
            "total_pages": len(pages_meta),
        }


    def clean_page_text(self, text: str) -> str:
        cleaned = text or ""
        for pattern in WATERMARK_PATTERNS:
            cleaned = cleaned.replace(pattern, "")
        return cleaned.replace(" ", "").replace("\n", "").strip()

    def should_run_ocr(
        self,
        extract_failed: bool,
        cleaned_text_length: int,
        image_area_ratio: float = 0.0,
    ) -> bool:
        if extract_failed or cleaned_text_length < self.ocr_text_len_threshold:
            return True
        return image_area_ratio >= self._image_area_ocr_threshold()

    def _image_area_ocr_threshold(self) -> float:
        return max(
            0.0,
            float(getattr(get_settings().rag, "ocr_image_area_threshold", 0.30)),
        )

    def estimate_image_area_ratio(self, page: Any) -> float:
        """Estimate how much of a PDF page is occupied by embedded images."""
        try:
            page_rect = page.rect
            page_area = float(page_rect.width * page_rect.height)
        except Exception:
            page_area = 0.0
        if page_area <= 0:
            return 0.0

        rects = []
        try:
            page_dict = page.get_text("dict") or {}
            for block in page_dict.get("blocks", []) or []:
                if block.get("type") != 1:
                    continue
                bbox = block.get("bbox")
                if not bbox or len(bbox) != 4:
                    continue
                rects.append(self._fitz_rect(bbox))
        except Exception:
            rects = []

        if not rects:
            try:
                for image in page.get_images(full=True):
                    xref = image[0]
                    rects.extend(page.get_image_rects(xref) or [])
            except Exception:
                rects = []

        area = 0.0
        for rect in rects:
            try:
                clipped = rect & page.rect
                area += max(0.0, float(clipped.width * clipped.height))
            except Exception:
                continue
        return min(1.0, area / page_area)

    @staticmethod
    def _fitz_rect(bbox: Any) -> Any:
        import fitz

        return fitz.Rect(bbox)

    def merge_native_and_ocr_text(self, native_text: str, ocr_text: str) -> str:
        native = (native_text or "").strip()
        ocr = (ocr_text or "").strip()
        if not ocr:
            return native
        if not native:
            return ocr

        native_norm = self.clean_page_text(native)
        ocr_norm = self.clean_page_text(ocr)
        if not ocr_norm:
            return native
        if native_norm and ocr_norm in native_norm:
            return native
        if native_norm and native_norm in ocr_norm:
            return ocr
        return f"{native}\n\n[OCR]\n{ocr}"

    def collect_done_ocr_futures(
        self,
        futures_by_page: Dict[int, Any],
        page_by_future: Dict[Any, int],
        ocr_results: Dict[int, Tuple[str, Optional[str]]],
    ) -> None:
        """
        收割已完成的 OCR future（无阻塞）。

        说明：仅处理 `future.done()` 的任务，不等待未完成任务，避免调度线程被卡住。
        """
        done_futures = [future for future in page_by_future.keys() if future.done()]
        for future in done_futures:
            page_number = page_by_future.pop(future, None)
            if page_number is None:
                continue
            futures_by_page.pop(page_number, None)
            self.consume_ocr_future_result(page_number, future, ocr_results)

    def wait_one_ocr_future(
        self,
        futures_by_page: Dict[int, Any],
        page_by_future: Dict[Any, int],
        ocr_results: Dict[int, Tuple[str, Optional[str]]],
        ocr_timeout: int,
    ) -> None:
        """
        阻塞等待至少一个 OCR 结果，超时则按页记录错误并释放窗口。
        """
        if not futures_by_page:
            return

        from concurrent.futures import FIRST_COMPLETED, wait

        done, _ = wait(
            list(page_by_future.keys()),
            timeout=ocr_timeout,
            return_when=FIRST_COMPLETED,
        )
        if not done:
            # 若全部未完成，主动超时最早提交的任务，防止窗口被长期占满。
            oldest_page, oldest_future = next(iter(futures_by_page.items()))
            futures_by_page.pop(oldest_page, None)
            page_by_future.pop(oldest_future, None)
            oldest_future.cancel()
            ocr_results[oldest_page] = ("", f"OCR 超时 ({ocr_timeout}s)")
            self.logger.warning("OCR 超时: page=%s timeout=%ss", oldest_page, ocr_timeout)
            return

        for future in done:
            page_number = page_by_future.pop(future, None)
            if page_number is None:
                continue
            futures_by_page.pop(page_number, None)
            self.consume_ocr_future_result(page_number, future, ocr_results)

    def consume_ocr_future_result(
        self,
        page_number: int,
        future: Any,
        ocr_results: Dict[int, Tuple[str, Optional[str]]],
    ) -> None:
        """统一解析 OCR 进程池返回结果，落盘到 `ocr_results`。"""
        try:
            payload = future.result()
            if isinstance(payload, dict):
                text = str(payload.get("text") or "")
                err = payload.get("error")
                ocr_results[page_number] = (text, str(err) if err else None)
                render_s = float(payload.get("render_s") or 0.0)
                ocr_s = float(payload.get("ocr_s") or 0.0)
                post_s = float(payload.get("postprocess_s") or 0.0)
                elapsed_s = float(payload.get("elapsed_s") or 0.0)
                tile_count = int(payload.get("tile_count") or 0)
                self.logger.info(
                    "OCR 耗时分解: page=%s render=%.3fs ocr=%.3fs postprocess=%.3fs total=%.3fs tiles=%s err=%s",
                    page_number,
                    render_s,
                    ocr_s,
                    post_s,
                    elapsed_s,
                    tile_count,
                    (str(err) if err else "-"),
                )
            else:
                ocr_results[page_number] = ("", "ocr_invalid_payload")
        except Exception as e:
            ocr_results[page_number] = ("", str(e))
            self.logger.warning("OCR 异常: page=%s err=%s", page_number, e)

    def cancel_ocr_futures(
        self,
        futures_by_page: Dict[int, Any],
        page_by_future: Dict[Any, int],
        worker_cancel_event: Optional[Any] = None,
    ) -> None:
        """取消尚未完成的 OCR 任务，释放 in-flight 窗口。"""
        if worker_cancel_event is not None:
            try:
                worker_cancel_event.set()
            except Exception as e:
                self.logger.warning("设置 OCR 跨进程取消信号失败: %s", e)
        for future in futures_by_page.values():
            if not future.done():
                future.cancel()
        futures_by_page.clear()
        page_by_future.clear()

    def ocr_from_image_bytes(self, png_bytes: bytes) -> Tuple[str, Optional[str]]:
        """
        从 PNG 图片字节做 OCR（复用 ocr_worker 统一实现）。

        Args:
            png_bytes: PNG 格式图片字节

        Returns:
            (ocr_text, error_message) — 成功时 error_message 为 None
        """
        import time as _time
        from app.core.rag.ocr_worker import ocr_image_bytes

        page_start = _time.monotonic()
        try:
            text, err = ocr_image_bytes(
                png_bytes=png_bytes,
                config=self._ocr_runtime_config,
            )
            elapsed = _time.monotonic() - page_start
            if err:
                self.logger.warning("PDF OCR 失败 (%.1fs)，将降级为原文本: %s", elapsed, err)
                return "", err
            self.logger.info("OCR 单页完成: %s chars, 耗时 %.1fs", len(text), elapsed)
            return text, None
        except Exception as e:
            elapsed = _time.monotonic() - page_start
            self.logger.warning("PDF OCR 失败 (%.1fs)，将降级为原文本: %s", elapsed, e)
            return "", str(e)
    

