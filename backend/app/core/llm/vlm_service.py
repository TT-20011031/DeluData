"""
VLM 视觉语言模型服务 (v2.2 - 异步非阻塞版)

封装 Qwen-VL API 调用，支持：
1. 图片描述生成（用于 ETL 入库）
2. 用户上传图片理解（用于以图搜文）
3. Redis 缓存（基于 pHash 去重）
4. [v2.2] 完全异步化 + Semaphore 限流，避免阻塞 Event Loop
"""
import asyncio
import logging
import base64
from typing import Optional
from pathlib import Path

import imagehash
from PIL import Image
import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)

# [v2.2] 模块级信号量，限制 VLM 并发请求数（防止 API 限流和资源耗尽）
VLM_SEMAPHORE = asyncio.Semaphore(5)


class VLMService:
    """
    视觉语言模型服务
    
    使用 Qwen-VL 为图片生成文本描述，支持 Redis 缓存避免重复调用。
    [v2.2] 完全异步化，所有 IO/CPU 密集操作均使用 asyncio.to_thread
    """
    
    CACHE_PREFIX = "vlm:phash:"
    CACHE_TTL = 86400 * 30  # 30天
    
    def __init__(self):
        settings = get_settings()
        self.model = settings.vlm.model
        self.max_tokens = settings.vlm.max_tokens
        self._redis: Optional[redis.Redis] = None
    
    async def get_redis(self) -> Optional[redis.Redis]:
        """获取 Redis 连接（懒加载 + 容错）"""
        if self._redis is None:
            try:
                settings = get_settings()
                self._redis = redis.from_url(
                    settings.redis.connection_url,
                    decode_responses=False
                )
                # 测试连接
                await self._redis.ping()
            except Exception as e:
                logger.warning(f"[VLM] Redis 连接失败，将跳过缓存: {e}")
                self._redis = None
        return self._redis
    
    def _compute_phash_sync(self, image_path: str) -> str:
        """[同步] 计算图片感知哈希（内部方法）"""
        img = Image.open(image_path)
        return str(imagehash.phash(img))
    
    async def compute_phash(self, image_path: str) -> str:
        """[异步] 计算图片感知哈希（用于去重和缓存）"""
        return await asyncio.to_thread(self._compute_phash_sync, image_path)
    
    def _call_vlm_sync(
        self,
        image_path: str,
        prompt: str
    ) -> Optional[str]:
        """
        [同步] 调用 VLM API（内部方法，被 to_thread 包装）
        
        将同步的 DashScope SDK 调用封装在此方法中，
        外层通过 asyncio.to_thread 调用以避免阻塞事件循环。
        """
        import dashscope
        from dashscope import MultiModalConversation
        
        settings = get_settings()
        dashscope.api_key = settings.llm.dashscope_api_key
        
        # 读取图片并转为 base64
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()
        
        # 获取图片格式
        suffix = Path(image_path).suffix.lower()
        mime_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp"
        }.get(suffix, "image/png")
        
        messages = [{
            "role": "user",
            "content": [
                {"image": f"data:{mime_type};base64,{image_data}"},
                {"text": prompt}
            ]
        }]
        
        response = MultiModalConversation.call(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens
        )
        
        if response.status_code == 200:
            return response.output.choices[0].message.content[0]["text"]
        else:
            logger.error(f"[VLM] API 调用失败: {response.code} - {response.message}")
            return None
    
    async def describe_image(
        self,
        image_path: str,
        prompt_type: str = "general"
    ) -> str:
        """
        生成图片描述（异步非阻塞版）
        
        [v2.2] 特性：
        - Semaphore 限流：最大 5 并发，防止 API Rate Limit
        - asyncio.to_thread：VLM 调用不阻塞事件循环
        - pHash 异步化：图片处理不阻塞事件循环
        
        Args:
            image_path: 图片路径
            prompt_type: 提示类型
                - general: 通用描述
                - chart: 图表数据提取
                - screenshot: 截图 OCR
                - exhibit: 博物馆展品
        
        Returns:
            图片描述文本
        """
        # 1. 计算 pHash（异步）
        try:
            phash = await self.compute_phash(image_path)
        except Exception as e:
            logger.error(f"[VLM] 计算 pHash 失败: {e}")
            return ""
        
        # 2. 检查缓存
        cache_key = f"{self.CACHE_PREFIX}{phash}"
        r = await self.get_redis()
        if r:
            try:
                cached = await r.get(cache_key)
                if cached:
                    logger.info(f"[VLM] 缓存命中: {phash[:8]}...")
                    return cached.decode()
            except Exception as e:
                logger.warning(f"[VLM] 读取缓存失败: {e}")
        
        # 3. [v2.3] 从配置获取 Prompt（解耦业务逻辑与配置）
        settings = get_settings()
        prompt = settings.vlm.get_prompt(prompt_type)
        
        # 4. [v2.2] 使用 Semaphore 限流 + to_thread 异步调用 VLM
        try:
            async with VLM_SEMAPHORE:
                logger.debug(f"[VLM] 开始调用 API (并发槽已获取)")
                description = await asyncio.to_thread(
                    self._call_vlm_sync,
                    image_path,
                    prompt
                )
            
            if description:
                # 5. 写入缓存
                if r:
                    try:
                        await r.setex(cache_key, self.CACHE_TTL, description)
                        logger.info(f"[VLM] 写入缓存: {phash[:8]}...")
                    except Exception as e:
                        logger.warning(f"[VLM] 写入缓存失败: {e}")
                
                logger.info(f"[VLM] 生成描述成功: {len(description)} chars")
                return description
            else:
                return ""
                
        except Exception as e:
            logger.error(f"[VLM] 调用异常: {e}")
            return ""
    
    async def understand_user_image(self, image_path: str, query: str = "") -> str:
        """
        理解用户上传的图片（用于以图搜文）
        
        Args:
            image_path: 用户上传的图片路径
            query: 用户附带的问题（可选）
        
        Returns:
            图片理解结果，可用于向量检索
        """
        return await self.describe_image(image_path, "general")
    
    async def close(self):
        """关闭连接"""
        if self._redis:
            await self._redis.close()
            self._redis = None


# 单例
_vlm_service: Optional[VLMService] = None


def get_vlm_service() -> VLMService:
    """获取 VLM 服务单例"""
    global _vlm_service
    if _vlm_service is None:
        _vlm_service = VLMService()
    return _vlm_service
