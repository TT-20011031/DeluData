"""
重排序模块 (Reranker)

使用阿里云 DashScope Rerank API 对候选文档进行精确排序
"""
import os
import asyncio
from typing import List, Optional, Dict, Any
from http import HTTPStatus
from dataclasses import dataclass

import dashscope

from app.config import get_settings
from app.models.common.execution import DocumentChunk


@dataclass
class RerankResult:
    """重排序结果"""
    chunk: DocumentChunk
    relevance_score: float
    original_index: int


class Reranker:
    """
    文档重排序器
    
    核心功能：
    1. 调用阿里云 qwen3-rerank/gte-rerank 模型对候选文档打分
    2. 根据 Rerank Score 截断 Top-K
    3. 包含降级策略（模型不可用时使用向量相似度排序）
    """
    
    def __init__(self):
        settings = get_settings()
        self.api_key = settings.llm.api_key
        self.model = settings.rag.reranker_model
        self.top_k = settings.rag.rerank_top_k
        self.instruct = settings.rag.rerank_instruct
        
        # 确保环境变量设置
        if self.api_key:
            os.environ["DASHSCOPE_API_KEY"] = self.api_key
    
    async def rerank(
        self,
        query: str,
        chunks: List[DocumentChunk],
        top_k: Optional[int] = None
    ) -> List[DocumentChunk]:
        """
        对候选文档进行重排序
        
        Args:
            query: 用户查询
            chunks: 候选文档列表
            top_k: 返回数量（可选，默认使用配置）
            
        Returns:
            重排序后的文档列表（已设置 rerank_score）
        """
        _top_k = top_k or self.top_k
        
        if not chunks:
            return []
        
        if len(chunks) <= 1:
            return chunks
        
        try:
            # 调用 Rerank API
            results = await self._call_rerank_api(query, chunks)
            
            # 按 relevance_score 排序
            results.sort(key=lambda x: x.relevance_score, reverse=True)
            
            # 取 Top-K
            top_results = results[:_top_k]
            
            # 返回 chunks（已设置 rerank_score）
            return [r.chunk for r in top_results]
            
        except Exception as e:
            # 降级策略：使用原始向量相似度排序
            return self._fallback_sort(chunks, _top_k)
    
    async def _call_rerank_api(
        self,
        query: str,
        chunks: List[DocumentChunk]
    ) -> List[RerankResult]:
        """调用阿里云 Rerank API"""
        
        # 提取文档内容
        documents = [chunk.content for chunk in chunks]
        
        # DashScope 同步 API 需要在线程池中执行
        def _sync_call():
            return dashscope.TextReRank.call(
                model=self.model,
                query=query,
                documents=documents,
                top_n=len(documents),  # 获取全部结果的分数
                return_documents=False,  # 只需要索引和分数
                instruct=self.instruct
            )
        
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _sync_call)
        
        if response.status_code != HTTPStatus.OK:
            raise Exception(f"Rerank API 调用失败: {response.code} - {response.message}")
        
        # 解析结果
        results = []
        for item in response.output.get("results", []):
            idx = item.get("index", 0)
            score = item.get("relevance_score", 0.0)
            
            if idx < len(chunks):
                chunk = chunks[idx]
                chunk.rerank_score = score
                results.append(RerankResult(
                    chunk=chunk,
                    relevance_score=score,
                    original_index=idx
                ))
        
        return results
    
    def _fallback_sort(
        self,
        chunks: List[DocumentChunk],
        top_k: int
    ) -> List[DocumentChunk]:
        """
        降级排序策略
        
        使用原始向量相似度分数排序
        """
        # 按 score（向量相似度）降序排序
        sorted_chunks = sorted(chunks, key=lambda x: x.score, reverse=True)
        
        # 标记为使用降级策略
        for chunk in sorted_chunks[:top_k]:
            chunk.rerank_score = chunk.score  # 使用向量分数作为 rerank 分数
        
        fallback_chunks = sorted_chunks[:top_k]
        for chunk in fallback_chunks:
            chunk.metadata = dict(chunk.metadata or {})
            chunk.metadata["rerank_fallback"] = True
        return fallback_chunks
    
    async def rerank_with_scores(
        self,
        query: str,
        chunks: List[DocumentChunk],
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        重排序并返回详细分数信息
        
        Returns:
            [{"chunk": DocumentChunk, "rerank_score": float, "vector_score": float}, ...]
        """
        _top_k = top_k or self.top_k
        
        reranked = await self.rerank(query, chunks, _top_k)
        
        return [
            {
                "chunk": chunk,
                "rerank_score": chunk.rerank_score or 0.0,
                "vector_score": chunk.score
            }
            for chunk in reranked
        ]


# 工厂函数
def get_reranker() -> Reranker:
    """获取重排序器实例"""
    return Reranker()
