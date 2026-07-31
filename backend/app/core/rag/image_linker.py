"""
DeluData 智能问数系统 - 图片关联服务

单一职责：管理文本切片与图片的语义关联

设计原则：
- 页码过滤：先按页码收集候选，减少计算量
- 语义匹配：使用 Embedding 计算相似度
- 置信度返回：返回匹配分数便于前端决策
"""
import asyncio
from typing import List, Dict, Set, Optional, Any
from dataclasses import dataclass
import logging

import numpy as np

from app.config import get_settings
from app.core.llm.async_embedding import get_async_embedding


# ========== 数据结构 ==========

@dataclass
class ImageMatch:
    """
    图片匹配结果
    
    Attributes:
        image_id: 图片 ID
        confidence: 置信度分数 (0.0 ~ 1.0)
        match_type: 匹配类型 (page_match | semantic_match | fallback)
    """
    image_id: str
    confidence: float
    match_type: str  # page_match | semantic_match | fallback


@dataclass
class ChunkImageLink:
    """
    切片-图片关联结果
    
    Attributes:
        chunk_id: 切片 ID
        matches: 匹配的图片列表（按置信度降序）
    """
    chunk_id: str
    matches: List[ImageMatch]
    
    @property
    def image_ids(self) -> List[str]:
        """获取所有匹配的图片 ID"""
        return [m.image_id for m in self.matches]
    
    @property
    def top_match(self) -> Optional[ImageMatch]:
        """获取最佳匹配"""
        return self.matches[0] if self.matches else None


# ========== 图片关联服务 ==========

class ImageLinker:
    """
    图片关联服务 - 处理 Chunk-图片 的语义匹配
    
    策略：
    1. 页码过滤：按页码收集候选图片（PDF 专用）
    2. 语义匹配：使用 Embedding 计算余弦相似度
    3. 阈值过滤 + Fallback：兜底保证至少有一个关联
    
    特性：
    - 返回置信度分数，便于前端决策展示方式
    - 支持批量处理，减少 Embedding API 调用
    """
    
    def __init__(
        self,
        semantic_threshold: Optional[float] = None,
        fallback_threshold: Optional[float] = None
    ):
        """
        初始化图片关联器
        
        Args:
            semantic_threshold: 语义匹配阈值，默认从配置读取
            fallback_threshold: 兜底匹配阈值，默认从配置读取
        """
        settings = get_settings()
        self.semantic_threshold = semantic_threshold or settings.rag.image_semantic_threshold
        self.fallback_threshold = fallback_threshold or settings.rag.image_fallback_threshold
        self.logger = logging.getLogger("rag.image_linker")
    
    async def link_images_to_chunks(
        self,
        chunks: List[Any],
        images: List[Any],
        image_summaries: Dict[str, str],
        image_by_page: Optional[Dict[int, List[str]]] = None
    ) -> Dict[str, ChunkImageLink]:
        """
        批量关联图片到切片
        
        策略优先级：
        1. [v2.5] 精确 ID 匹配：如果 chunk 包含 [IMAGE:ID] 标记，直接关联
        2. 页码过滤：按页码筛选候选（PDF）
        3. 语义匹配：使用 Embedding 计算相似度
        
        Args:
            chunks: 文本切片列表（需有 chunk_id, content, metadata.page_numbers）
            images: 图片列表（需有 id, page_number）
            image_summaries: 图片 ID -> VLM 摘要映射
            image_by_page: 页码 -> 图片ID列表映射（可选，不提供则自动构建）
            
        Returns:
            {chunk_id: ChunkImageLink} 关联结果映射
        """
        if not chunks or not images:
            return {}
        
        settings = get_settings()
        all_image_ids = {img.id for img in images}
        result: Dict[str, ChunkImageLink] = {}
        
        # ========== [策略1] 精确 ID 匹配 (v2.5) ==========
        # 优先使用 [IMAGE:ID] 标记进行精确关联
        precise_match_count = 0
        if settings.rag.enable_precise_image_link:
            for chunk in chunks:
                raw_linked_ids = chunk.metadata.get("linked_image_ids", [])
                
                # [Refactor] Schema Validation: 确保一定是列表，增强健壮性
                if isinstance(raw_linked_ids, str):
                    linked_ids = [pid.strip() for pid in raw_linked_ids.split(",") if pid.strip()]
                elif isinstance(raw_linked_ids, list):
                    linked_ids = [str(pid) for pid in raw_linked_ids]
                else:
                    linked_ids = []
                
                if linked_ids:
                    # 过滤出实际存在的图片 ID
                    valid_ids = [img_id for img_id in linked_ids if img_id in all_image_ids]
                    if valid_ids:
                        matches = [
                            ImageMatch(
                                image_id=img_id,
                                confidence=1.0,
                                match_type="precise_id_match"
                            )
                            for img_id in valid_ids
                        ]
                        result[chunk.chunk_id] = ChunkImageLink(
                            chunk_id=chunk.chunk_id,
                            matches=matches
                        )
                        precise_match_count += 1
        
        if precise_match_count > 0:
            self.logger.info(f"[v2.5] 精确 ID 匹配: {precise_match_count} chunks")
        
        # 过滤掉已精确匹配的 chunks，剩余的继续使用页码+语义匹配
        remaining_chunks = [c for c in chunks if c.chunk_id not in result]
        
        if not remaining_chunks:
            return result
        
        # ========== [策略2/3] 页码过滤 + 语义匹配 ==========
        # 1. 构建页码-图片映射
        if image_by_page is None:
            image_by_page = self._build_page_image_map(images)
        
        all_image_ids_list = [img.id for img in images]
        
        # 2. 检测是否有有效页码
        has_valid_pages = self._detect_valid_pages(remaining_chunks)
        
        # 3. 按页码收集候选
        chunk_to_candidates: Dict[str, List[str]] = {}
        for chunk in remaining_chunks:
            page_numbers = chunk.metadata.get('page_numbers', [])
            valid_pages = [p for p in page_numbers if p > 0]
            
            if has_valid_pages and valid_pages:
                # PDF：按页码筛选
                candidate_ids = set()
                for page in valid_pages:
                    candidate_ids.update(image_by_page.get(page, []))
                chunk_to_candidates[chunk.chunk_id] = list(candidate_ids)
            else:
                # Word 等无页码文档：所有图片都是候选
                chunk_to_candidates[chunk.chunk_id] = all_image_ids_list
        
        # 4. 筛选需要语义匹配的切片
        chunks_need_semantic = []
        if has_valid_pages:
            chunks_need_semantic = [
                c for c in remaining_chunks 
                if len(chunk_to_candidates.get(c.chunk_id, [])) > 1
            ]
        else:
            # 无页码：所有切片都需要语义匹配
            chunks_need_semantic = remaining_chunks if images else []
        
        # 5. 执行语义匹配
        semantic_results: Dict[str, List[ImageMatch]] = {}
        if chunks_need_semantic and image_summaries:
            try:
                semantic_results = await self._match_semantic(
                    chunks_need_semantic,
                    chunk_to_candidates,
                    image_summaries
                )
            except Exception as e:
                self.logger.warning(f"语义匹配失败，退回页码关联: {e}")
        
        # 6. 构建最终结果（合并到已有的 result）
        for chunk in remaining_chunks:
            chunk_id = chunk.chunk_id
            candidates = chunk_to_candidates.get(chunk_id, [])
            
            if chunk_id in semantic_results:
                # 使用语义匹配结果
                matches = semantic_results[chunk_id]
            elif len(candidates) == 1:
                # 单候选：直接使用，置信度为 1.0（页码精确匹配）
                matches = [ImageMatch(
                    image_id=candidates[0],
                    confidence=1.0,
                    match_type="page_match"
                )]
            else:
                # 多候选无语义结果：全部使用，置信度按均分
                confidence = 1.0 / len(candidates) if candidates else 0
                matches = [
                    ImageMatch(image_id=img_id, confidence=confidence, match_type="page_match")
                    for img_id in candidates
                ]
            
            result[chunk_id] = ChunkImageLink(chunk_id=chunk_id, matches=matches)
        
        self.logger.info(
            f"图片关联完成: {len(chunks)} chunks, "
            f"{len(images)} images, "
            f"{len(semantic_results)} semantic matches, "
            f"{precise_match_count} precise matches"
        )
        
        return result
    
    async def _match_semantic(
        self,
        chunks: List[Any],
        chunk_to_candidates: Dict[str, List[str]],
        image_summaries: Dict[str, str]
    ) -> Dict[str, List[ImageMatch]]:
        """
        语义匹配核心逻辑：基于 VLM 摘要计算 chunk-图片相似度
        
        Args:
            chunks: 需要语义匹配的切片列表
            chunk_to_candidates: 切片候选图片映射
            image_summaries: 图片摘要映射
            
        Returns:
            {chunk_id: [ImageMatch]} 匹配结果
        """
        if not chunks or not image_summaries:
            return {}
        
        # 1. 收集所有候选图片 ID
        all_candidate_ids: Set[str] = set()
        for chunk in chunks:
            all_candidate_ids.update(chunk_to_candidates.get(chunk.chunk_id, []))
        
        # 过滤出有摘要的图片
        valid_image_ids = [
            img_id for img_id in all_candidate_ids 
            if img_id in image_summaries
        ]
        if not valid_image_ids:
            return {}
        
        # 2. 准备文本：截取头尾各 300 字避免过长
        chunk_texts = []
        for chunk in chunks:
            text = chunk.content if hasattr(chunk, 'content') else str(chunk)
            if len(text) > 600:
                text = text[:300] + " ... " + text[-300:]
            chunk_texts.append(text)
        
        summary_texts = [image_summaries[img_id] for img_id in valid_image_ids]
        
        # 3. 批量计算向量
        embedding_client = get_async_embedding()
        all_texts = chunk_texts + summary_texts
        all_vecs = await embedding_client.embed_texts(all_texts)
        
        chunk_vecs = all_vecs[:len(chunk_texts)]
        image_vecs = all_vecs[len(chunk_texts):]
        
        # 4. Numpy 矩阵运算 - 余弦相似度
        chunk_matrix = np.array(chunk_vecs)  # [N, D]
        image_matrix = np.array(image_vecs)  # [M, D]
        
        similarities = chunk_matrix @ image_matrix.T
        norms_c = np.linalg.norm(chunk_matrix, axis=1, keepdims=True)
        norms_i = np.linalg.norm(image_matrix, axis=1, keepdims=True)
        similarities = similarities / (norms_c @ norms_i.T + 1e-8)
        
        # 5. 阈值过滤 + Fallback
        result: Dict[str, List[ImageMatch]] = {}
        
        for i, chunk in enumerate(chunks):
            chunk_id = chunk.chunk_id if hasattr(chunk, 'chunk_id') else str(i)
            scores = similarities[i]
            
            # 获取该 chunk 的候选图片
            candidates = chunk_to_candidates.get(chunk_id, [])
            
            matches: List[ImageMatch] = []
            
            for j, img_id in enumerate(valid_image_ids):
                if img_id not in candidates:
                    continue  # 只考虑候选范围内的图片
                
                score = float(scores[j])
                if score >= self.semantic_threshold:
                    matches.append(ImageMatch(
                        image_id=img_id,
                        confidence=score,
                        match_type="semantic_match"
                    ))
            
            # Fallback：都没匹配到时取最高分的 Top-1
            if not matches and candidates:
                # 找候选中得分最高的
                best_score = 0.0
                best_img_id = None
                for j, img_id in enumerate(valid_image_ids):
                    if img_id in candidates and scores[j] > best_score:
                        best_score = scores[j]
                        best_img_id = img_id
                
                if best_img_id and best_score >= self.fallback_threshold:
                    matches.append(ImageMatch(
                        image_id=best_img_id,
                        confidence=best_score,
                        match_type="fallback"
                    ))
            
            if matches:
                # 按置信度降序排序
                matches.sort(key=lambda m: m.confidence, reverse=True)
                result[chunk_id] = matches
        
        return result
    
    def _build_page_image_map(self, images: List[Any]) -> Dict[int, List[str]]:
        """构建页码-图片映射"""
        page_map: Dict[int, List[str]] = {}
        for img in images:
            page = getattr(img, 'page_number', 0)
            if page not in page_map:
                page_map[page] = []
            page_map[page].append(img.id)
        return page_map
    
    def _detect_valid_pages(self, chunks: List[Any]) -> bool:
        """检测是否有有效页码信息"""
        for chunk in chunks:
            page_numbers = chunk.metadata.get('page_numbers', [])
            valid_pages = [p for p in page_numbers if p > 0]
            if valid_pages:
                return True
        return False


# ========== 单例工厂函数 ==========

_linker_instance: Optional[ImageLinker] = None


def get_image_linker() -> ImageLinker:
    """
    获取 ImageLinker 单例
    
    Returns:
        ImageLinker 实例
    """
    global _linker_instance
    if _linker_instance is None:
        _linker_instance = ImageLinker()
    return _linker_instance
