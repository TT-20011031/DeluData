"""
语义切分器 (Semantic Chunker)

基于 Embedding 相似度检测语义断点，保证每个 Chunk 内部逻辑连贯。
同时提取文档层级结构（Header Path）并维护 Chunk 链表关系。
"""
import re
import hashlib
import asyncio
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np

from app.config import get_settings
from app.core.llm.async_embedding import get_async_embedding
from app.core.llm.async_llm import get_async_llm


@dataclass
class SemanticChunk:
    """
    语义切片数据结构
    
    存储切片内容及其元数据，用于后续向量化和存储
    """
    content: str
    chunk_id: str
    chunk_index: int
    
    # 链表关系
    prev_id: Optional[str] = None
    next_id: Optional[str] = None
    parent_id: Optional[str] = None
    
    # 语义增强
    summary: Optional[str] = None
    header_path: Optional[str] = None
    
    # 向量（延迟生成）
    embedding: Optional[List[float]] = None
    
    # 额外元数据
    metadata: Dict = field(default_factory=dict)


class SemanticChunker:
    """
    语义切分器
    
    核心功能：
    1. 基于 Embedding 相似度检测语义断点
    2. 提取 Markdown/文档标题层级
    3. 为每个 Chunk 生成摘要（异步后台任务）
    4. 建立 Chunk 之间的链表关系
    """
    
    # 标题正则模式
    HEADER_PATTERNS = [
        (r'^#{1,6}\s+(.+)$', 'markdown'),  # Markdown 标题
        (r'^第[一二三四五六七八九十\d]+[章节条款][\s：:]+(.+)$', 'chinese'),  # 中文章节
        (r'^(\d+\.)+\s*(.+)$', 'numbered'),  # 编号标题
    ]
    
    def __init__(self):
        settings = get_settings()
        self.rag_settings = settings.rag
        
        # 切分参数
        self.similarity_threshold = self.rag_settings.chunk_similarity_threshold
        self.min_chunk_size = self.rag_settings.min_chunk_size
        self.max_chunk_size = self.rag_settings.max_chunk_size
        
        # [Refactor] 预编译正则，提升性能 (Zero Tech Debt)
        self.page_marker_pattern = re.compile(self.rag_settings.page_marker_regex)
        self.image_marker_pattern = re.compile(self.rag_settings.image_marker_pattern)
        
        # 客户端
        self._embedding_client = None
        self._llm_client = None
    
    @property
    def embedding_client(self):
        if self._embedding_client is None:
            self._embedding_client = get_async_embedding()
        return self._embedding_client
    
    @property
    def llm_client(self):
        if self._llm_client is None:
            self._llm_client = get_async_llm()
        return self._llm_client
    
    async def chunk_document(
        self,
        text: str,
        file_id: str,
        source_file: str,
        workspace_id: str,
        generate_summaries: bool = False
    ) -> List[SemanticChunk]:
        """
        对文档进行语义切分
        
        Args:
            text: 文档全文
            file_id: 文件唯一标识
            source_file: 源文件名
            workspace_id: 工作空间 ID
            generate_summaries: 是否立即生成摘要（False 则后台异步生成）
            
        Returns:
            SemanticChunk 列表
        """
        if not text or not text.strip():
            return []
        
        # 1. 预处理：按段落分割
        paragraphs = self._split_paragraphs(text)
        if not paragraphs:
            return []
        
        # 2. 提取标题路径
        header_paths = self._extract_header_paths(paragraphs)
        
        # 3. 计算段落向量
        embeddings = await self.embedding_client.embed_texts(
            [p['text'] for p in paragraphs]
        )
        
        # 4. 检测语义断点
        breakpoints = self._detect_breakpoints(embeddings)
        
        # 5. 合并段落为语义块
        chunks = self._merge_paragraphs(
            paragraphs, 
            breakpoints, 
            header_paths,
            file_id
        )
        
        # 6. 建立链表关系
        self._link_chunks(chunks)
        
        # 7. 添加元数据（保留 chunk 已有的 page_numbers）
        for chunk in chunks:
            existing_pages = chunk.metadata.get('page_numbers', [])
            chunk.metadata.update({
                "workspace_id": workspace_id,
                "file_id": file_id,
                "source_file": source_file,
                "chunk_index": chunk.chunk_index,
                "page_numbers": existing_pages  # [v2.2] 保留页码列表
            })
        
        # 8. 生成摘要（可选）
        if generate_summaries:
            await self._generate_summaries(chunks)
        
        return chunks
    
    def _split_paragraphs(self, text: str) -> List[Dict]:
        """
        [Fix] 增强型段落分割逻辑
        
        1. 动态阈值：遵循用户配置的 max_chunk_size，同时兼顾 API 安全。
        2. 标题感知：即使没有 \n\n，遇到标题行也自动断句。
        3. [v2.2] 页码提取：解析 [PAGE:x] 标记并记录到段落 metadata
        
        Returns:
            [{"text": str, "para_index": int, "page_numbers": List[int]}, ...]
        """
        # [Fix] 动态计算预切分阈值
        MAX_PARA_CHARS = min(self.max_chunk_size, 3000)
        
        lines = text.split('\n')
        raw_paragraphs = []
        current_para = []
        current_page = 0  # [v2.2] 追踪当前页码
        para_pages = set()  # [v2.2] 当前段落涉及的页码
        
        def commit_paragraph():
            """提交当前累积的段落内容"""
            nonlocal current_para, para_pages
            if current_para:
                para_text = '\n'.join(current_para).strip()
                if len(para_text) >= 10:
                    # [Fix] 如果段落本身依然超长，进入句子级预切分
                    if len(para_text) > MAX_PARA_CHARS:
                        sub_paras = self._split_long_paragraph(para_text, MAX_PARA_CHARS)
                        for sp in sub_paras:
                            raw_paragraphs.append({
                                "text": sp,
                                "page_numbers": list(para_pages) if para_pages else [0]
                            })
                    else:
                        raw_paragraphs.append({
                            "text": para_text,
                            "page_numbers": list(para_pages) if para_pages else [0]
                        })
                current_para = []
                para_pages = set()

        for line in lines:
            stripped = line.strip()
            
            # [v2.2] 检测页码标记 [PAGE:x]
            # [Refactor] 使用预编译的正则对象
            page_match = self.page_marker_pattern.match(stripped)
            if page_match:
                current_page = int(page_match.group(1))
                # 不将页码标记本身加入内容，但继续处理后续内容
                remaining = stripped[page_match.end():].strip()
                if remaining:
                    para_pages.add(current_page)
                    current_para.append(remaining)
                continue
            
            # 情况1：空行 -> 断句
            if not stripped:
                commit_paragraph()
                continue
                
            # 情况2：标题行感知 -> 即使没有空行也断句
            if self._parse_header(line):
                commit_paragraph()
            
            # 追踪页码
            if current_page > 0:
                para_pages.add(current_page)
            
            current_para.append(line)
        
        # 处理最后一个段落
        commit_paragraph()
        
        # 添加索引
        return [{"text": p["text"], "para_index": i, "page_numbers": p["page_numbers"]} 
                for i, p in enumerate(raw_paragraphs)]
    
    def _split_long_paragraph(self, text: str, max_chars: int) -> List[str]:
        """
        将超长段落按句子边界切分
        
        Returns:
            切分后的文本列表
        """
        # 优先按句子切分
        sentence_pattern = r'([。！？.!?]+)'
        parts = re.split(sentence_pattern, text)
        
        result = []
        current_text = ""
        
        for part in parts:
            test_text = current_text + part
            if len(test_text) > max_chars and current_text:
                result.append(current_text.strip())
                current_text = part
            else:
                current_text = test_text
        
        if current_text.strip():
            result.append(current_text.strip())
        
        final_result = []
        for para_text in result:
            if len(para_text) > max_chars:
                for j in range(0, len(para_text), max_chars):
                    chunk = para_text[j:j+max_chars].strip()
                    if chunk:
                        final_result.append(chunk)
            else:
                final_result.append(para_text)
        
        return final_result
    
    def _extract_header_paths(self, paragraphs: List[Dict]) -> Dict[int, str]:
        """
        提取每个段落对应的标题路径
        
        Returns:
            {paragraph_index: "章节 > 小节 > 子节"}
        """
        header_stack = []  # [(level, title), ...]
        header_paths = {}
        
        for i, para in enumerate(paragraphs):
            text = para['text'].strip()
            first_line = text.split('\n')[0]
            
            # 检测是否为标题
            header_info = self._parse_header(first_line)
            if header_info:
                level, title = header_info
                
                # 更新标题栈
                while header_stack and header_stack[-1][0] >= level:
                    header_stack.pop()
                header_stack.append((level, title))
            
            # 记录当前路径
            if header_stack:
                path = ' > '.join([h[1] for h in header_stack])
                header_paths[i] = path
        
        return header_paths
    
    def _parse_header(self, line: str) -> Optional[Tuple[int, str]]:
        """解析标题行，返回 (level, title) 或 None"""
        line = line.strip()
        
        # Markdown 标题
        md_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if md_match:
            level = len(md_match.group(1))
            title = md_match.group(2).strip()
            return (level, title)
        
        # 中文章节标题
        zh_match = re.match(r'^第([一二三四五六七八九十\d]+)[章][\s：:]*(.*)$', line)
        if zh_match:
            return (1, line)
        
        zh_section = re.match(r'^第([一二三四五六七八九十\d]+)[节条款][\s：:]*(.*)$', line)
        if zh_section:
            return (2, line)
        
        # 编号标题 (1. / 1.1 / 1.1.1)
        num_match = re.match(r'^(\d+(?:\.\d+)*)[\.、\s]+(.+)$', line)
        if num_match:
            level = num_match.group(1).count('.') + 1
            return (level, line)
        
        return None
    
    def _detect_breakpoints(self, embeddings: List[List[float]]) -> List[int]:
        """
        检测语义断点
        
        通过计算相邻段落的余弦相似度，当相似度低于阈值时标记为断点
        
        Returns:
            断点索引列表（即需要切分的位置）
        """
        if len(embeddings) <= 1:
            return []
        
        breakpoints = []
        
        for i in range(len(embeddings) - 1):
            sim = self._cosine_similarity(embeddings[i], embeddings[i + 1])
            
            if sim < self.similarity_threshold:
                breakpoints.append(i + 1)  # 在 i+1 处切分
        
        return breakpoints
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        a = np.array(vec1)
        b = np.array(vec2)
        
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return dot / (norm_a * norm_b)
    
    def _extract_image_markers(self, text: str) -> List[str]:
        """
        [v2.5] 从文本中提取 [IMAGE:ID] 标记
        
        用于 Word 文档的精确图片关联
        支持容错格式: [IMAGE:001], [Image: 1], [IMAGE: 12]
        
        [Refactor] 移除 int() 硬编码，支持更通用的 ID 格式 (No Hardcoding)
        
        Args:
            text: 要解析的文本内容
            
        Returns:
            图片 ID 列表，尝试格式化为 3 位，否则保留原样
        """
        matches = self.image_marker_pattern.findall(text)
        
        cleaned_ids = []
        for m in matches:
            # 尝试标准化为 3 位数字，如果失败（如遇到 "fig-1"），则保留原样
            try:
                cleaned_ids.append(f"{int(m):03d}")
            except ValueError:
                cleaned_ids.append(str(m).strip())
        
        return cleaned_ids
    
    def _create_chunk(
        self,
        content: str,
        chunk_index: int,
        file_id: str,
        header_path: Optional[str],
        page_numbers: List[int]
    ) -> SemanticChunk:
        """
        [Refactor] 封装 Chunk 创建逻辑，消除重复代码 (Zero Tech Debt / DRY)
        """
        linked_image_ids = self._extract_image_markers(content)
        return SemanticChunk(
            content=content,
            chunk_id=self._generate_chunk_id(file_id, chunk_index),
            chunk_index=chunk_index,
            header_path=header_path,
            parent_id=file_id,
            metadata={
                "page_numbers": sorted(list(set(page_numbers))),
                "linked_image_ids": linked_image_ids
            }
        )
    
    def _merge_paragraphs(
        self,
        paragraphs: List[Dict],
        breakpoints: List[int],
        header_paths: Dict[int, str],
        file_id: str
    ) -> List[SemanticChunk]:
        """
        根据断点将段落合并为语义块
        
        同时考虑最大/最小块大小限制
        [v2.2] 收集合并后 chunk 涉及的所有页码
        """
        chunks = []
        breakpoints_set = set(breakpoints)
        
        current_paras = []
        current_header = None
        current_pages = set()  # [v2.2] 当前 chunk 涉及的页码
        chunk_index = 0
        
        for i, para in enumerate(paragraphs):
            # 更新标题路径
            if i in header_paths:
                current_header = header_paths[i]
            
            current_paras.append(para)
            current_text = '\n\n'.join([p['text'] for p in current_paras])
            
            # [v2.2] 收集页码
            para_pages = para.get('page_numbers', [])
            current_pages.update(para_pages)
            
            # 判断是否需要切分
            should_split = False
            
            # 1. 达到语义断点
            if i + 1 in breakpoints_set:
                should_split = True
            
            # 2. 超过最大长度
            if len(current_text) >= self.max_chunk_size:
                should_split = True
            
            # 3. 最后一个段落
            if i == len(paragraphs) - 1:
                should_split = True
            
            if should_split and current_paras:
                # 检查是否达到最小长度
                if len(current_text) >= self.min_chunk_size:
                    # [Refactor] 使用封装方法创建 Chunk
                    chunk = self._create_chunk(
                        content=current_text,
                        chunk_index=chunk_index,
                        file_id=file_id,
                        header_path=current_header,
                        page_numbers=list(current_pages)
                    )
                    chunks.append(chunk)
                    chunk_index += 1
                    current_paras = []
                    current_pages = set()  # [v2.2] 重置
                elif i == len(paragraphs) - 1 and current_paras:
                    # 最后剩余内容不足最小长度，合并到前一个块
                    if chunks:
                        chunks[-1].content += '\n\n' + current_text
                        # [v2.2] 合并页码
                        prev_pages = set(chunks[-1].metadata.get('page_numbers', []))
                        prev_pages.update(current_pages)
                        chunks[-1].metadata['page_numbers'] = sorted(list(prev_pages))
                        # [v2.5] 合并图片标记
                        prev_imgs = chunks[-1].metadata.get('linked_image_ids', [])
                        new_imgs = self._extract_image_markers(current_text)
                        chunks[-1].metadata['linked_image_ids'] = prev_imgs + new_imgs
                    else:
                        # 唯一的块，即使很短也保留
                        chunk = self._create_chunk(
                            content=current_text,
                            chunk_index=chunk_index,
                            file_id=file_id,
                            header_path=current_header,
                            page_numbers=list(current_pages)
                        )
                        chunks.append(chunk)
        
        return chunks
    
    def _link_chunks(self, chunks: List[SemanticChunk]):
        """建立 Chunk 之间的双向链表关系"""
        for i, chunk in enumerate(chunks):
            if i > 0:
                chunk.prev_id = chunks[i - 1].chunk_id
            if i < len(chunks) - 1:
                chunk.next_id = chunks[i + 1].chunk_id
    
    def _generate_chunk_id(self, file_id: str, index: int) -> str:
        """生成 Chunk 唯一 ID"""
        content = f"{file_id}_chunk_{index}"
        return hashlib.md5(content.encode()).hexdigest()[:16]
    
    async def _generate_summaries(self, chunks: List[SemanticChunk]):
        """为所有 Chunk 生成摘要"""
        tasks = [self._generate_single_summary(chunk) for chunk in chunks]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _generate_single_summary(self, chunk: SemanticChunk):
        """为单个 Chunk 生成摘要"""
        try:
            settings = get_settings()
            prompt = f"""请为以下文本生成一个简洁的摘要，不超过100字：

---
{chunk.content[:2000]}
---

摘要："""
            
            messages = [{"role": "user", "content": prompt}]
            summary = await self.llm_client.chat(
                messages,
                model=settings.rag.summary_model,
                max_tokens=settings.rag.summary_max_tokens
            )
            chunk.summary = summary.strip()
        except Exception:
            # 摘要生成失败不影响主流程
            chunk.summary = None


# 工厂函数
def get_semantic_chunker() -> SemanticChunker:
    """获取语义切分器实例"""
    return SemanticChunker()
