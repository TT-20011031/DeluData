"""
上下文扩展模块 (Context Expander) - 增强版 (Async)

利用 Chunk 之间的链表关系 + 知识图谱显式关系，扩展检索结果的上下文窗口
"""
from dataclasses import replace
import logging
from typing import List, Optional, Dict, Set
import asyncio

import chromadb
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.common.execution import DocumentChunk
from app.core.db.database import get_async_db_manager
from app.models.knowledge.graph import FileRelationship, File

logger = logging.getLogger(__name__)

class ContextExpander:
    """
    上下文扩展器 (GraphRAG Enhanced)
    
    核心功能：
    1. 链表扩展：利用 prev_id/next_id 获取邻居 Chunk (Window Context)
    2. 图谱扩展：利用 MySQL 中的 file_relationships 获取相关文档摘要 (Global Context)
    """
    
    def __init__(self, chroma_client: Optional[chromadb.ClientAPI] = None):
        settings = get_settings()
        self.rag_settings = settings.rag
        self.window_size = settings.rag.expansion_window
        self.max_chars = settings.rag.expansion_max_chars
        self.enabled = settings.rag.enable_context_expansion
        
        # ChromaDB 客户端
        if chroma_client is None:
            from pathlib import Path
            persist_path = Path(settings.chroma.persist_dir).resolve()
            persist_path.mkdir(parents=True, exist_ok=True)
            chroma_client = chromadb.PersistentClient(path=str(persist_path))
        
        self.chroma_client = chroma_client
        self._collection = None
    
    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.chroma_client.get_or_create_collection(
                name="tenant_docs",
                metadata={"description": "User uploaded documents for RAG"}
            )
        return self._collection
    
    async def expand(
        self,
        chunks: List[DocumentChunk],
        window_size: Optional[int] = None
    ) -> List[DocumentChunk]:
        """
        扩展检索结果的上下文 (Async)
        """
        if not self.enabled or not chunks:
            return chunks
        
        _window = window_size if window_size is not None else self.window_size
        
        # 链表扩展 (大窗口上下文)
        expanded_chunks = self._expand_window(chunks, _window) if _window > 0 else chunks
        
        return expanded_chunks
    
    def _expand_window(self, chunks: List[DocumentChunk], window_size: int) -> List[DocumentChunk]:
        """
        利用 prev/next 扩展上下文窗口
        
        [优化] 大窗口扩展 + 字符上限保护
        """
        # 先收集所有可能需要的邻居 ID
        neighbor_ids: Set[str] = set()
        for chunk in chunks:
            # 向前遍历
            cur_prev = chunk.prev_id
            for _ in range(window_size):
                if cur_prev:
                    neighbor_ids.add(cur_prev)
                    # 需要先获取这个 chunk 才能继续遍历，这里先收集 ID
                    cur_prev = None  # 后续通过 neighbor_map 继续
                else:
                    break
            # 向后遍历
            cur_next = chunk.next_id
            for _ in range(window_size):
                if cur_next:
                    neighbor_ids.add(cur_next)
                    cur_next = None
                else:
                    break
        
        # 批量获取邻居（第一批）
        neighbor_map = self._fetch_chunks_by_ids(list(neighbor_ids))
        
        # 递归获取更深层的邻居
        for _ in range(window_size - 1):
            new_ids = set()
            for nid, nchunk in neighbor_map.items():
                if nchunk.prev_id and nchunk.prev_id not in neighbor_map:
                    new_ids.add(nchunk.prev_id)
                if nchunk.next_id and nchunk.next_id not in neighbor_map:
                    new_ids.add(nchunk.next_id)
            if not new_ids:
                break
            new_chunks = self._fetch_chunks_by_ids(list(new_ids))
            neighbor_map.update(new_chunks)
        
        result_chunks = []
        seen_ids = set()
        
        for chunk in chunks:
            if chunk.chunk_id in seen_ids:
                continue
            
            # 构建内容
            parts = []
            total_chars = 0
            
            # 前邻居（倒序收集）
            cur_prev = chunk.prev_id
            pre_parts = []
            for _ in range(window_size):
                if cur_prev and cur_prev in neighbor_map:
                    p = neighbor_map[cur_prev]
                    if total_chars + len(p.content) > self.max_chars:
                        break  # 字符上限保护
                    pre_parts.insert(0, p.content)
                    total_chars += len(p.content)
                    cur_prev = p.prev_id
                else:
                    break
            parts.extend(pre_parts)
            
            # 当前 chunk
            parts.append(chunk.content)
            total_chars += len(chunk.content)
            
            # 后邻居
            cur_next = chunk.next_id
            for _ in range(window_size):
                if cur_next and cur_next in neighbor_map:
                    n = neighbor_map[cur_next]
                    if total_chars + len(n.content) > self.max_chars:
                        break  # 字符上限保护
                    parts.append(n.content)
                    total_chars += len(n.content)
                    cur_next = n.next_id
                else:
                    break
            
            expanded_content = "\n\n---\n\n".join(parts)
            
            # 复制并更新内容
            new_chunk = replace(chunk, content=expanded_content)
            result_chunks.append(new_chunk)
            seen_ids.add(chunk.chunk_id)
            
            logger.debug(f"[ContextExpander] chunk {chunk.chunk_id}: {len(chunk.content)} -> {len(expanded_content)} chars")
            
        return result_chunks

    async def _expand_graph(self, chunks: List[DocumentChunk]) -> List[DocumentChunk]:
        """
        查询 MySQL 获取关联文档信息，并追加到 Context (Async)
        """
        doc_ids = set()
        for c in chunks:
            if c.metadata and 'doc_id' in c.metadata:
                doc_ids.add(c.metadata['doc_id'])
        
        if not doc_ids:
            return chunks

        try:
            db_manager = get_async_db_manager()
            doc_relations = {} # doc_id -> list of related descriptions
            
            async with db_manager.session_scope() as session:
                # 批量查询关系
                query = select(FileRelationship, File).join(
                    File, FileRelationship.target_file_id == File.id
                ).where(FileRelationship.source_file_id.in_(list(doc_ids)))
                
                result = await session.execute(query)
                results = result.all()
                
                for rel, file in results:
                    if rel.source_file_id not in doc_relations:
                        doc_relations[rel.source_file_id] = []
                    
                    info = f"[{rel.relation_type}] 相关文档《{file.name}》: {file.description or '无描述'}"
                    doc_relations[rel.source_file_id].append(info)
            
            # 将关联信息附加到 Chunk 内容末尾
            final_chunks = []
            for chunk in chunks:
                doc_id = chunk.metadata.get('doc_id')
                if doc_id and doc_id in doc_relations:
                    relations_text = "\n".join(doc_relations[doc_id])
                    expanded_text = f"{chunk.content}\n\n=== 知识图谱关联上下文 ===\n{relations_text}"
                    
                    new_chunk = replace(chunk, content=expanded_text)
                    final_chunks.append(new_chunk)
                else:
                    final_chunks.append(chunk)
            
            return final_chunks

        except Exception as e:
            logger.error(f"Graph expansion failed: {e}")
            return chunks

    def _fetch_chunks_by_ids(self, chunk_ids: List[str]) -> Dict[str, DocumentChunk]:
        """批量获取 Chunk (Helper)"""
        if not chunk_ids: return {}
        try:
            results = self.collection.get(ids=chunk_ids, include=["documents", "metadatas"])
            chunk_map = {}
            if results and results.get("ids"):
                for i, cid in enumerate(results["ids"]):
                    meta = results["metadatas"][i]
                    chunk_map[cid] = DocumentChunk(
                        content=results["documents"][i],
                        source_file=meta.get("source_file", ""),
                        chunk_id=cid,
                        metadata=meta,
                        prev_id=meta.get("prev_id"),
                        next_id=meta.get("next_id")
                    )
            return chunk_map
        except ImportError: return {}
        except Exception: return {}

# 工厂函数
def get_context_expander(chroma_client: Optional[chromadb.ClientAPI] = None) -> ContextExpander:
    return ContextExpander(chroma_client)
