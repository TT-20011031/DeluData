"""
DeluData 智能问数系统 - 文档入库服务

单一职责：编排文档解析、切分、向量化、存储的完整流程

设计原则：
- 流程编排：协调多个子模块完成入库
- 补偿机制：失败时清理已产生的垃圾数据
- 可观测性：详细日志记录每个阶段耗时
"""
import asyncio
import json
from typing import Any, Callable, Dict, List, Optional
from pathlib import Path
import logging
import time
import re
import hashlib
from threading import Event

import chromadb
from sqlalchemy import delete

from app.models.common.context import UserContext
from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.core.utils.storage_path import resolve_storage_path
from app.models.knowledge.graph import DocumentImage

# 核心依赖模块
from app.core.rag.document_parser import DocumentParser, get_document_parser, DocumentParseError
from app.core.rag.semantic_chunker import SemanticChunker, SemanticChunk, get_semantic_chunker
from app.core.rag.noise_filter import NoiseFilter
from app.core.rag.image_linker import ImageLinker, get_image_linker
from app.core.rag.summary_service import get_summary_service
from app.core.rag.ingestion_progress import ThreadsafeProgressProxy, emit_progress
from app.core.llm.async_embedding import get_async_embedding


class DocumentIngestor:
    """
    文档入库服务 - 编排完整的文档入库流程
    
    流程：
    1. 调用 DocumentParser 解析文本
    2. 调用 ImageService 提取图片
    3. （可选）调用 VLMService 生成图片描述
    4. 调用 SemanticChunker 语义切分
    5. 调用 ImageLinker 关联图片
    6. 批量向量化并存储到 ChromaDB
    7. 提交后台摘要任务
    
    特性：
    - 依赖注入：支持传入 mock 便于测试
    - 补偿机制：失败时清理垃圾数据
    - 可观测性：详细日志记录
    """
    
    COLLECTION_NAME = "tenant_docs"
    
    def __init__(
        self,
        chroma_client: chromadb.ClientAPI,
        parser: Optional[DocumentParser] = None,
        chunker: Optional[SemanticChunker] = None,
        image_linker: Optional[ImageLinker] = None
    ):
        """
        初始化文档入库服务
        
        Args:
            chroma_client: ChromaDB 客户端（必须）
            parser: 文档解析器（可选，默认使用单例）
            chunker: 语义切分器（可选，默认使用单例）
            image_linker: 图片关联器（可选，默认使用单例）
        """
        self.client = chroma_client
        self._collection: Optional[chromadb.Collection] = None
        
        # 依赖注入 - 支持测试时传入 mock
        self._parser = parser
        self._chunker = chunker
        self._image_linker = image_linker
        self._noise_filter: Optional[NoiseFilter] = None
        self._page_marker_regex: Optional[str] = None
        self._page_marker_pattern = None
        
        self.logger = logging.getLogger("rag.document_ingestor")
    
    @property
    def collection(self) -> chromadb.Collection:
        """懒加载 Collection"""
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"description": "User uploaded documents for RAG"}
            )
        return self._collection
    
    @property
    def parser(self) -> DocumentParser:
        if self._parser is None:
            self._parser = get_document_parser()
        return self._parser
    
    @property
    def chunker(self) -> SemanticChunker:
        if self._chunker is None:
            self._chunker = get_semantic_chunker()
        return self._chunker
    
    @property
    def image_linker(self) -> ImageLinker:
        if self._image_linker is None:
            self._image_linker = get_image_linker()
        return self._image_linker

    @property
    def noise_filter(self) -> NoiseFilter:
        if self._noise_filter is None:
            rag_settings = get_settings().rag
            self._noise_filter = NoiseFilter(
                marker_density_threshold=float(getattr(rag_settings, "noise_marker_density_threshold", 0.02)),
                min_readable_ratio=float(getattr(rag_settings, "noise_min_readable_ratio", 0.2)),
                repeat_ratio_threshold=float(getattr(rag_settings, "noise_repeat_ratio_threshold", 0.45)),
            )
        return self._noise_filter

    def _get_page_marker_pattern(self):
        regex = str(get_settings().rag.page_marker_regex)
        if self._page_marker_pattern is None or self._page_marker_regex != regex:
            self._page_marker_regex = regex
            self._page_marker_pattern = re.compile(regex, re.MULTILINE)
        return self._page_marker_pattern

    def _chunk_pdf_fixed(
        self,
        text: str,
        file_id: str,
        source_file: str,
        workspace_id: str,
    ) -> List[SemanticChunk]:
        """
        PDF 固定切片：默认 500 字符、150 重叠（可配置）。
        按页码切片，确保每个 chunk 保留 page_numbers。
        """
        settings = get_settings()
        chunk_size = max(1, int(settings.rag.pdf_fixed_chunk_size))
        overlap = max(0, int(settings.rag.pdf_fixed_chunk_overlap))
        if overlap >= chunk_size:
            overlap = max(0, chunk_size - 1)
        stride = max(1, chunk_size - overlap)

        pages = self._split_pdf_pages(text)
        chunks: List[SemanticChunk] = []
        chunk_index = 0
        previous_chunk_id: Optional[str] = None

        for page_number, page_text in pages:
            normalized_text = (page_text or "").strip()
            if not normalized_text:
                continue

            text_length = len(normalized_text)
            if text_length <= chunk_size:
                content = normalized_text
                chunk_id = self._build_chunk_id(file_id, chunk_index)
                chunk = SemanticChunk(
                    content=content,
                    chunk_id=chunk_id,
                    chunk_index=chunk_index,
                    prev_id=previous_chunk_id,
                    parent_id=file_id,
                    header_path=None,
                    summary=None,
                    metadata={
                        "workspace_id": workspace_id,
                        "file_id": file_id,
                        "source_file": source_file,
                        "chunk_index": chunk_index,
                        "page_numbers": [page_number],
                        "linked_image_ids": [],
                    },
                )
                if chunks:
                    chunks[-1].next_id = chunk_id
                chunks.append(chunk)
                previous_chunk_id = chunk_id
                chunk_index += 1
                continue

            start = 0
            while start < text_length:
                end = min(start + chunk_size, text_length)
                content = normalized_text[start:end].strip()
                if not content:
                    if end >= text_length:
                        break
                    start += stride
                    continue

                chunk_id = self._build_chunk_id(file_id, chunk_index)
                chunk = SemanticChunk(
                    content=content,
                    chunk_id=chunk_id,
                    chunk_index=chunk_index,
                    prev_id=previous_chunk_id,
                    parent_id=file_id,
                    header_path=None,
                    summary=None,
                    metadata={
                        "workspace_id": workspace_id,
                        "file_id": file_id,
                        "source_file": source_file,
                        "chunk_index": chunk_index,
                        "page_numbers": [page_number],
                        "linked_image_ids": [],
                    },
                )
                if chunks:
                    chunks[-1].next_id = chunk_id
                chunks.append(chunk)
                previous_chunk_id = chunk_id
                chunk_index += 1

                if end >= text_length:
                    break
                start += stride

        return chunks

    def _split_pdf_pages(self, text: str) -> List[tuple[int, str]]:
        page_pattern = self._get_page_marker_pattern()
        matches = list(page_pattern.finditer(text))
        if not matches:
            return [(1, text or "")]

        pages: List[tuple[int, str]] = []
        for idx, match in enumerate(matches):
            try:
                page_number = int(match.group(1))
            except (TypeError, ValueError):
                page_number = idx + 1
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            page_text = text[start:end]
            pages.append((page_number, page_text))
        return pages

    @staticmethod
    def _build_chunk_id(file_id: str, chunk_index: int) -> str:
        raw = f"{file_id}_fixed_{chunk_index}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def _filter_noise_chunks(self, chunks: List[SemanticChunk]) -> tuple[List[SemanticChunk], Dict[str, Any]]:
        if not chunks:
            return [], {"dropped_count": 0, "kept_count": 0, "samples": []}

        if not bool(getattr(get_settings().rag, "noise_filter_enabled", True)):
            return chunks, {"dropped_count": 0, "kept_count": len(chunks), "samples": []}

        kept: List[SemanticChunk] = []
        dropped_samples: List[Dict[str, Any]] = []
        for chunk in chunks:
            decision = self.noise_filter.evaluate(chunk.content)
            if decision.is_noise:
                if len(dropped_samples) < 5:
                    dropped_samples.append(
                        {
                            "chunk_id": chunk.chunk_id,
                            "reason": decision.reason,
                            "metrics": decision.metrics,
                            "preview": (chunk.content or "")[:80],
                        }
                    )
                continue
            kept.append(chunk)

        for idx, chunk in enumerate(kept):
            chunk.chunk_index = idx
            if isinstance(chunk.metadata, dict):
                chunk.metadata["chunk_index"] = idx
            chunk.prev_id = kept[idx - 1].chunk_id if idx > 0 else None
            chunk.next_id = kept[idx + 1].chunk_id if idx + 1 < len(kept) else None

        report = {
            "dropped_count": len(chunks) - len(kept),
            "kept_count": len(kept),
            "samples": dropped_samples,
        }
        return kept, report
    
    async def ingest(
        self,
        file_path: str,
        user_context: UserContext,
        file_id: Optional[str] = None,
        visibility: str = "dept",
        allowed_users: Optional[List[str]] = None,
        target_dept_id: Optional[str] = None,
        cancel_event: Optional[Event] = None,
        progress_callback: Optional[Callable[[str, int, Optional[dict[str, Any]]], Any]] = None,
    ) -> Dict[str, Any]:
        """
        执行完整的文档入库流程
        
        Args:
            file_path: 文档路径
            user_context: 用户上下文（包含 workspace_id, user_id 等）
            file_id: 可选的文件 ID（若提供则使用，否则自动生成）
            visibility: 可见性 - public(全员可见), dept(部门可见), private(仅所有者)
            allowed_users: 额外允许访问的用户 ID 列表
            target_dept_id: 管理员可指定归属部门
            cancel_event: 可选取消信号（用于中止长时间入库任务）
            
        Returns:
            {
                "success": bool,
                "file_id": str,
                "chunk_count": int,
                "image_count": int,
                "file_name": str,
                "error": str (仅失败时)
            }
        """
        file_path = Path(resolve_storage_path(str(file_path)))
        start_time = time.time()
        
        # 入库过程中产生的临时数据（用于失败时清理）
        ingested_chunk_ids: List[str] = []
        ingested_image_ids: List[str] = []
        document_images_synced = False
        
        try:
            if cancel_event is not None and cancel_event.is_set():
                return {"success": False, "error": "文档处理已取消"}

            # ========== 1. 生成文件 ID ==========
            if not file_id:
                content = f"{user_context.workspace_id}_{file_path.name}"
                file_id = hashlib.md5(content.encode()).hexdigest()[:16]
            
            self.logger.info(f"开始入库: {file_path.name} -> {file_id}")
            emit_progress(
                progress_callback, "parsing", 8, {"message": "parsing_started"}
            )

            # ========== 2. 解析文档文本 ==========
            parse_start = time.time()
            detailed_parse = None
            loop = asyncio.get_running_loop()
            parser_progress_callback: Optional[
                Callable[[str, int, Optional[dict[str, Any]]], None]
            ] = None
            if progress_callback is not None:
                parser_progress_callback = ThreadsafeProgressProxy(
                    callback=progress_callback,
                    loop=loop,
                )

            if file_path.suffix.lower() == ".pdf":
                detailed_parse = await self.parser.parse_pdf_detailed(
                    file_path,
                    cancel_event=cancel_event,
                    progress_callback=parser_progress_callback,
                )
                text_content = detailed_parse.get("text", "")
            else:
                text_content = await self.parser.parse(file_path)
            
            if not text_content:
                return {"success": False, "error": "无法解析文档内容"}
            
            self.logger.info(
                f"解析完成: {len(text_content)} chars, "
                f"耗时 {time.time() - parse_start:.2f}s"
            )
            if detailed_parse:
                page_count = int(detailed_parse.get("total_pages", 0))
                ocr_count = sum(1 for p in detailed_parse.get("pages", []) if p.get("used_ocr"))
                self.logger.info(
                    "PDF 详细解析: pages=%s, ocr_pages=%s, threshold=%s",
                    page_count,
                    ocr_count,
                    get_settings().rag.ocr_text_len_threshold,
                )
                emit_progress(
                    progress_callback,
                    "parsing",
                    30,
                    {"pdf": {"total_pages": page_count, "ocr_pages": ocr_count}},
                )
            else:
                emit_progress(
                    progress_callback, "parsing", 30, {"message": "parsing_completed"}
                )

            if cancel_event is not None and cancel_event.is_set():
                return {"success": False, "error": "文档处理已取消"}
            
            # ========== 3. 图片提取 ==========
            if cancel_event is not None and cancel_event.is_set():
                return {"success": False, "error": "文档处理已取消"}

            from app.core.utils.image_service import get_image_service

            image_start = time.time()
            image_service = get_image_service()
            ingest_image_summary_enabled = bool(get_settings().rag.ingest_image_summary_enabled)

            images = await image_service.extract_images_async(
                str(file_path),
                file_id,
                user_context.workspace_id,
            )
            
            self.logger.info(
                f"图片提取完成: {len(images)} images, "
                f"耗时 {time.time() - image_start:.2f}s"
            )
            
            # ========== 4. VLM 生成图片描述 ==========
            image_summaries: Dict[str, str] = {}
            if images and ingest_image_summary_enabled:
                from app.core.llm.vlm_service import get_vlm_service
                vlm_service = get_vlm_service()
                vlm_start = time.time()
                
                async def describe_image(img):
                    prompt_type = "chart" if img.width > img.height * 1.5 else "general"
                    description = await vlm_service.describe_image(img.local_path, prompt_type)
                    return img.id, description
                
                results = await asyncio.gather(*[describe_image(img) for img in images])
                
                for img_id, description in results:
                    if description:
                        image_summaries[img_id] = description
                
                self.logger.info(
                    f"VLM 描述完成: {len(image_summaries)}/{len(images)} images, "
                    f"耗时 {time.time() - vlm_start:.2f}s"
                )
            elif images:
                self.logger.info(
                    "已禁用 image_summary 生成: file_id=%s, images=%s",
                    file_id,
                    len(images),
                )
            
            # ========== 5. 切分 ==========
            emit_progress(
                progress_callback, "chunking", 55, {"message": "chunking_started"}
            )
            chunk_start = time.time()
            if file_path.suffix.lower() == ".pdf":
                chunks = self._chunk_pdf_fixed(
                    text=text_content,
                    file_id=file_id,
                    source_file=file_path.name,
                    workspace_id=user_context.workspace_id,
                )
                self.logger.info(
                    "PDF 固定切分完成: %s chunks (size=%s overlap=%s), 耗时 %.2fs",
                    len(chunks),
                    get_settings().rag.pdf_fixed_chunk_size,
                    get_settings().rag.pdf_fixed_chunk_overlap,
                    time.time() - chunk_start,
                )
            else:
                chunks = await self.chunker.chunk_document(
                    text=text_content,
                    file_id=file_id,
                    source_file=file_path.name,
                    workspace_id=user_context.workspace_id,
                    generate_summaries=False  # 后台异步生成
                )
                self.logger.info(
                    f"语义切分完成: {len(chunks)} chunks, "
                    f"耗时 {time.time() - chunk_start:.2f}s"
                )

            if not chunks:
                self.logger.warning(
                    "文档切分后无可用文本: file=%s, file_id=%s, suffix=%s",
                    file_path.name,
                    file_id,
                    file_path.suffix.lower(),
                )
                return {
                    "success": False,
                    "error": "文档未提取到可用文本切片（可能是扫描件 OCR 失败或仅包含水印/空白内容）",
                }
            emit_progress(
                progress_callback,
                "chunking",
                70,
                {"chunk_count": len(chunks)},
            )
            chunks, noise_report = self._filter_noise_chunks(chunks)
            if noise_report["dropped_count"] > 0:
                self.logger.info(
                    "noise filter dropped %s chunks, kept=%s, samples=%s",
                    noise_report["dropped_count"],
                    noise_report["kept_count"],
                    noise_report["samples"],
                )
            if not chunks:
                return {
                    "success": False,
                    "error": "文档切片均被识别为噪声，未写入向量库",
                }

            if cancel_event is not None and cancel_event.is_set():
                return {"success": False, "error": "文档处理已取消"}
             
            # ========== 6. 图片关联 ==========
            image_by_page: Dict[int, List[str]] = {}
            if images:
                for img in images:
                    if img.page_number not in image_by_page:
                        image_by_page[img.page_number] = []
                    image_by_page[img.page_number].append(img.id)
                self.logger.info(f"图片页码映射: {dict(list(image_by_page.items())[:5])}...")
            
            link_start = time.time()
            chunk_image_links = await self.image_linker.link_images_to_chunks(
                chunks=chunks,
                images=images,
                image_summaries=image_summaries,
                image_by_page=image_by_page
            )
            
            self.logger.info(
                f"图片关联完成: {len(chunk_image_links)} links, "
                f"耗时 {time.time() - link_start:.2f}s"
            )
            
            # ========== 7. 生成向量 ==========
            if cancel_event is not None and cancel_event.is_set():
                return {"success": False, "error": "文档处理已取消"}

            emit_progress(
                progress_callback, "embedding", 75, {"message": "embedding_started"}
            )
            embed_start = time.time()
            embedding_client = get_async_embedding()
            contents = [chunk.content for chunk in chunks]
            embeddings = await embedding_client.embed_texts(contents)
            
            self.logger.info(
                f"向量化完成: {len(embeddings)} vectors, "
                f"耗时 {time.time() - embed_start:.2f}s"
            )
            emit_progress(
                progress_callback,
                "embedding",
                88,
                {"vector_count": len(embeddings)},
            )
            
            # ========== 8. 删除旧切片 ==========
            if cancel_event is not None and cancel_event.is_set():
                return {"success": False, "error": "文档处理已取消"}

            try:
                await asyncio.to_thread(
                    self.collection.delete,
                    where={"file_id": {"$eq": file_id}}
                )
            except Exception:
                pass  # 忽略删除失败
            
            # ========== 9. 存储文本切片 ==========
            emit_progress(
                progress_callback, "storing", 90, {"message": "storing_started"}
            )
            store_start = time.time()
            ids = [chunk.chunk_id for chunk in chunks]
            ingested_chunk_ids = ids.copy()  # 记录以便失败时清理
            
            metadatas = []
            for chunk in chunks:
                # 获取图片关联信息
                link = chunk_image_links.get(chunk.chunk_id)
                related_image_ids = link.image_ids if link else []
                page_numbers = chunk.metadata.get('page_numbers', [])
                linked_image_ids = chunk.metadata.get('linked_image_ids', [])  # [v2.5]
                
                metadata = {
                    "workspace_id": user_context.workspace_id,
                    "file_id": file_id,
                    "source_file": file_path.name,
                    "chunk_index": chunk.chunk_index,
                    "parent_id": chunk.parent_id or "",
                    "prev_id": chunk.prev_id or "",
                    "next_id": chunk.next_id or "",
                    "header_path": chunk.header_path or "",
                    "summary": chunk.summary or "",
                    # 权限控制
                    "visibility": visibility,
                    "owner_id": user_context.user_id,
                    "dept_id": str(target_dept_id) if target_dept_id else (
                        str(user_context.dept_id) if user_context.dept_id else ""
                    ),
                    "allowed_users": ",".join(allowed_users) if allowed_users else "",
                    # 类型和图片关联
                    "type": "text",
                    "related_image_ids": ",".join(related_image_ids),
                    "linked_image_ids": ",".join(linked_image_ids),  # [v2.5] 精确标记的图片
                    "page_numbers": ",".join(map(str, page_numbers)) if page_numbers else ""
                }
                metadatas.append(metadata)
            
            await asyncio.to_thread(
                self.collection.add,
                documents=contents,
                embeddings=embeddings,
                metadatas=metadatas,
                ids=ids
            )
            
            # ========== 10. 存储图片摘要（可选） ==========
            if ingest_image_summary_enabled and image_summaries:
                img_contents = list(image_summaries.values())
                img_embeddings = await embedding_client.embed_texts(img_contents)
                img_ids = [
                    self._get_image_chunk_id(file_id, img_id) 
                    for img_id in image_summaries.keys()
                ]
                ingested_image_ids = img_ids.copy()
                
                # 构建图片 ID -> 页码映射
                img_page_map = {img.id: img.page_number for img in images}
                
                img_metadatas = []
                for img_id in image_summaries.keys():
                    img_metadatas.append({
                        "workspace_id": user_context.workspace_id,
                        "file_id": file_id,
                        "source_file": file_path.name,
                        "type": "image_summary",
                        "image_id": img_id,
                        "page_number": str(img_page_map.get(img_id, 0)),  # 保存页码
                        "visibility": visibility,
                        "owner_id": user_context.user_id,
                        "dept_id": str(target_dept_id) if target_dept_id else (
                            str(user_context.dept_id) if user_context.dept_id else ""
                        ),
                        "allowed_users": ",".join(allowed_users) if allowed_users else ""
                    })
                
                await asyncio.to_thread(
                    self.collection.add,
                    documents=img_contents,
                    embeddings=img_embeddings,
                    metadatas=img_metadatas,
                    ids=img_ids
                )

            # ========== 10.1 同步图片元数据到 document_images ==========
            await self._persist_document_images(file_id=file_id, images=images)
            document_images_synced = True
            
            self.logger.info(
                f"存储完成: {len(chunks)} chunks + {len(image_summaries)} images, "
                f"耗时 {time.time() - store_start:.2f}s"
            )
            emit_progress(
                progress_callback,
                "storing",
                99,
                {
                    "chunk_count": len(chunks),
                    "image_count": len(images),
                },
            )
            
            # ========== 11. 提交后台摘要任务 ==========
            if cancel_event is not None and cancel_event.is_set():
                return {"success": False, "error": "文档处理已取消"}

            summary_service = get_summary_service(self.client)
            await summary_service.submit_chunks([
                {"chunk_id": chunk.chunk_id, "content": chunk.content}
                for chunk in chunks
            ])
            
            total_time = time.time() - start_time
            self.logger.info(
                f"入库完成: {file_path.name}, "
                f"chunks={len(chunks)}, images={len(images)}, "
                f"总耗时 {total_time:.2f}s"
            )
            
            return {
                "success": True,
                "file_id": file_id,
                "chunk_count": len(chunks),
                "image_count": len(images),
                "file_name": file_path.name
            }
            
        except DocumentParseError as e:
            # 解析错误 - 用户可理解的错误
            self.logger.warning(f"文档解析失败: {e}")
            return {"success": False, "error": str(e)}
            
        except Exception as e:
            # 其他错误 - 尝试清理已入库的数据
            self.logger.error(f"入库失败: {e}", exc_info=True)
            
            await self._cleanup_on_failure(ingested_chunk_ids, ingested_image_ids)
            if document_images_synced:
                await self._cleanup_document_images(file_id)
            
            return {"success": False, "error": f"入库失败: {e}"}
    
    async def _cleanup_on_failure(
        self,
        chunk_ids: List[str],
        image_ids: List[str]
    ) -> None:
        """
        失败时清理已入库的数据（补偿机制）
        
        Args:
            chunk_ids: 已入库的切片 ID 列表
            image_ids: 已入库的图片 ID 列表
        """
        if not chunk_ids and not image_ids:
            return
        
        self.logger.info(f"清理失败入库数据: {len(chunk_ids)} chunks, {len(image_ids)} images")
        
        try:
            all_ids = chunk_ids + image_ids
            if all_ids:
                await asyncio.to_thread(
                    self.collection.delete,
                    ids=all_ids
                )
        except Exception as e:
            self.logger.warning(f"清理失败: {e}")

    async def _persist_document_images(self, file_id: str, images: List[Any]) -> None:
        """
        将提取图片页码映射写入 document_images（覆盖同 file_id 历史记录）。
        """
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            await session.execute(delete(DocumentImage).where(DocumentImage.file_id == file_id))

            records: List[DocumentImage] = []
            for image in images:
                image_id = str(getattr(image, "id", "") or "").strip()
                storage_path = str(
                    getattr(image, "storage_path", "") or getattr(image, "local_path", "") or ""
                ).strip()
                if not image_id or not storage_path:
                    continue

                try:
                    page_number = int(getattr(image, "page_number", 1) or 1)
                except (TypeError, ValueError):
                    page_number = 1
                page_number = max(1, page_number)

                bbox = getattr(image, "bbox", None)
                bbox_text = json.dumps(bbox, ensure_ascii=False) if bbox else None
                phash = str(getattr(image, "phash", "") or "").strip() or None

                try:
                    width = int(getattr(image, "width", 0) or 0)
                except (TypeError, ValueError):
                    width = 0
                try:
                    height = int(getattr(image, "height", 0) or 0)
                except (TypeError, ValueError):
                    height = 0

                records.append(
                    DocumentImage(
                        file_id=file_id,
                        image_id=image_id,
                        storage_path=storage_path,
                        page_number=page_number,
                        bbox=bbox_text,
                        phash=phash,
                        width=max(0, width),
                        height=max(0, height),
                    )
                )

            if records:
                session.add_all(records)

        self.logger.info("document_images 同步完成: file_id=%s, count=%s", file_id, len(records))

    async def _cleanup_document_images(self, file_id: str) -> None:
        """
        清理 document_images 记录，避免失败流程遗留脏数据。
        """
        if not file_id:
            return
        try:
            db_manager = get_async_db_manager()
            async with db_manager.session_scope() as session:
                await session.execute(delete(DocumentImage).where(DocumentImage.file_id == file_id))
        except Exception as e:
            self.logger.warning("清理 document_images 失败: file_id=%s err=%s", file_id, e)
    
    @staticmethod
    def _get_image_chunk_id(file_id: str, image_id: str) -> str:
        """
        统一管理图片 Chunk ID 的生成规则
        
        支持新旧两种 ID 格式：
        - 新格式: "001", "002" (纯数字)
        - 旧格式: "img_abc12345" (uuid 前缀)
        """
        clean_id = image_id[4:] if image_id.startswith("img_") else image_id
        return f"{file_id}_img_{clean_id}"


# ========== 单例工厂函数 ==========

_ingestor_instance: Optional[DocumentIngestor] = None


def get_document_ingestor(chroma_client: chromadb.ClientAPI) -> DocumentIngestor:
    """
    获取 DocumentIngestor 单例
    
    Args:
        chroma_client: ChromaDB 客户端
        
    Returns:
        DocumentIngestor 实例
    """
    global _ingestor_instance
    if _ingestor_instance is None:
        _ingestor_instance = DocumentIngestor(chroma_client)
    return _ingestor_instance
