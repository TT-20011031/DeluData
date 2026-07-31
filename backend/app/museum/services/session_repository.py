"""
博物馆模块 - 会话仓储服务

遵循设计原则：
- 全异步 I/O (Async First)
- 严格模式校验 (Schema Validation)
"""
import json
import logging
from typing import Optional, List
from datetime import datetime

import redis.asyncio as redis
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.database import get_async_db_context
from app.museum.db import MuseumGuideSession
from app.museum.models import PersonType, GuideSessionCreate, GuideSessionResponse
from app.config import get_settings

logger = logging.getLogger(__name__)


class MessageHistory:
    """
    会话消息历史管理
    
    遵循设计原则：
    - 全异步 I/O：使用 Redis 异步操作
    - 配置外置：TTL 等配置可调
    
    策略：
    - 限制 3-5 轮历史，避免上下文窗口溢出
    - 只存 AI 消化后的回答，不存原始检索文档
    - 使用 Redis List 存储，自动过期
    - 当 Redis 不可用时，使用内存缓存作为降级方案
    """
    
    CACHE_PREFIX = "museum:history:"
    IMAGE_CACHE_PREFIX = "museum:images:"  # 图片缓存前缀
    MAX_ROUNDS = 5  # 最多保留轮数
    CACHE_TTL = 1800  # 30 分钟过期
    
    # 内存缓存降级方案（Redis 不可用时使用）
    _memory_cache: dict[str, list] = {}
    _image_cache: dict[str, list] = {}  # 图片缓存降级
    
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self._redis = redis_client
        self._redis_available = None  # None=未检测, True/False=已检测
    
    async def _get_redis(self) -> Optional[redis.Redis]:
        """获取 Redis 连接"""
        # 如果已经检测过且不可用，直接返回 None（避免重复连接尝试）
        if self._redis_available is False:
            return None
            
        if self._redis is None:
            try:
                settings = get_settings()
                self._redis = redis.from_url(
                    settings.redis.connection_url,
                    decode_responses=True
                )
                await self._redis.ping()
                self._redis_available = True
                logger.info("[MessageHistory] Redis 连接成功")
            except Exception as e:
                logger.warning(f"[MessageHistory] Redis 连接失败，使用内存缓存: {e}")
                self._redis = None
                self._redis_available = False
        return self._redis
    
    async def add_round(
        self,
        session_id: str,
        user_msg: str,
        assistant_msg: str,
    ) -> bool:
        """
        添加一轮对话
        
        Args:
            session_id: 会话ID
            user_msg: 用户消息（不含检索文档，只含原始问题）
            assistant_msg: AI 回复
            
        Returns:
            是否添加成功
        """
        round_data = {
            "user": user_msg,
            "assistant": assistant_msg,
            "ts": datetime.utcnow().isoformat()
        }
        
        r = await self._get_redis()
        if not r:
            # 降级到内存缓存
            if session_id not in self._memory_cache:
                self._memory_cache[session_id] = []
            self._memory_cache[session_id].append(round_data)
            # 保留最近 MAX_ROUNDS 轮
            if len(self._memory_cache[session_id]) > self.MAX_ROUNDS:
                self._memory_cache[session_id] = self._memory_cache[session_id][-self.MAX_ROUNDS:]
            logger.info(f"[MessageHistory] 内存缓存添加历史: {session_id}, 共 {len(self._memory_cache[session_id])} 轮")
            return True
        
        try:
            cache_key = f"{self.CACHE_PREFIX}{session_id}"
            
            # 构建消息对
            round_json = json.dumps(round_data, ensure_ascii=False)
            
            # RPUSH 添加到列表尾部
            await r.rpush(cache_key, round_json)
            
            # 保留最近 MAX_ROUNDS 轮（LTRIM 保留 -MAX_ROUNDS 到 -1）
            await r.ltrim(cache_key, -self.MAX_ROUNDS, -1)
            
            # 刷新过期时间
            await r.expire(cache_key, self.CACHE_TTL)
            
            logger.info(f"[MessageHistory] Redis 添加历史: {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"[MessageHistory] 添加历史失败: {e}")
            return False
    
    async def get_history(
        self,
        session_id: str,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        获取历史消息（OpenAI messages 格式）
        
        Args:
            session_id: 会话ID
            limit: 限制轮数（默认取全部）
            
        Returns:
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
        """
        r = await self._get_redis()
        
        # 尝试从内存缓存获取（Redis 不可用时的降级方案）
        if not r:
            rounds = self._memory_cache.get(session_id, [])
            if not rounds:
                return []
            messages = []
            for round_data in rounds:
                messages.append({"role": "user", "content": round_data["user"]})
                messages.append({"role": "assistant", "content": round_data["assistant"]})
            if limit and len(messages) > limit * 2:
                messages = messages[-(limit * 2):]
            logger.info(f"[MessageHistory] 从内存缓存获取历史: {session_id}, {len(messages)//2} 轮")
            return messages
        
        try:
            cache_key = f"{self.CACHE_PREFIX}{session_id}"
            
            # 获取所有历史
            raw_list = await r.lrange(cache_key, 0, -1)
            
            if not raw_list:
                return []
            
            # 解析并转换为 messages 格式
            messages = []
            for raw in raw_list:
                try:
                    round_data = json.loads(raw)
                    messages.append({"role": "user", "content": round_data["user"]})
                    messages.append({"role": "assistant", "content": round_data["assistant"]})
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"[MessageHistory] 解析历史失败: {e}")
                    continue
            
            # 如果指定了 limit，只取最近 limit 轮（limit*2 条消息）
            if limit and len(messages) > limit * 2:
                messages = messages[-(limit * 2):]
            
            logger.debug(f"[MessageHistory] 获取历史: {session_id}, {len(messages)//2} 轮")
            return messages
            
        except Exception as e:
            logger.error(f"[MessageHistory] 获取历史失败: {e}")
            return []
    
    async def clear(self, session_id: str) -> bool:
        """清除会话历史"""
        r = await self._get_redis()
        if not r:
            return False
        
        try:
            cache_key = f"{self.CACHE_PREFIX}{session_id}"
            await r.delete(cache_key)
            logger.info(f"[MessageHistory] 清除历史: {session_id}")
            return True
        except Exception as e:
            logger.error(f"[MessageHistory] 清除历史失败: {e}")
            return False
    
    async def get_last_user_message(self, session_id: str) -> Optional[str]:
        """
        获取上一轮用户消息（用于提取主题关键词）
        
        Returns:
            上一轮用户消息内容，若无则返回 None
        """
        r = await self._get_redis()
        
        if not r:
            # 降级到内存缓存
            rounds = self._memory_cache.get(session_id, [])
            if rounds:
                return rounds[-1].get("user")
            return None
        
        try:
            cache_key = f"{self.CACHE_PREFIX}{session_id}"
            # 获取最后一条（LINDEX -1）
            last_raw = await r.lindex(cache_key, -1)
            if last_raw:
                round_data = json.loads(last_raw)
                return round_data.get("user")
            return None
        except Exception as e:
            logger.error(f"[MessageHistory] 获取上一轮消息失败: {e}")
            return None
    
    async def cache_images(self, session_id: str, images: List[dict]) -> bool:
        """
        缓存当前轮的检索图片（用于兜底复用）
        
        Args:
            session_id: 会话ID
            images: 图片列表 [{"fileId": ..., "imageId": ..., "caption": ...}, ...]
        """
        if not images:
            return False
        
        r = await self._get_redis()
        if not r:
            # 降级到内存缓存
            self._image_cache[session_id] = images
            logger.debug(f"[MessageHistory] 内存缓存图片: {session_id}, {len(images)} 张")
            return True
        
        try:
            cache_key = f"{self.IMAGE_CACHE_PREFIX}{session_id}"
            await r.setex(cache_key, self.CACHE_TTL, json.dumps(images, ensure_ascii=False))
            logger.debug(f"[MessageHistory] Redis 缓存图片: {session_id}, {len(images)} 张")
            return True
        except Exception as e:
            logger.error(f"[MessageHistory] 缓存图片失败: {e}")
            return False
    
    async def get_cached_images(self, session_id: str) -> List[dict]:
        """
        获取上一轮缓存的图片（兜底复用）
        
        Returns:
            图片列表，若无则返回空列表
        """
        r = await self._get_redis()
        
        if not r:
            # 降级到内存缓存
            return self._image_cache.get(session_id, [])
        
        try:
            cache_key = f"{self.IMAGE_CACHE_PREFIX}{session_id}"
            raw = await r.get(cache_key)
            if raw:
                return json.loads(raw)
            return []
        except Exception as e:
            logger.error(f"[MessageHistory] 获取缓存图片失败: {e}")
            return []
    
    # ========== 情感记忆持久化 ==========
    EMOTION_CACHE_PREFIX = "museum:emotion:"
    _emotion_cache: dict[str, str] = {}  # 情感缓存降级
    
    async def save_emotion(self, session_id: str, emotion: str) -> bool:
        """
        保存当前情感状态（用于跨请求语音连贯性）
        
        Args:
            session_id: 会话ID
            emotion: 情感标记 (如 "excited", "gentle" 等)
        """
        if not emotion:
            return False
        
        r = await self._get_redis()
        if not r:
            # 降级到内存缓存
            self._emotion_cache[session_id] = emotion
            logger.debug(f"[MessageHistory] 内存缓存情感: {session_id} -> {emotion}")
            return True
        
        try:
            cache_key = f"{self.EMOTION_CACHE_PREFIX}{session_id}"
            await r.setex(cache_key, self.CACHE_TTL, emotion)
            logger.debug(f"[MessageHistory] Redis 缓存情感: {session_id} -> {emotion}")
            return True
        except Exception as e:
            logger.error(f"[MessageHistory] 缓存情感失败: {e}")
            return False
    
    async def get_emotion(self, session_id: str) -> Optional[str]:
        """
        获取上次缓存的情感状态
        
        Returns:
            情感标记，若无则返回 None
        """
        r = await self._get_redis()
        
        if not r:
            # 降级到内存缓存
            return self._emotion_cache.get(session_id)
        
        try:
            cache_key = f"{self.EMOTION_CACHE_PREFIX}{session_id}"
            return await r.get(cache_key)
        except Exception as e:
            logger.error(f"[MessageHistory] 获取缓存情感失败: {e}")
            return None


class SessionRepository:
    """
    导览会话仓储
    
    职责：
    - 会话 CRUD 操作
    - Redis 缓存管理 (人物类型快速读取)
    """
    
    CACHE_PREFIX = "museum:session:"
    CACHE_TTL = 3600  # 1 小时
    
    def __init__(self):
        self._redis: Optional[redis.Redis] = None
    
    async def get_redis(self) -> Optional[redis.Redis]:
        """获取 Redis 连接（懒加载 + 容错）"""
        if self._redis is None:
            try:
                settings = get_settings()
                self._redis = redis.from_url(
                    settings.redis.connection_url,
                    decode_responses=True
                )
                await self._redis.ping()
            except Exception as e:
                logger.warning(f"[Museum Session] Redis 连接失败: {e}")
                self._redis = None
        return self._redis
    
    async def create(self, data: GuideSessionCreate, workspace_id: str = "default") -> str:
        """
        创建会话
        
        Returns:
            新创建的会话 ID
        """
        async with get_async_db_context() as session:
            db_session = MuseumGuideSession(
                workspace_id=workspace_id,
                visitor_uuid=data.visitor_uuid,
                person_type=data.person_type.value if data.person_type else None,
                person_features=data.person_features,
                visitor_image_path=data.visitor_image_path,
            )
            session.add(db_session)
            await session.commit()
            await session.refresh(db_session)
            
            logger.info(f"[Museum Session] 创建会话: {db_session.id}")
            return db_session.id
    
    async def get(
        self,
        session_id: str,
        workspace_id: Optional[str] = None,
    ) -> Optional[GuideSessionResponse]:
        """获取会话"""
        async with get_async_db_context() as session:
            stmt = select(MuseumGuideSession).where(MuseumGuideSession.id == session_id)
            if workspace_id:
                stmt = stmt.where(MuseumGuideSession.workspace_id == workspace_id)
            result = await session.execute(stmt)
            db_session = result.scalar_one_or_none()
            
            if db_session:
                return GuideSessionResponse(
                    id=db_session.id,
                    visitor_uuid=db_session.visitor_uuid,
                    person_type=PersonType(db_session.person_type) if db_session.person_type else None,
                    person_features=db_session.person_features,
                    created_at=db_session.created_at,
                    updated_at=db_session.updated_at,
                )
            return None
    
    async def get_person_type(
        self,
        session_id: str,
        workspace_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        获取会话的人物类型（优先 Redis）
        
        策略：
        1. 先查 Redis 缓存
        2. 缓存未命中则查 DB
        3. 查到后回写 Redis
        """
        cache_key = f"{self.CACHE_PREFIX}{session_id}:person_type"
        
        # 1. 尝试 Redis
        r = await self.get_redis()
        if r:
            try:
                cached = await r.get(cache_key)
                if cached:
                    logger.debug(f"[Museum Session] 缓存命中: {session_id}")
                    return cached
            except Exception as e:
                logger.warning(f"[Museum Session] Redis 读取失败: {e}")
        
        # 2. 查 DB
        async with get_async_db_context() as session:
            stmt = select(MuseumGuideSession.person_type).where(
                MuseumGuideSession.id == session_id
            )
            if workspace_id:
                stmt = stmt.where(MuseumGuideSession.workspace_id == workspace_id)
            result = await session.execute(stmt)
            person_type = result.scalar_one_or_none()
            
            if person_type:
                # 3. 回写 Redis
                if r:
                    try:
                        await r.setex(cache_key, self.CACHE_TTL, person_type)
                    except Exception as e:
                        logger.warning(f"[Museum Session] Redis 写入失败: {e}")
                
                return person_type
        
        return None
    
    async def update_person_type(
        self, 
        session_id: str, 
        person_type: str,
        features: Optional[List[str]] = None,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """
        更新会话人物类型
        
        同时更新 DB 和 Redis 缓存
        """
        # 更新 DB
        async with get_async_db_context() as session:
            stmt = update(MuseumGuideSession).where(MuseumGuideSession.id == session_id)
            if workspace_id:
                stmt = stmt.where(MuseumGuideSession.workspace_id == workspace_id)
            stmt = stmt.values(
                person_type=person_type,
                person_features=features,
                updated_at=datetime.utcnow(),
            )
            result = await session.execute(stmt)
            await session.commit()
            
            if result.rowcount == 0:
                return False
        
        # 更新 Redis 缓存
        r = await self.get_redis()
        if r:
            try:
                cache_key = f"{self.CACHE_PREFIX}{session_id}:person_type"
                await r.setex(cache_key, self.CACHE_TTL, person_type)
            except Exception as e:
                logger.warning(f"[Museum Session] Redis 更新失败: {e}")
        
        logger.info(f"[Museum Session] 更新人物类型: {session_id} -> {person_type}")
        return True
    
    # ========== BUG2 修复: 文创商品上下文持久化 ==========
    PRODUCT_CACHE_PREFIX = "museum:last_product:"
    _product_cache: dict[str, str] = {}  # 内存降级缓存
    
    async def save_last_recommended_product(self, session_id: str, product_id: str) -> bool:
        """
        保存最近推荐的商品ID（用于文创咨询上下文）
        
        Args:
            session_id: 会话ID
            product_id: 商品ID
        """
        if not product_id:
            return False
        
        r = await self.get_redis()
        if not r:
            # 降级到内存缓存
            self._product_cache[session_id] = product_id
            logger.debug(f"[Museum Session] 内存缓存商品: {session_id} -> {product_id}")
            return True
        
        try:
            cache_key = f"{self.PRODUCT_CACHE_PREFIX}{session_id}"
            await r.setex(cache_key, self.CACHE_TTL, product_id)
            logger.debug(f"[Museum Session] Redis 缓存商品: {session_id} -> {product_id}")
            return True
        except Exception as e:
            logger.error(f"[Museum Session] 缓存商品失败: {e}")
            return False
    
    async def get_last_recommended_product(self, session_id: str) -> Optional[str]:
        """
        获取最近推荐的商品ID
        
        Returns:
            商品ID，若无则返回 None
        """
        r = await self.get_redis()
        
        if not r:
            # 降级到内存缓存
            return self._product_cache.get(session_id)
        
        try:
            cache_key = f"{self.PRODUCT_CACHE_PREFIX}{session_id}"
            return await r.get(cache_key)
        except Exception as e:
            logger.error(f"[Museum Session] 获取缓存商品失败: {e}")
            return None
    
    async def delete(
        self,
        session_id: str,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """
        删除会话
        
        同时删除 DB 记录和 Redis 缓存
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否删除成功
        """
        # 删除 DB 记录
        async with get_async_db_context() as session:
            stmt = delete(MuseumGuideSession).where(MuseumGuideSession.id == session_id)
            if workspace_id:
                stmt = stmt.where(MuseumGuideSession.workspace_id == workspace_id)
            result = await session.execute(stmt)
            await session.commit()
            
            if result.rowcount == 0:
                return False
        
        # 删除 Redis 缓存
        r = await self.get_redis()
        if r:
            try:
                cache_key = f"{self.CACHE_PREFIX}{session_id}:person_type"
                await r.delete(cache_key)
            except Exception as e:
                logger.warning(f"[Museum Session] Redis 删除失败: {e}")
        
        logger.info(f"[Museum Session] 会话已删除: {session_id}")
        return True
    
    async def close(self):
        """关闭连接"""
        if self._redis:
            await self._redis.close()
            self._redis = None


# ========== 单例工厂 ==========

_session_repository: Optional[SessionRepository] = None
_message_history: Optional[MessageHistory] = None


def get_session_repository() -> SessionRepository:
    """获取会话仓储单例"""
    global _session_repository
    if _session_repository is None:
        _session_repository = SessionRepository()
    return _session_repository


def get_message_history() -> MessageHistory:
    """获取消息历史管理单例"""
    global _message_history
    if _message_history is None:
        _message_history = MessageHistory()
    return _message_history

