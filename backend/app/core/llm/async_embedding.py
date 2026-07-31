"""
异步 Embedding 客户端

使用阿里云 DashScope 原生 TextEmbedding SDK 实现异步调用
避免 OpenAI 兼容层 SSL 握手超时问题
"""
import os
import asyncio
import logging
from http import HTTPStatus
from typing import List, Optional

import dashscope
from dashscope import TextEmbedding
from requests import ConnectionError as RequestsConnectionError
from requests import Timeout as RequestsTimeout

from app.config import get_settings

logger = logging.getLogger(__name__)


class AsyncEmbeddingClient:
    """
    异步 Embedding 客户端
    
    使用 DashScope 原生 TextEmbedding SDK，
    通过线程池实现异步调用，避免 OpenAI 兼容层的连接超时问题
    """
    
    # DashScope TextEmbedding 单批最大 10 条（API 硬限制），保守设为 6
    MAX_BATCH_SIZE = 6
    
    def __init__(self):
        settings = get_settings()
        self.api_key = settings.llm.api_key
        self.model = settings.rag.embedding_model
        self.dimensions = settings.rag.embedding_dimensions
        self.max_attempts = max(1, int(settings.rag.embedding_max_attempts))
        self.retry_backoff_sec = max(0.0, float(settings.rag.embedding_retry_backoff_sec))
        
        # 设置全局 API Key (DashScope SDK 要求)
        if self.api_key:
            dashscope.api_key = self.api_key
            os.environ["DASHSCOPE_API_KEY"] = self.api_key
    
    async def embed_texts(
        self,
        texts: List[str],
        model: Optional[str] = None,
        dimensions: Optional[int] = None
    ) -> List[List[float]]:
        """
        异步生成文本向量
        
        Args:
            texts: 待向量化的文本列表
            model: 向量模型名称（可选，默认使用配置）
            dimensions: 向量维度（可选，默认使用配置）
            
        Returns:
            向量列表，每个向量为 float 列表
        """
        if not texts:
            raise ValueError("embed_texts 需要非空文本列表")
        if not any(str(text or "").strip() for text in texts):
            raise ValueError("embed_texts 输入文本不能为空")

        _model = model or self.model
        _dimensions = dimensions or self.dimensions
        
        # 分批处理（DashScope 原生 SDK 限制单批 25 条）
        if len(texts) > self.MAX_BATCH_SIZE:
            results = []
            for i in range(0, len(texts), self.MAX_BATCH_SIZE):
                batch = texts[i:i + self.MAX_BATCH_SIZE]
                batch_results = await self._embed_batch(batch, _model, _dimensions)
                results.extend(batch_results)
            return results
        
        return await self._embed_batch(texts, _model, _dimensions)
    
    async def _embed_batch(
        self,
        texts: List[str],
        model: str,
        dimensions: int
    ) -> List[List[float]]:
        """批量生成向量（使用线程池执行 DashScope 同步调用）"""
        
        def _sync_call():
            return TextEmbedding.call(
                model=model,
                input=texts,
                dimension=dimensions,
                text_type="document"
            )

        loop = asyncio.get_running_loop()
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await loop.run_in_executor(None, _sync_call)
            except (RequestsConnectionError, RequestsTimeout, TimeoutError) as exc:
                if attempt >= self.max_attempts:
                    raise
                delay = self.retry_backoff_sec * (2 ** (attempt - 1))
                logger.warning(
                    "[Embedding] transient request failure; retrying attempt=%d/%d "
                    "error_type=%s delay_sec=%.2f",
                    attempt,
                    self.max_attempts,
                    type(exc).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            status_code = int(response.status_code)
            if status_code != HTTPStatus.OK:
                error = RuntimeError(
                    f"DashScope Embedding 调用失败: "
                    f"code={response.code}, message={response.message}"
                )
                retryable_status = status_code == 429 or 500 <= status_code < 600
                if not retryable_status or attempt >= self.max_attempts:
                    raise error
                delay = self.retry_backoff_sec * (2 ** (attempt - 1))
                logger.warning(
                    "[Embedding] retryable response; retrying attempt=%d/%d "
                    "status_code=%d delay_sec=%.2f",
                    attempt,
                    self.max_attempts,
                    status_code,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            # 按 text_index 排序，确保与输入顺序一致
            embeddings_data = response.output.get("embeddings", [])
            sorted_data = sorted(embeddings_data, key=lambda x: x.get("text_index", 0))
            return [item["embedding"] for item in sorted_data]

        raise RuntimeError("DashScope Embedding 调用在重试后仍未成功")
    
    async def embed_single(
        self,
        text: str,
        model: Optional[str] = None,
        dimensions: Optional[int] = None
    ) -> List[float]:
        """
        异步生成单条文本向量
        
        Args:
            text: 待向量化的文本
            model: 向量模型名称（可选）
            dimensions: 向量维度（可选）
            
        Returns:
            向量（float 列表）
        """
        results = await self.embed_texts([text], model, dimensions)
        return results[0] if results else []


# 单例
_embedding_client: Optional[AsyncEmbeddingClient] = None


def get_async_embedding() -> AsyncEmbeddingClient:
    """获取异步 Embedding 客户端单例"""
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = AsyncEmbeddingClient()
    return _embedding_client
