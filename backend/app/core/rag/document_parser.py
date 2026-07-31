"""
DeluData 智能问数系统 - 文档解析器

单一职责：将文件转换为纯文本，支持 PDF/Word/Markdown

设计原则：
- 异步优先：使用 asyncio.to_thread 包装同步解析
- 异常明确：定义具体异常类型便于上层处理
- 可扩展：预留流式处理接口
"""
import asyncio
from typing import Any, Callable, Dict, Iterator, Optional, Tuple
from pathlib import Path
import logging
from threading import Event

from app.config import get_settings
from app.core.utils.storage_path import resolve_storage_path
from app.core.rag.document_parse_errors import (
    DocumentParseError,
    EncryptedFileError,
    FileTooLargeError,
    LegacyFormatError,
    UnsupportedFormatError,
)
from app.core.rag.pdf_detailed_pipeline import PdfDetailedPipeline


# ========== 文档解析器 ==========

class DocumentParser:
    """
    文档解析器 - 单一职责：将文件转换为纯文本

    支持格式：
    - PDF (PyMuPDF, 保留页码标记)
    - DOCX (python-docx)
    - MD/TXT (纯文本)

    特性：
    - 异步解析（避免阻塞事件循环）
    - 大小限制（防止 OOM）
    - 页码保留（PDF 专用）
    """

    def __init__(self, max_file_size: Optional[int] = None):
        """
        初始化解析器

        Args:
            max_file_size: 最大文件大小（字节），默认从 RAG 配置读取
        """
        settings = get_settings()
        if max_file_size is None:
            max_file_size = settings.rag.max_file_size_mb * 1024 * 1024
        self.max_file_size = max_file_size
        self.ocr_text_len_threshold = max(0, int(settings.rag.ocr_text_len_threshold))
        self.ocr_dpi = max(72, int(settings.rag.ocr_dpi))
        max_ocr_image_height_px = max(512, int(settings.rag.ocr_worker_max_ocr_image_height_px))
        self._ocr_runtime_config = {
            "ort_intra_threads": max(1, int(settings.rag.ocr_ort_intra_threads)),
            "ort_inter_threads": max(1, int(settings.rag.ocr_ort_inter_threads)),
            "device": str(settings.rag.ocr_device).strip().lower(),
            "gpu_fallback_to_cpu": bool(settings.rag.ocr_gpu_fallback_to_cpu),
            "max_ocr_image_height_px": max_ocr_image_height_px,
            "tile_threshold": max_ocr_image_height_px,
            "tile_overlap_ratio": 0.05,
            "det_db_box_thresh": float(settings.rag.ocr_det_db_box_thresh),
            "det_db_unclip_ratio": float(settings.rag.ocr_det_db_unclip_ratio),
        }
        self.logger = logging.getLogger("rag.document_parser")
        self._pdf_pipeline = PdfDetailedPipeline(
            max_file_size=self.max_file_size,
            ocr_text_len_threshold=self.ocr_text_len_threshold,
            ocr_dpi=self.ocr_dpi,
            ocr_runtime_config=self._ocr_runtime_config,
            logger=self.logger,
        )

    async def parse(self, file_path: Path) -> str:
        """
        异步解析文件，返回带页码标记的纯文本

        Args:
            file_path: 文件路径

        Returns:
            解析后的文本内容（PDF 会包含 [PAGE:x] 标记）

        Raises:
            DocumentParseError: 解析失败时抛出具体子类异常
        """
        return await asyncio.to_thread(self._parse_sync, file_path)

    async def parse_stream(self, file_path: Path) -> Iterator[str]:
        """
        [预留] 流式解析文件，逐块返回文本

        用于未来支持超大文档（>100MB）的场景

        Args:
            file_path: 文件路径

        Yields:
            文本块（每块约 10KB）
        """
        text = await self.parse(file_path)
        yield text

    async def parse_pdf_detailed(
        self,
        file_path: Path,
        cancel_event: Optional[Event] = None,
        progress_callback: Optional[Callable[[str, int, Optional[dict[str, Any]]], None]] = None,
    ) -> Dict[str, Any]:
        """PDF 详细解析（页级质量判定 + OCR 兜底）。"""
        settings = get_settings()
        pipeline_mode = str(settings.rag.ocr_pipeline_mode).strip().lower()
        if pipeline_mode == "windowed_v2":
            return await asyncio.to_thread(
                self._parse_pdf_detailed_windowed_sync,
                file_path,
                cancel_event,
                progress_callback,
            )
        return await asyncio.to_thread(
            self._parse_pdf_detailed_sync,
            file_path,
            cancel_event,
            progress_callback,
        )

    def _parse_sync(self, file_path: Path) -> str:
        """同步解析核心逻辑。"""
        file_path = Path(resolve_storage_path(str(file_path)))

        if not file_path.exists():
            raise DocumentParseError(f"文件不存在: {file_path}")

        file_size = file_path.stat().st_size
        if file_size > self.max_file_size:
            raise FileTooLargeError(file_size, self.max_file_size)

        suffix = file_path.suffix.lower()
        if suffix in (".txt", ".md"):
            return self._parse_text(file_path)
        if suffix == ".pdf":
            return self._parse_pdf(file_path)
        if suffix == ".docx":
            return self._parse_docx(file_path)
        if suffix == ".doc":
            raise LegacyFormatError(file_path.name)
        raise UnsupportedFormatError(file_path.name, suffix)

    def _parse_text(self, file_path: Path) -> str:
        """解析纯文本文件（TXT/MD）。"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read(self.max_file_size)
        except UnicodeDecodeError:
            from app.core.utils.file_utils import detect_encoding

            encoding = detect_encoding(file_path)
            with open(file_path, "r", encoding=encoding) as f:
                return f.read(self.max_file_size)

    def _parse_pdf(self, file_path: Path) -> str:
        """解析 PDF 文件，保留页码标记供后续切分使用。"""
        import fitz

        settings = get_settings()
        page_template = settings.rag.page_marker_template
        pages_text = []

        try:
            with fitz.open(str(file_path)) as doc:
                if doc.is_encrypted:
                    raise EncryptedFileError(file_path.name)
                for page_num, page in enumerate(doc, start=1):
                    text = page.get_text()
                    if text.strip():
                        pages_text.append(f"{page_template.format(page_num)}\n{text}")
        except fitz.FileDataError as e:
            raise DocumentParseError(f"PDF 文件损坏或格式错误: {e}")

        return "\n\n".join(pages_text)

    def _parse_pdf_detailed_windowed_sync(
        self,
        file_path: Path,
        cancel_event: Optional[Event] = None,
        progress_callback: Optional[Callable[[str, int, Optional[dict[str, Any]]], None]] = None,
    ) -> Dict[str, Any]:
        return self._pdf_pipeline.parse_windowed_sync(
            file_path,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )

    def _parse_pdf_detailed_sync(
        self,
        file_path: Path,
        cancel_event: Optional[Event] = None,
        progress_callback: Optional[Callable[[str, int, Optional[dict[str, Any]]], None]] = None,
    ) -> Dict[str, Any]:
        return self._pdf_pipeline.parse_legacy_sync(
            file_path,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
            ocr_from_image_bytes=self._ocr_from_image_bytes,
        )

    def _clean_page_text(self, text: str) -> str:
        return self._pdf_pipeline.clean_page_text(text)

    def _should_run_ocr(
        self,
        extract_failed: bool,
        cleaned_text_length: int,
        image_area_ratio: float = 0.0,
    ) -> bool:
        return self._pdf_pipeline.should_run_ocr(
            extract_failed,
            cleaned_text_length,
            image_area_ratio=image_area_ratio,
        )

    def _collect_done_ocr_futures(
        self,
        futures_by_page: Dict[int, Any],
        page_by_future: Dict[Any, int],
        ocr_results: Dict[int, Tuple[str, Optional[str]]],
    ) -> None:
        self._pdf_pipeline.collect_done_ocr_futures(futures_by_page, page_by_future, ocr_results)

    def _wait_one_ocr_future(
        self,
        futures_by_page: Dict[int, Any],
        page_by_future: Dict[Any, int],
        ocr_results: Dict[int, Tuple[str, Optional[str]]],
        ocr_timeout: int,
    ) -> None:
        self._pdf_pipeline.wait_one_ocr_future(
            futures_by_page,
            page_by_future,
            ocr_results,
            ocr_timeout,
        )

    def _consume_ocr_future_result(
        self,
        page_number: int,
        future: Any,
        ocr_results: Dict[int, Tuple[str, Optional[str]]],
    ) -> None:
        self._pdf_pipeline.consume_ocr_future_result(page_number, future, ocr_results)

    def _cancel_ocr_futures(
        self,
        futures_by_page: Dict[int, Any],
        page_by_future: Dict[Any, int],
        worker_cancel_event: Optional[Any] = None,
    ) -> None:
        self._pdf_pipeline.cancel_ocr_futures(
            futures_by_page,
            page_by_future,
            worker_cancel_event=worker_cancel_event,
        )

    def _ocr_from_image_bytes(self, png_bytes: bytes) -> Tuple[str, Optional[str]]:
        return self._pdf_pipeline.ocr_from_image_bytes(png_bytes)

    def _parse_docx(self, file_path: Path) -> str:
        """解析 Word 文档（DOCX）。"""
        import docx
        from docx.oxml.ns import qn
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        def iter_block_items(document):
            for child in document.element.body.iterchildren():
                if isinstance(child, CT_P):
                    yield Paragraph(child, document)
                elif isinstance(child, CT_Tbl):
                    yield Table(child, document)

        def count_page_breaks(element) -> int:
            page_break_count = 0
            for br in element.findall(".//w:br", element.nsmap):
                if br.get(qn("w:type")) == "page":
                    page_break_count += 1
            page_break_count += len(
                element.findall(".//w:lastRenderedPageBreak", element.nsmap)
            )
            return page_break_count

        def extract_table_text(table: Table) -> str:
            row_lines: list[str] = []
            for row in table.rows:
                cell_texts: list[str] = []
                seen_cells: set[int] = set()
                for cell in row.cells:
                    cell_id = id(cell._tc)
                    if cell_id in seen_cells:
                        continue
                    seen_cells.add(cell_id)
                    text = cell.text.strip()
                    if text:
                        cell_texts.append(text)
                if cell_texts:
                    row_lines.append(" | ".join(cell_texts))
            return "\n".join(row_lines).strip()

        try:
            doc = docx.Document(file_path)
            page_number = 1
            lines = [f"[PAGE:{page_number}]"]

            for block in iter_block_items(doc):
                block_element = block._element
                page_break_count = count_page_breaks(block_element)

                if page_break_count > 0:
                    for _ in range(page_break_count):
                        page_number += 1
                        lines.append(f"[PAGE:{page_number}]")

                if isinstance(block, Paragraph):
                    text = block.text.strip()
                else:
                    text = extract_table_text(block)
                if text:
                    lines.append(text)

            return "\n\n".join(lines)
        except Exception as e:
            if "encrypted" in str(e).lower():
                raise EncryptedFileError(file_path.name)
            raise DocumentParseError(f"DOCX 解析失败: {e}")


# ========== 单例工厂函数 ==========

_parser_instance: Optional[DocumentParser] = None


def get_document_parser() -> DocumentParser:
    """获取 DocumentParser 单例。"""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = DocumentParser()
    return _parser_instance
