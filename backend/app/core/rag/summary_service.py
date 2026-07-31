"""
后台摘要生成服务 (Background Summary Service)

异步后台任务，为文档 Chunk 生成摘要，不阻塞主流程。
使用线程池执行，用户体验无感知。
"""
import asyncio
import logging
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum

import chromadb

from app.config import get_settings
from app.core.llm.async_llm import get_async_llm


logger = logging.getLogger("summary_service")


class SummaryTaskStatus(Enum):
    """摘要任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SummaryTask:
    """摘要任务"""
    chunk_id: str
    content: str
    status: SummaryTaskStatus = SummaryTaskStatus.PENDING
    summary: Optional[str] = None
    error: Optional[str] = None


class BackgroundSummaryService:
    """
    后台摘要生成服务
    
    核心功能：
    1. 接收 Chunk 列表，异步批量生成摘要
    2. 生成完成后自动更新 ChromaDB metadata
    3. 任务队列管理，避免阻塞主流程
    4. 失败重试机制
    """
    
    SUMMARY_PROMPT = """请为以下文档片段生成一个简洁的摘要，概括其核心内容。
要求：
1. 摘要长度不超过100字
2. 保留关键信息和核心观点
3. 使用书面语表达

文档片段：
---
{content}
---

摘要："""
    
    def __init__(self, chroma_client: Optional[chromadb.ClientAPI] = None):
        settings = get_settings()
        self.rag_settings = settings.rag
        
        # ChromaDB 客户端
        if chroma_client is None:
            from pathlib import Path
            persist_path = Path(settings.chroma.persist_dir).resolve()
            persist_path.mkdir(parents=True, exist_ok=True)
            chroma_client = chromadb.PersistentClient(path=str(persist_path))
        
        self.chroma_client = chroma_client
        self._collection = None
        self._llm_client = None
        
        # 任务队列
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        
        # 并发控制
        self._semaphore = asyncio.Semaphore(5)  # 最多5个并发 LLM 调用
    
    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.chroma_client.get_or_create_collection(
                name="tenant_docs"
            )
        return self._collection
    
    @property
    def llm_client(self):
        if self._llm_client is None:
            self._llm_client = get_async_llm()
        return self._llm_client
    
    async def start(self):
        """启动后台 Worker"""
        if self._running:
            return
        
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Background summary service started")
    
    async def stop(self):
        """停止后台 Worker"""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Background summary service stopped")
    
    async def submit_chunks(
        self,
        chunks: List[Dict[str, Any]]
    ):
        """
        提交 Chunk 列表进行摘要生成
        
        Args:
            chunks: [{"chunk_id": str, "content": str}, ...]
        """
        for chunk in chunks:
            task = SummaryTask(
                chunk_id=chunk["chunk_id"],
                content=chunk["content"]
            )
            await self._task_queue.put(task)
        
        logger.info(f"Submitted {len(chunks)} chunks for summary generation")
    
    async def _worker_loop(self):
        """Worker 循环，处理任务队列"""
        while self._running:
            try:
                # 批量获取任务
                tasks = []
                try:
                    # 等待第一个任务
                    task = await asyncio.wait_for(
                        self._task_queue.get(), 
                        timeout=1.0
                    )
                    tasks.append(task)
                    
                    # 尝试获取更多任务（最多10个一批）
                    while len(tasks) < 10:
                        try:
                            task = self._task_queue.get_nowait()
                            tasks.append(task)
                        except asyncio.QueueEmpty:
                            break
                            
                except asyncio.TimeoutError:
                    continue
                
                if tasks:
                    await self._process_batch(tasks)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(1)
    
    async def _process_batch(self, tasks: List[SummaryTask]):
        """处理一批任务"""
        # 并发生成摘要
        async def process_single(task: SummaryTask):
            async with self._semaphore:
                try:
                    task.status = SummaryTaskStatus.RUNNING
                    summary = await self._generate_summary(task.content)
                    task.summary = summary
                    task.status = SummaryTaskStatus.COMPLETED
                    
                    # 更新 ChromaDB
                    await self._update_metadata(task.chunk_id, summary)
                    
                except Exception as e:
                    task.status = SummaryTaskStatus.FAILED
                    task.error = str(e)
                    logger.error(f"Failed to generate summary for {task.chunk_id}: {e}")
        
        await asyncio.gather(*[process_single(t) for t in tasks])
        
        completed = sum(1 for t in tasks if t.status == SummaryTaskStatus.COMPLETED)
        logger.info(f"Processed {len(tasks)} tasks, {completed} completed")
    
    async def _generate_summary(self, content: str) -> str:
        """生成单个摘要"""
        # 截断过长内容
        truncated = content[:2000] if len(content) > 2000 else content
        
        prompt = self.SUMMARY_PROMPT.format(content=truncated)
        messages = [{"role": "user", "content": prompt}]
        
        summary = await self.llm_client.chat(
            messages,
            model=self.rag_settings.summary_model,
            max_tokens=self.rag_settings.summary_max_tokens
        )
        
        return summary.strip()
    
    async def _update_metadata(self, chunk_id: str, summary: str):
        """
        更新 ChromaDB 中的摘要 metadata
        
        使用线程池执行同步操作
        """
        def _sync_update():
            try:
                # 获取现有 metadata
                result = self.collection.get(
                    ids=[chunk_id],
                    include=["metadatas"]
                )
                
                if result and result.get("metadatas"):
                    metadata = result["metadatas"][0] or {}
                    metadata["summary"] = summary
                    
                    # 更新
                    self.collection.update(
                        ids=[chunk_id],
                        metadatas=[metadata]
                    )
                    
            except Exception as e:
                logger.error(f"Failed to update metadata for {chunk_id}: {e}")
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_update)


# 单例
_summary_service: Optional[BackgroundSummaryService] = None


def get_summary_service(
    chroma_client: Optional[chromadb.ClientAPI] = None
) -> BackgroundSummaryService:
    """获取后台摘要服务单例"""
    global _summary_service
    if _summary_service is None:
        _summary_service = BackgroundSummaryService(chroma_client)
    return _summary_service


async def start_summary_service():
    """启动后台摘要服务"""
    service = get_summary_service()
    await service.start()


async def stop_summary_service():
    """停止后台摘要服务"""
    service = get_summary_service()
    await service.stop()
