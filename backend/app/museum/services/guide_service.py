"""
博物馆模块 - 导览主服务

遵循设计原则：
- 严谨设计原则 (Design Rigor): 并行流水线架构
- 全异步 I/O (Async First): asyncio.gather 并行任务
- 配置外置原则 (No Hardcoding): 配置从环境变量读取

功能：
- 并行流水线处理 (VLM/Search/Cache 并发)
- 一次识别，全程复用 (会话缓存策略)
- RAG + 导购融合 (带货钩子)
"""
import asyncio
import uuid
import json
import logging
import base64
import re
from typing import Optional, List, AsyncIterator, Callable, Awaitable

from app.museum.config import get_museum_settings
from app.museum.models import (
    PersonType,
    PersonAnalysisResult,
    AdjustmentResult,
    ProductRecommendItem,
)
from app.museum.services.session_repository import get_session_repository, get_message_history
from app.museum.services.person_recognizer import get_person_recognizer
from app.museum.services.prompt_adjuster import get_prompt_adjuster
from app.museum.services.sentence_buffer import SentenceBuffer
from app.museum.services.tts_service import get_tts_connection_pool
from app.museum.services.emotion_parser import (
    parse_emotion_tags, 
    strip_emotion_tags, 
    get_default_emotion,
)
from app.museum.services.query_rewriter import get_query_rewriter
from app.museum.services.catalog_service import get_catalog_service

logger = logging.getLogger(__name__)

# Jieba keywords extraction removed in favor of LLM Query Rewriter



class SSEEmitter:
    """SSE 事件发射器"""
    
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
    
    async def emit(self, event_type: str, payload: dict):
        """发射事件"""
        if not self._closed:
            await self._queue.put({
                "type": event_type,
                "payload": payload
            })
    
    async def close(self):
        """关闭发射器"""
        self._closed = True
        await self._queue.put(None)  # 发送结束信号
    
    async def __aiter__(self):
        """异步迭代器"""
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event


class GuideService:
    """
    导览主服务
    
    实现并行流水线架构：
    1. 请求到达时并发启动: DocSkill Search + PersonRecognizer (如有图)
    2. PromptAdjuster 调节 (毫秒级，需要 person_type)
    3. GuideAgent 生成 + TTS 流式输出
    
    优化点：
    - VLM 5秒超时优雅降级
    - 一次识别，全程复用
    - 带货钩子融合
    """
    
    def __init__(self):
        from app.museum.config import get_museum_prompt_settings
        self.settings = get_museum_settings()
        self.prompt_settings = get_museum_prompt_settings()
        self.session_repo = get_session_repository()
        self.message_history = get_message_history()
        self.person_recognizer = get_person_recognizer()
        self.prompt_adjuster = get_prompt_adjuster()
    
    async def process_guide(
        self,
        session_id: str,
        query: str,
        image_path: Optional[str] = None,
        enable_tts: bool = True,
        dept_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        skip_person_recognition: bool = False,
    ) -> AsyncIterator[dict]:
        """
        处理导览请求 (并行流水线)
        
        Args:
            session_id: 会话ID
            query: 用户问题
            image_path: 图片路径 (可选)
            enable_tts: 是否启用 TTS
            dept_id: 部门ID (用于知识库检索权限过滤)
            skip_person_recognition: 跳过人物识别 (对话区图片仅用于VL检索)
            
        Yields:
            SSE 事件 dict
        """
        emitter = SSEEmitter()
        
        # 启动处理任务
        process_task = asyncio.create_task(
            self._process_impl(
                emitter=emitter,
                session_id=session_id,
                query=query,
                image_path=image_path,
                enable_tts=enable_tts,
                dept_id=dept_id or self.settings.guide_dept_id,
                workspace_id=workspace_id,
                skip_person_recognition=skip_person_recognition,
            )
        )
        
        # 消费事件队列
        try:
            async for event in emitter:
                yield event
        finally:
            process_task.cancel()
    
    async def _process_impl(
        self,
        emitter: SSEEmitter,
        session_id: str,
        query: str,
        image_path: Optional[str],
        enable_tts: bool,
        dept_id: str,
        workspace_id: Optional[str],
        skip_person_recognition: bool = False,
    ):
        """并行流水线实现 (Modular Refactor v7)"""
        try:
            # 新请求开始时，关闭上一轮的 TTS 连接
            if enable_tts and session_id:
                tts_pool = get_tts_connection_pool()
                await tts_pool.release_connection(session_id)
            
            # ========== Phase 1: 启动 VLM (最耗时任务) ==========
            person_type: Optional[str] = None
            person_features: List[str] = []
            vlm_task = None
            artifact_task = None  # 文物识别任务
            artifact_context = ""  # 文物识别结果上下文
            
            if image_path:
                # 有新图片时，同时启动人物识别和文物识别
                if not skip_person_recognition:
                    vlm_task = asyncio.create_task(
                        self.person_recognizer.analyze(image_path)
                    )
                
                # 启动文物识别（总是执行，用于理解图片内容）
                from app.museum.services.artifact_recognizer import get_artifact_recognizer
                artifact_recognizer = get_artifact_recognizer()
                artifact_task = asyncio.create_task(
                    artifact_recognizer.recognize(image_path, timeout=10.0)
                )
                logger.info(f"[GuideService] 启动文物识别任务: {image_path}")
            else:
                # 无新图片，从会话缓存加载人物类型
                person_type = await self.session_repo.get_person_type(
                    session_id, workspace_id=workspace_id
                )
                if person_type:
                    logger.info(f"[GuideService] 从缓存加载人物类型: {person_type}")

            # ========== Phase 2: 上下文改写与线索注入 ==========
            # 等待文物识别结果（如果有）
            if artifact_task:
                artifact_result = await artifact_task
                if artifact_result.artifact_name:
                    # 识别到文物，将其注入查询上下文
                    artifact_context = f"[图片识别结果: {artifact_result.artifact_name}]"
                    logger.info(f"[GuideService] 文物识别成功: {artifact_result.artifact_name} (置信度: {artifact_result.confidence})")
                elif artifact_result.description:
                    # 有描述但无具体名称
                    artifact_context = f"[图片内容: {artifact_result.description[:50]}]"
                    logger.info(f"[GuideService] 文物识别降级: {artifact_result.description[:50]}")
                else:
                    logger.info(f"[GuideService] 文物识别无结果: {artifact_result.reason}")
            
            # 获取历史消息 (用于改写)
            history_messages = []
            if session_id:
                history_messages = await self.message_history.get_history(session_id, limit=5)
            
            # 调用改写服务（将图片识别结果注入）
            rewriter = get_query_rewriter()
            query_with_context = f"{artifact_context} {query}" if artifact_context else query
            rewrite_result = await rewriter.rewrite_query(query_with_context, history_messages)
            
            rewritten_query = rewrite_result.get("rewritten_query", query)
            intent = rewrite_result.get("intent", "specific_query")
            floor_hint = rewrite_result.get("floor_hint")  # BUG2修复: 获取楼层提示
            
            logger.info(f"[GuideService] Rewrite: '{query}' -> '{rewritten_query}' (Intent: {intent}, Floor: {floor_hint}, ArtifactCtx: {bool(artifact_context)})")
            
            # 目录线索注入 (Seeding)
            catalog = get_catalog_service()
            if intent == "broad_recommendation":
                seeds = await catalog.get_catalog_seeds(
                    limit=3,
                    workspace_id=workspace_id,
                )
                if seeds:
                    seed_text = "、".join(seeds)
                    rewritten_query += f" (参考代表性文物: {seed_text})"
                    logger.info(f"[GuideService] Catalog Seeding: Injected {seeds}")
            
            # ========== 闲聊意图特殊处理：走 RAG 但不显示商品和图片卡片 ==========
            is_chitchat = intent in ("chitchat", "topic_switch")
            if is_chitchat:
                logger.info(f"[GuideService] 检测到闲聊/话题切换意图，走 RAG 但跳过商品和图片卡片")
            
            # ========== BUG1 修复: 负面反馈意图处理（用户纯抱怨时使用）==========
            if intent == "negative_feedback":
                logger.info(f"[GuideService] 检测到负面反馈意图，使用共情回复，跳过RAG和商品推荐")
                
                # 使用配置模板生成共情回复
                negative_context = self.prompt_settings.negative_feedback_context_template.format(
                    query=query
                )
                
                # 直接生成共情回复，不走RAG也不推荐商品
                full_response = await self._generate_response(
                    context=negative_context,
                    emitter=emitter,
                    enable_tts=enable_tts,
                    person_type=person_type or PersonType.GENERAL.value,
                    history_messages=history_messages,
                    session_id=session_id,
                    image_candidates=[]  # 不推荐图片
                )
                
                await self.message_history.add_round(
                    session_id=session_id,
                    user_msg=query,
                    assistant_msg=full_response,
                )
                
                await emitter.emit("GUIDE_END", {
                    "session_id": session_id,
                    "message_length": len(full_response),
                })
                return
            
            # ========== BUG1 修复: 价格异议意图处理（用户犹豫但有意向时使用）==========
            if intent == "price_objection":
                logger.info(f"[GuideService] 检测到价格异议意图，进行价值塑造回复")
                from app.museum.services.product_service import get_product_service
                product_svc = get_product_service()
                
                # 尝试获取上下文商品
                product = None
                last_product_id = await self.session_repo.get_last_recommended_product(session_id)
                if last_product_id:
                    product = await product_svc.get_product(last_product_id)
                
                if product:
                    # 有商品上下文，发送商品卡片并进行价值塑造
                    product_card = ProductRecommendItem(
                        id=product.id,
                        name=product.name,
                        price=float(product.price),
                        image_url=product.image_urls[0] if product.image_urls else None,
                        relation="您正在了解的商品"
                    )
                    await emitter.emit("PRODUCT_RECOMMEND", {
                        "products": [product_card.model_dump()]
                    })
                    
                    # 使用价格异议模板进行价值塑造
                    price_context = self.prompt_settings.price_objection_context_template.format(
                        product_name=product.name,
                        product_description=product.description or '暂无描述',
                        product_price=product.price,
                        query=query
                    )
                    
                    full_response = await self._generate_response(
                        context=price_context,
                        emitter=emitter,
                        enable_tts=enable_tts,
                        person_type=person_type or PersonType.GENERAL.value,
                        history_messages=history_messages,
                        session_id=session_id,
                        image_candidates=[]
                    )
                else:
                    # 无商品上下文，使用通用负面反馈模板
                    negative_context = self.prompt_settings.negative_feedback_context_template.format(
                        query=query
                    )
                    full_response = await self._generate_response(
                        context=negative_context,
                        emitter=emitter,
                        enable_tts=enable_tts,
                        person_type=person_type or PersonType.GENERAL.value,
                        history_messages=history_messages,
                        session_id=session_id,
                        image_candidates=[]
                    )
                
                await self.message_history.add_round(
                    session_id=session_id,
                    user_msg=query,
                    assistant_msg=full_response,
                )
                
                await emitter.emit("GUIDE_END", {
                    "session_id": session_id,
                    "message_length": len(full_response),
                })
                return
            
            # ========== 文创商品意图特殊处理 ==========
            if intent == "product_inquiry":
                logger.info(f"[GuideService] 检测到文创商品咨询意图，跳过知识库检索")
                from app.museum.services.product_service import get_product_service
                product_svc = get_product_service()
                product = None
                
                # 检测用户是否想看"别的"商品
                other_patterns = ["别的", "其他", "其它", "还有", "还有什么", "还有哪些", "换一个", "换个"]
                is_other_request = any(p in query for p in other_patterns)
                
                if is_other_request:
                    # 用户想看其他商品 - 使用三级商品推荐逻辑
                    logger.info(f"[GuideService] 检测到'别的商品'请求，使用三级推荐逻辑")
                    
                    # 排除上次推荐的商品
                    last_product_id = await self.session_repo.get_last_recommended_product(session_id)
                    
                    # 第一级：文物相关商品
                    search_results = await product_svc.search_products(rewritten_query, limit=5)
                    if last_product_id and search_results:
                        search_results = [p for p in search_results if p.id != last_product_id]
                    
                    recommend_source = "文物关联"
                    related_artifact = rewritten_query.replace("的其他文创周边", "").replace("周边", "").replace("文创", "").strip()
                    
                    # 第二级：如果文物相关不足，补充身份相关商品
                    if len(search_results) < 3:
                        logger.info(f"[GuideService] 文物相关商品不足({len(search_results)}个)，尝试身份相关推荐")
                        try:
                            pt_enum = PersonType(person_type) if person_type else PersonType.GENERAL
                        except:
                            pt_enum = PersonType.GENERAL
                        
                        person_products = await catalog.get_global_products(
                            pt_enum,
                            limit=5,
                            workspace_id=workspace_id,
                        )
                        # 排除已有的和已推荐的商品
                        existing_ids = {p.id for p in search_results}
                        if last_product_id:
                            existing_ids.add(last_product_id)
                        
                        for pp in person_products:
                            if pp.id not in existing_ids and len(search_results) < 5:
                                # 转换 ProductRecommendItem 回 ProductResponse
                                full_product = await product_svc.get_product(pp.id)
                                if full_product:
                                    search_results.append(full_product)
                                    existing_ids.add(pp.id)
                        
                        if len(search_results) > 0:
                            recommend_source = "精选推荐"
                            logger.info(f"[GuideService] 补充身份相关后共{len(search_results)}个商品")
                    
                    # 第三级：如果仍然不足，随机兜底
                    if len(search_results) < 2:
                        logger.info(f"[GuideService] 身份相关也不足，使用随机兜底")
                        all_products = await product_svc.list_products(status="active", limit=10)
                        if all_products[0]:
                            existing_ids = {p.id for p in search_results}
                            if last_product_id:
                                existing_ids.add(last_product_id)
                            
                            import random
                            shuffled = list(all_products[0])
                            random.shuffle(shuffled)
                            
                            for p in shuffled:
                                if p.id not in existing_ids and len(search_results) < 5:
                                    search_results.append(p)
                                    existing_ids.add(p.id)
                        
                        recommend_source = "热门推荐"
                    
                    if search_results:
                        # 构造商品列表字符串文物相关 身份相关  当文物相关不足两个的时候补充
                        products_list = "\n".join([
                            f"- {p.name}：{p.description or '暂无描述'}，价格¥{p.price}，库存{p.stock}件"
                            for p in search_results
                        ])
                        
                        # 使用多商品推荐模板
                        product_context = self.prompt_settings.multi_product_recommend_template.format(
                            related_artifact=related_artifact or "综合",
                            products_list=products_list,
                            query=query,
                            person_type=person_type or "通用访客"
                        )
                        
                        # 发送所有商品卡片
                        await emitter.emit("PRODUCT_RECOMMEND", {
                            "products": [ProductRecommendItem(
                                id=p.id,
                                name=p.name,
                                price=float(p.price),
                                image_url=p.image_urls[0] if p.image_urls else None,
                                relation=recommend_source
                            ).model_dump() for p in search_results[:5]]
                        })
                        
                        # 缓存第一个商品
                        await self.session_repo.save_last_recommended_product(session_id, search_results[0].id)
                        
                        # 生成响应
                        full_response = await self._generate_response(
                            context=product_context,
                            emitter=emitter,
                            enable_tts=enable_tts,
                            person_type=person_type or PersonType.GENERAL.value,
                            history_messages=history_messages,
                            session_id=session_id,
                            image_candidates=[]
                        )
                        
                        await self.message_history.add_round(
                            session_id=session_id,
                            user_msg=query,
                            assistant_msg=full_response,
                        )
                        
                        await emitter.emit("GUIDE_END", {
                            "session_id": session_id,
                            "message_length": len(full_response),
                        })
                        return
                    else:
                        # 极端情况：数据库完全没有商品
                        no_more_msg = "抱歉，暂时没有找到可推荐的文创商品。我们的文创区正在上新中，请稍后再来看看哦！"
                        await emitter.emit("MESSAGE_CHUNK", {"text": no_more_msg, "done": False})
                        await emitter.emit("MESSAGE_END", {"full_text": no_more_msg})
                        await self.message_history.add_round(session_id=session_id, user_msg=query, assistant_msg=no_more_msg)
                        await emitter.emit("GUIDE_END", {"session_id": session_id, "message_length": len(no_more_msg)})
                        return
                
                # 策略1: 优先从改写后的查询中提取商品名称进行搜索
                # 例如 "再讲讲金缕玉衣拼图" -> 搜索 "金缕玉衣拼图"
                search_results = await product_svc.search_products(rewritten_query, limit=3)
                if search_results:
                    product = search_results[0]
                    logger.info(f"[GuideService] 从改写查询搜索到商品: {product.name}")
                
                # 策略2: 检查用户是否使用了模糊指代（如"这个"、"它"、"这件"等）
                # 如果是模糊指代，才使用缓存商品；如果明确指定了文物名则不使用缓存
                pronoun_patterns = ["这个", "那个", "它", "这件", "那件", "这款", "那款", "刚才那个"]
                is_pronoun_query = any(p in query for p in pronoun_patterns)
                
                if not product and is_pronoun_query:
                    # 只在模糊指代时使用缓存
                    last_product_id = await self.session_repo.get_last_recommended_product(session_id)
                    logger.info(f"[GuideService] 模糊指代，尝试缓存商品ID: {last_product_id}")
                    if last_product_id:
                        product = await product_svc.get_product(last_product_id)
                        if product:
                            logger.info(f"[GuideService] 使用缓存商品: {product.name}")
                
                # 策略3已移除：三级推荐机制保证必有商品推荐，无需"诚实告知暂无"
                
                if product:
                    # BUG3 修复: 发送商品推荐卡片（确保第二轮对话仍有卡片）
                    product_card = ProductRecommendItem(
                        id=product.id,
                        name=product.name,
                        price=float(product.price),
                        image_url=product.image_urls[0] if product.image_urls else None,
                        relation="您正在咨询的商品"
                    )
                    await emitter.emit("PRODUCT_RECOMMEND", {
                        "products": [product_card.model_dump()]
                    })
                    
                    # 构造商品咨询响应（使用配置模板）
                    product_context = self.prompt_settings.product_inquiry_context_template.format(
                        product_name=product.name,
                        product_description=product.description or '暂无描述',
                        product_price=product.price,
                        product_category=product.category,
                        product_stock=product.stock,
                        query=query
                    )
                    
                    # 跳过检索，直接生成响应
                    full_response = await self._generate_response(
                        context=product_context,
                        emitter=emitter,
                        enable_tts=enable_tts,
                        person_type=person_type or PersonType.GENERAL.value,
                        history_messages=history_messages,
                        session_id=session_id,
                        image_candidates=[]
                    )
                    
                    await self.message_history.add_round(
                        session_id=session_id,
                        user_msg=query,
                        assistant_msg=full_response,
                    )
                    
                    await emitter.emit("GUIDE_END", {
                        "session_id": session_id,
                        "message_length": len(full_response),
                    })
                    return
                
                # 没有上下文商品或商品查询失败 -> 使用三级推荐兜底
                logger.info(f"[GuideService] 无上下文商品ID，尝试三级推荐兜底")
                try:
                    pt_enum = PersonType(person_type) if person_type else PersonType.GENERAL
                except:
                    pt_enum = PersonType.GENERAL
                
                # 获取角色推荐商品
                fallback_products = await catalog.get_global_products(
                    pt_enum,
                    limit=5,
                    workspace_id=workspace_id,
                )
                
                if fallback_products:
                    # 获取完整商品信息
                    full_products = []
                    for pp in fallback_products:
                        full_p = await product_svc.get_product(pp.id)
                        if full_p:
                            full_products.append(full_p)
                    
                    if full_products:
                        # 发送商品卡片
                        await emitter.emit("PRODUCT_RECOMMEND", {
                            "products": [pp.model_dump() for pp in fallback_products[:5]]
                        })
                        await self.session_repo.save_last_recommended_product(session_id, fallback_products[0].id)
                        
                        # 构造商品列表传给LLM
                        products_list = "\n".join([
                            f"- {p.name}：{p.description or '暂无描述'}，价格¥{p.price}"
                            for p in full_products
                        ])
                        
                        product_context = self.prompt_settings.multi_product_recommend_template.format(
                            related_artifact="热门精选",
                            products_list=products_list,
                            query=query,
                            person_type=person_type or "通用访客"
                        )
                        
                        # 使用LLM生成回复（支持TTS）
                        full_response = await self._generate_response(
                            context=product_context,
                            emitter=emitter,
                            enable_tts=enable_tts,
                            person_type=person_type or PersonType.GENERAL.value,
                            history_messages=history_messages,
                            session_id=session_id,
                            image_candidates=[]
                        )
                        
                        await self.message_history.add_round(
                            session_id=session_id,
                            user_msg=query,
                            assistant_msg=full_response,
                        )
                        
                        await emitter.emit("GUIDE_END", {
                            "session_id": session_id,
                            "message_length": len(full_response),
                        })
                        return
                
                # 极端情况：数据库完全没有商品
                fallback_msg = "抱歉，目前没有可推荐的文创商品。我们的文创区正在上新中，请稍后再来看看哦！"
                await emitter.emit("MESSAGE_CHUNK", {"text": fallback_msg, "done": False})
                await emitter.emit("MESSAGE_END", {"full_text": fallback_msg})
                await self.message_history.add_round(session_id=session_id, user_msg=query, assistant_msg=fallback_msg)
                await emitter.emit("GUIDE_END", {"session_id": session_id, "message_length": len(fallback_msg)})
                return
            
            # ========== Phase 3: 知识库检索 ==========
            # 使用改写后的 Query 进行检索
            # 闲聊时只检索指定 file_id 的文档（如果配置了）
            file_id_filter = self.settings.guide_chitchat_file_id if is_chitchat else None
            search_task = asyncio.create_task(
                self._search_exhibits(rewritten_query, dept_id, file_id_filter=file_id_filter, floor_hint=floor_hint)
            )
            
            # --- 等待 VLM 结果 (带超时降级) ---
            if vlm_task:
                vlm_timeout = self.settings.vlm_timeout  # 从配置读取超时时间
                try:
                    analysis = await asyncio.wait_for(vlm_task, timeout=vlm_timeout)
                    person_type = analysis.person_type.value
                    person_features = analysis.features
                    
                    # 推送识别结果
                    await emitter.emit("PERSON_ANALYSIS", {
                        "person_type": person_type,
                        "confidence": analysis.confidence,
                        "features": person_features,
                        "fallback": analysis.fallback,
                        "reason": analysis.reason,
                    })
                    
                    # update session async
                    asyncio.create_task(
                        self.session_repo.update_person_type(
                            session_id,
                            person_type,
                            person_features,
                            workspace_id=workspace_id,
                        )
                    )
                except asyncio.TimeoutError:
                    # VLM 超时，优雅降级为通用访客
                    logger.warning(f"[GuideService] VLM 识别超时 ({vlm_timeout}s)，降级为通用访客")
                    vlm_task.cancel()  # 取消任务，释放资源
                    try:
                        await vlm_task
                    except asyncio.CancelledError:
                        pass
                    
                    person_type = PersonType.GENERAL.value
                    person_features = []
                    
                    # 推送降级通知
                    await emitter.emit("PERSON_ANALYSIS", {
                        "person_type": person_type,
                        "confidence": 0.0,
                        "features": [],
                        "fallback": True,
                        "reason": f"VLM超时({vlm_timeout}s)，使用默认类型",
                    })
                except Exception as vlm_err:
                    # VLM 其他异常，同样降级
                    logger.error(f"[GuideService] VLM 识别失败: {vlm_err}，降级为通用访客")
                    person_type = PersonType.GENERAL.value
                    person_features = []
                    
                    await emitter.emit("PERSON_ANALYSIS", {
                        "person_type": person_type,
                        "confidence": 0.0,
                        "features": [],
                        "fallback": True,
                        "reason": f"VLM异常: {str(vlm_err)[:50]}",
                    })
            
            if not person_type:
                person_type = PersonType.GENERAL.value
                
            # --- 等待检索结果 ---
            search_results = await search_task
            await emitter.emit("SEARCH_RESULT", {
                "count": len(search_results),
                "keywords": [], # 移除旧的关键词提取
            })
            
            # ========== BUG3 修复: 统计问题阈值兜底 ==========
            # 检测是否为统计类问题
            is_statistics_query = any(kw in query for kw in self.prompt_settings.statistics_keywords)
            
            # 检查 RAG 结果可靠性（通过结果数量判断，后续可扩展为分数判断）
            if is_statistics_query and len(search_results) == 0:
                # 统计问题但无检索结果，使用诚实回复
                logger.info(f"[GuideService] 统计问题无检索结果，使用兜底回复")
                fallback_msg = self.prompt_settings.no_statistics_data_template.format(query=query)
                await emitter.emit("MESSAGE_CHUNK", {"text": fallback_msg, "done": False})
                await emitter.emit("MESSAGE_END", {"full_text": fallback_msg})
                
                await self.message_history.add_round(
                    session_id=session_id,
                    user_msg=query,
                    assistant_msg=fallback_msg,
                )
                
                await emitter.emit("GUIDE_END", {
                    "session_id": session_id,
                    "message_length": len(fallback_msg),
                })
                return
            
            # ========== Phase 4: 资源提取 (图片与商品) ==========
            # BUG4 修复: 闲聊模式下跳过商品和图片卡片，同时增强对抱怨场景的处理
            if is_chitchat:
                related_products = []
                image_candidates = []
            else:
                # 提取相关商品
                related_products, _ = await self._get_related_products(
                    search_results,
                    workspace_id=workspace_id,
                )
                
                # 提取候选图片列表（供 LLM 选择，Context 注入策略）
                image_candidates = self._extract_image_candidates(search_results)
                if len(related_products) < 3:
                    # 数量不足，使用全局推荐补全
                    global_limit = 3 - len(related_products)
                    try:
                        pt_enum = PersonType(person_type)
                    except:
                        pt_enum = PersonType.GENERAL
                        
                    global_products = await catalog.get_global_products(
                        pt_enum,
                        limit=global_limit + 3,
                        workspace_id=workspace_id,
                    )
                    
                    existing_ids = {p.id for p in related_products}
                    for gp in global_products:
                        if gp.id not in existing_ids and len(related_products) < 3:
                            related_products.append(gp)
                            existing_ids.add(gp.id)
                            
                    logger.info(f"[GuideService] Used Global Products fallback, total: {len(related_products)} items")

                # 发送商品推荐 (商品可以先发，图片要等引用)
                if related_products:
                    await emitter.emit("PRODUCT_RECOMMEND", {
                        "products": [p.model_dump() for p in related_products]
                    })
                    first_product = related_products[0]
                    product_id_str = str(first_product.id)
                    await self.session_repo.save_last_recommended_product(session_id, product_id_str)
                    logger.info(f"[GuideService] 缓存推荐商品ID: {product_id_str}")

            # ========== Phase 5: 提示词调节与生成 ==========
            adjustment = await self.prompt_adjuster.adjust(
                person_type=person_type,
                base_context=rewritten_query,
                person_features=person_features
            )
            
            context = self._build_context(
                query=rewritten_query,
                search_results=search_results,
                adjustment=adjustment,
                related_products=related_products,
                image_candidates=image_candidates,
            )
            
            # 生成回答（传入候选图片列表用于流式解析匹配）
            full_response = await self._generate_response(
                context=context,
                emitter=emitter,
                enable_tts=enable_tts,
                person_type=person_type,
                history_messages=history_messages,
                session_id=session_id,
                image_candidates=image_candidates
            )
            
            # 保存历史 (使用 rewrite 后的 query 还是原始 query? 
            # 通常存原始 query，否则用户会看到改写后的。但这里为了上下文连贯，存原始 query 比较好)
            await self.message_history.add_round(
                session_id=session_id,
                user_msg=query,
                assistant_msg=full_response,
            )
            logger.info(f"[GuideService] 会话完成 session={session_id[:8]}")
            
            await emitter.emit("GUIDE_END", {
                "session_id": session_id,
                "message_length": len(full_response),
            })
            
        except Exception as e:
            logger.error(f"[GuideService] 处理失败: {e}", exc_info=True)
            await emitter.emit("ERROR", {"message": str(e)})
        finally:
            await emitter.close()
    
    async def _search_exhibits(self, query: str, dept_id: str, file_id_filter: str | None = None, floor_hint: str | None = None) -> List[dict]:
        """
        检索展品知识库
        
        Args:
            query: 检索查询
            dept_id: 部门ID
            file_id_filter: 可选，只检索此 file_id 的文档
            floor_hint: 可选，楼层过滤提示（如 "1楼"、"2楼"）
        """
        try:
            from app.skills.doc_skill import DocSkill
            from app.models.common.context import UserContext
            
            settings = get_museum_settings()
            user_context = UserContext(
                user_id="museum_visitor",
                workspace_id=settings.guide_workspace_id,
                username="游客",
                role="visitor",
                dept_id=int(dept_id) if dept_id and dept_id.isdigit() else None,
                data_scope=1,
            )
            
            doc_skill = DocSkill()
            # BUG2修复: 如果有楼层提示，增加top_k以避免过滤后结果为空
            top_k = self.settings.guide_default_top_k
            if floor_hint:
                top_k = top_k * 3  # 检索3倍数量以确保过滤后仍有足够结果
            
            results = await doc_skill.query_knowledge_base(
                query=query,
                user_context=user_context,
                top_k=top_k,
            )
            
            # 转换 DocumentChunk 对象为字典
            converted = []
            for r in results or []:
                if hasattr(r, 'metadata'):
                    converted.append({"content": r.content, "metadata": r.metadata})
                else:
                    converted.append(r)
            
            # 如果指定了 file_id 过滤，只保留匹配的结果
            if file_id_filter and converted:
                filtered = [r for r in converted if r.get("metadata", {}).get("file_id", "") == file_id_filter]
                if filtered:
                    logger.info(f"[GuideService] 闲聊文档过滤: {len(converted)} -> {len(filtered)} (file_id={file_id_filter})")
                    return filtered
                logger.warning(f"[GuideService] 闲聊文档过滤无匹配，使用全部结果")
            
            # BUG2修复: 楼层后置过滤
            if floor_hint and converted:
                # 标准化楼层关键词
                floor_keywords = []
                if "1" in floor_hint or "一" in floor_hint:
                    floor_keywords = ["1楼", "一楼", "1F", "1层"]
                elif "2" in floor_hint or "二" in floor_hint:
                    floor_keywords = ["2楼", "二楼", "2F", "2层"]
                elif "3" in floor_hint or "三" in floor_hint:
                    floor_keywords = ["3楼", "三楼", "3F", "3层"]
                
                if floor_keywords:
                    floor_filtered = []
                    for r in converted:
                        content = r.get("content", "")
                        # 检查文档内容是否包含楼层关键词
                        if any(kw in content for kw in floor_keywords):
                            floor_filtered.append(r)
                    
                    if floor_filtered:
                        logger.info(f"[GuideService] 楼层过滤: {len(converted)} -> {len(floor_filtered)} (floor={floor_hint})")
                        # 截取为原始 top_k
                        return floor_filtered[:self.settings.guide_default_top_k]
                    else:
                        logger.warning(f"[GuideService] 楼层过滤无匹配，使用全部结果 (floor={floor_hint})")
            
            return converted
        except Exception as e:
            logger.error(f"[GuideService] 知识库检索失败: {e}")
            return []
    
    
    def _extract_image_candidates(self, search_results: list) -> List[dict]:
        """从检索结果中提取候选图片列表（供 LLM 选择）
        
        策略：Context 注入 + 流式解析
        - 只收集候选列表，不做评分排序
        - LLM 根据叙述逻辑自行决定输出哪张图
        - 后端流式捕获 [[IMAGE:xxx]] 标签后立即 emit
        
        Args:
            search_results: DocumentChunk 对象列表
            
        Returns:
            候选图片列表 [{"id": "fileId/imageId", "name": "文物名称", ...}, ...]
        """
        from app.models.common.execution import DocumentChunk
        
        candidates = []
        seen = set()
        
        for result in search_results[:10]:
            if isinstance(result, DocumentChunk):
                metadata = result.metadata or {}
                content = result.content or ""
                header_path = result.header_path or ""
            else:
                metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
                content = result.get("content", "") if isinstance(result, dict) else ""
                header_path = metadata.get("header_path", "")
            
            file_id = metadata.get("file_id") or metadata.get("doc_id")
            chunk_type = metadata.get("type", "text")
            linked_ids = metadata.get("linked_image_ids", "")
            related_ids = metadata.get("related_image_ids", "")
            image_id = metadata.get("image_id", "")
            
            logger.info(f"[GuideService] Chunk分析: file_id={file_id}, type={chunk_type}, linked={linked_ids}, related={related_ids}, image_id={image_id}")
            
            if not file_id:
                continue
            
            # 提取文物名称
            raw_name = header_path.split(">")[-1].strip() if ">" in header_path else header_path
            artifact_name = self._sanitize_artifact_name(raw_name)
            if not artifact_name:
                artifact_name = metadata.get("title", "") or content[:30]
            
            chunk_type = metadata.get("type", "text")
            
            # 收集图片 ID
            # 兼容历史 image_summary 数据；主路径已切到 text chunk 的 linked/related_image_ids
            if chunk_type == "image_summary":
                image_id = metadata.get("image_id", "")
                if image_id:
                    img_key = f"{file_id}/{image_id}"
                    if img_key not in seen:
                        seen.add(img_key)
                        candidates.append({
                            "id": img_key,
                            "name": artifact_name,
                            "fileId": file_id,
                            "imageId": image_id
                        })
            else:
                # Text Chunk 关联的图片（优先 linked_image_ids，其次 related_image_ids）
                linked_ids = metadata.get("linked_image_ids", "") or metadata.get("related_image_ids", "")
                if isinstance(linked_ids, str):
                    linked_ids = [i.strip() for i in linked_ids.split(",") if i.strip()]
                elif isinstance(linked_ids, list):
                    linked_ids = [str(i).strip() for i in linked_ids if i]
                else:
                    linked_ids = []
                
                for image_id in linked_ids[:2]:
                    img_key = f"{file_id}/{image_id}"
                    if img_key not in seen:
                        seen.add(img_key)
                        candidates.append({
                            "id": img_key,
                            "name": artifact_name,
                            "fileId": file_id,
                            "imageId": image_id
                        })
        
        logger.info(f"[GuideService] 候选图片列表: {[c['name'] for c in candidates[:5]]}")
        return candidates
    

    @staticmethod
    def _sanitize_artifact_name(text: str) -> str:
        """
        清洗 RAG 返回的标题/文件名，提取文物名称
        
        Examples:
            "15. 三彩荷叶童子枕" -> "三彩荷叶童子枕"
            "20. 蟠螭纹铜盖鼎" -> "蟠螭纹铜盖鼎"
            "贾湖骨笛_v2.docx" -> "贾湖骨笛"
            "data/docs/妇好鸮尊.txt" -> "妇好鸮尊"
        """
        if not text:
            return ""
        
        name = text.strip()
        
        # 1. 去掉序号前缀 (如 "15. ", "20、", "第一章 ")
        name = re.sub(r'^(\d+[\.\、\s]+|第.+?章\s*)', '', name)
        
        # 2. 去掉路径
        name = name.replace("\\", "/").split("/")[-1]
        
        # 3. 去掉文件扩展名 (只处理常见扩展名，避免误删 "15.三彩" 中的内容)
        name = re.sub(r'\.(docx?|pdf|txt|md|xlsx?)$', '', name, flags=re.IGNORECASE)
            
        # 4. 去掉常见后缀 (v1, final, etc)
        name = re.sub(r'[_ -]?(v\d+|final|copy|副本|\(\d+\))$', '', name, flags=re.IGNORECASE)
        
        return name.strip()

    async def _get_related_products(
        self, 
        search_results: list,
        workspace_id: str | None = None,
    ) -> tuple[List[ProductRecommendItem], set[str]]:
        """获取相关商品 (Bridge Lookup Strategy)
        
        策略：
        1. Hook A (Bridge): RAG Title -> Sanitized Name -> Artifact Bridge -> Products
        2. Hook B (Fallback): Intent/Exhibit ID Search (原逻辑)
        
        Returns:
            Tuple[List[ProductRecommendItem], Set[str]]: 
                - 商品推荐列表
                - 匹配的文物名称集合（用于精准图片检索）
        """
        from app.models.common.execution import DocumentChunk
        from sqlalchemy import select, func, or_
        from app.core.db.database import get_async_db_context
        from app.core.db.tenant_mixin import get_current_workspace
        from app.museum.db import MuseumProduct, MuseumArtifact

        workspace = workspace_id or get_current_workspace() or "default"
        
        # 1. 提取所有可能的 Artifact Names
        # 策略：从 header_path（标题路径）中提取，扩大到前10个结果以获取更多文物多样性
        candidate_names = set()
        for i, result in enumerate(search_results[:10]):
            # 优先使用 DocumentChunk 的 header_path 属性
            if isinstance(result, DocumentChunk):
                header_path = result.header_path or ""
                metadata = result.metadata or {}
            else:
                metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
                header_path = metadata.get("header_path", "")
            
            # 从 header_path 中提取文物名称（通常是最后一级标题）
            if header_path:
                # header_path 格式如 "第一章 > 金缕玉衣" 或直接 "金缕玉衣"
                parts = header_path.split(">")
                for part in parts:
                    cleaned = self._sanitize_artifact_name(part.strip())
                    if cleaned:
                        candidate_names.add(cleaned)
            
            logger.info(f"[GuideService] Result {i}: header_path='{header_path}'")
            
        candidate_names = {n for n in candidate_names if n} # 过滤空值
        logger.info(f"[GuideService] Bridge Lookup Candidates: {candidate_names}")
        
        if not candidate_names:
            return [], set()

        try:
            async with get_async_db_context() as session:
                # 2. Bridge Lookup: Name -> Artifact IDs + Names
                # SELECT id, name FROM artifacts WHERE name IN (...)
                stmt = select(MuseumArtifact.id, MuseumArtifact.name).where(
                    MuseumArtifact.workspace_id == workspace,
                    MuseumArtifact.name.in_(candidate_names),
                )
                bridge_results = (await session.execute(stmt)).all()
                bridge_ids = [str(row[0]) for row in bridge_results]
                matched_artifact_names = {row[1] for row in bridge_results}  # 精准匹配的文物名称
                
                if bridge_ids:
                    logger.info(f"[GuideService] Bridge Hit! Artifact IDs: {bridge_ids}, Names: {matched_artifact_names}")
                    
                    # 3. Product Lookup: Related Exhibit IDs matches Bridge IDs
                    # 使用 JSON_CONTAINS 查找
                    conditions = [
                        func.json_contains(
                            MuseumProduct.related_exhibit_ids, 
                            f'"{bid}"'
                        )
                        for bid in bridge_ids
                    ]
                    
                    query = (
                        select(MuseumProduct)
                        .where(
                            MuseumProduct.workspace_id == workspace,
                            MuseumProduct.status == "active",
                        )
                        .where(or_(*conditions))
                        .limit(3)
                    )
                    
                    result = await session.execute(query)
                    products = result.scalars().all()
                    
                    logger.info(f"[GuideService] Product Query Result: {len(products)} products found")
                    
                    if products:
                        settings = get_museum_settings()
                        base_url = settings.image_base_url
                        
                        result = []
                        for p in products:
                            img_url = None
                            if p.image_urls:
                                raw_url = p.image_urls[0]
                                if raw_url and raw_url.startswith("/static"):
                                    img_url = f"{base_url}{raw_url}"
                                else:
                                    img_url = raw_url
                            result.append(ProductRecommendItem(
                                id=p.id,
                                name=p.name,
                                price=float(p.price),
                                image_url=img_url,
                                relation="相关文创商品"
                            ))
                        return result, matched_artifact_names
                    else:
                        # 调试：检查所有商品的关联ID
                        all_prods = (
                            await session.execute(
                                select(MuseumProduct).where(
                                    MuseumProduct.workspace_id == workspace
                                )
                            )
                        ).scalars().all()
                        for p in all_prods[:5]:
                            logger.debug(f"[GuideService] Product '{p.name}' related_ids: {p.related_exhibit_ids}")
                
                # 4. Fallback: 如果 Bridge 没命中，或者命中但没商品
                # 二级兜底：随机获取任意 active 商品（移除 related_exhibit_ids 限制）
                logger.info(f"[GuideService] Bridge Miss or No Products for IDs {bridge_ids}, trying secondary fallback...")
                
                try:
                    # 二级兜底：随机获取任意 active 商品
                    fallback_query = (
                        select(MuseumProduct)
                        .where(
                            MuseumProduct.workspace_id == workspace,
                            MuseumProduct.status == "active",
                        )
                        .order_by(func.rand())
                        .limit(3)
                    )
                    fallback_result = await session.execute(fallback_query)
                    fallback_products = fallback_result.scalars().all()
                    
                    if fallback_products:
                        logger.info(f"[GuideService] Secondary Fallback: found {len(fallback_products)} random products")
                        settings = get_museum_settings()
                        base_url = settings.image_base_url
                        
                        result = []
                        for p in fallback_products:
                            img_url = None
                            if p.image_urls:
                                raw_url = p.image_urls[0]
                                if raw_url and raw_url.startswith("/static"):
                                    img_url = f"{base_url}{raw_url}"
                                else:
                                    img_url = raw_url
                            result.append(ProductRecommendItem(
                                id=p.id,
                                name=p.name,
                                price=float(p.price),
                                image_url=img_url,
                                relation="热门文创"
                            ))
                        return result, set()  # 兜底无精准匹配
                except Exception as fallback_err:
                    logger.warning(f"[GuideService] Secondary fallback also failed: {fallback_err}")
                
                return [], set()
                
        except Exception as e:
            logger.warning(f"[GuideService] 获取相关商品失败: {e}")
            return [], set()

    
    def _build_context(
        self,
        query: str,
        search_results: list,
        adjustment: AdjustmentResult,
        related_products: List[ProductRecommendItem],
        image_candidates: List[dict] | None = None,
    ) -> str:
        """构建完整的生成上下文（含候选图片列表注入）
        
        Args:
            search_results: DocumentChunk 对象列表
            image_candidates: 候选图片列表 [{"id": "...", "name": "文物名"}, ...]
        """
        from app.models.common.execution import DocumentChunk
        
        # 检索结果
        def get_content(r) -> str:
            if isinstance(r, DocumentChunk):
                return r.content[:500] if r.content else ""
            elif isinstance(r, dict):
                return r.get('content', '')[:500]
            return ""
        
        search_context = "\n".join([
            f"- {get_content(r)}"
            for r in search_results[:5]
        ])
        
        # 候选图片列表（Context 注入策略核心，使用配置模板）
        image_list_context = ""
        if image_candidates:
            image_lines = "\n".join([f"  - `[[IMAGE:{c['id']}]]` → {c['name']}" for c in image_candidates[:8]])
            image_list_context = self.prompt_settings.image_list_template.format(image_lines=image_lines)
        
        # 商品信息
        product_context = ""
        if related_products:
            related_items = [p for p in related_products if p.relation == "相关文创商品"]
            recommended_items = [p for p in related_products if p.relation != "相关文创商品"]
            
            lines = []
            if related_items:
                lines.append("### 与当前文物相关的文创")
                for p in related_items:
                    lines.append(f"- {p.name} (¥{p.price})")
            
            if recommended_items:
                lines.append("### 热门推荐（非当前文物专属）")
                for p in recommended_items:
                    lines.append(f"- {p.name} (¥{p.price})")
            
            product_context = f"\n## 文创商品\n{chr(10).join(lines)}\n"
        
        # 使用配置模板构建最终上下文
        return self.prompt_settings.guide_context_template.format(
            adjusted_prompt=adjustment.adjusted_prompt,
            search_context=search_context,
            image_list_context=image_list_context,
            product_context=product_context,
            query=query
        )
    
    async def _generate_response(
        self,
        context: str,
        emitter: SSEEmitter,
        enable_tts: bool,
        person_type: str,
        history_messages: list[dict] | None = None,
        session_id: str = "",
        image_candidates: list[dict] | None = None,
    ) -> str:
        """
        生成导览回答 (流式 + TTS + 图片解析)
        
        图片策略：Context 注入 + 流式解析
        - LLM 根据候选列表输出 [[IMAGE:xxx]] 标签
        - 后端流式捕获标签后立即 emit SEARCH_IMAGES
        """
        sentence_buffer = SentenceBuffer()
        full_response = ""
        has_shown_image = False # 是否已经展示过图片
        
        try:
            # 从配置读取模型和提示词
            model = self.settings.guide_llm_model
            system_prompt = self.settings.guide_system_prompt
            
            # 使用 openai_async 异步流式调用 (兼容 dashscope)
            import os
            import openai
            
            # 优先使用 DASHSCOPE_API_KEY，兼容 OPENAI_API_KEY
            api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("请设置 DASHSCOPE_API_KEY 或 OPENAI_API_KEY 环境变量")
            
            # 从配置获取 base_url，遵循配置外置原则
            base_url = self.settings.llm_base_url
            
            client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=base_url
            )
            
            # 构建消息列表
            messages = [{"role": "system", "content": system_prompt}]
            if history_messages:
                messages.extend(history_messages)
            messages.append({"role": "user", "content": context})
            
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
            )
            
            # TTS 队列模式
            tts_queue: asyncio.Queue[tuple[str, bool] | None] = asyncio.Queue()
            tts_worker_task: asyncio.Task | None = None
            tts_complete_event = asyncio.Event()
            
            # 情感记忆：从会话缓存加载上次情感，实现跨请求语音连贯性
            cached_emotion = await self.message_history.get_emotion(session_id) if session_id else None
            emotion_memory = {"current": cached_emotion or get_default_emotion(person_type)}
            
            async def tts_worker():
                try:
                    while True:
                        item = await tts_queue.get()
                        if item is None:
                            break
                        sentence, is_final = item
                        await self._emit_tts_chunk_pooled(
                            emitter, sentence, person_type, session_id, is_final,
                            emotion_memory=emotion_memory
                        )
                        tts_queue.task_done()
                    tts_queue.task_done()
                finally:
                    tts_complete_event.set()
            
            if enable_tts and session_id:
                tts_worker_task = asyncio.create_task(tts_worker())
            
            # 构建候选图片索引（用于流式匹配）
            image_index = {}
            if image_candidates:
                for c in image_candidates:
                    image_index[c['id']] = c
            
            display_buffer = ""
            
            # 图片标签缓冲区 (Robust Cross-Chunk Parsing)
            # 可能情况: [[IMAGE:xxx]] 分散在多个 chunk，如 [[IMA + GE:123]]
            tag_buffer = "" 
            
            # 标签可能的起始字符序列 (用于检测 partial tag)
            TAG_PREFIX = "[[IMAGE:"
            TAG_SUFFIX = "]]"
            
            def find_partial_tag_start(text: str) -> int:
                """
                查找可能是标签起始的位置（处理跨chunk分割）
                
                返回值：
                - >= 0: 找到潜在的 partial tag 起始位置
                - -1: 没有 partial tag
                """
                # 检查是否有完整的 '[[' 但没有闭合
                if "[[" in text and "]]" not in text[text.rfind("[["):]:
                    return text.rfind("[[")
                
                # 检查末尾是否是 TAG_PREFIX 的部分前缀
                # 例如: 文本以 "[" 或 "[[" 或 "[[I" 或 "[[IM" 等结尾
                for i in range(min(len(TAG_PREFIX), len(text)), 0, -1):
                    suffix = text[-i:]
                    if TAG_PREFIX.startswith(suffix):
                        return len(text) - i
                
                return -1
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    
                    # === 1. 图片标签解析逻辑 (Robust State Machine) ===
                    # 将内容追加到临时 buffer 进行检测
                    tag_buffer += content
                    
                    # 查找并处理所有完整标签
                    while True:
                        img_match = re.search(r'\[\[IMAGE:(.*?)\]\]', tag_buffer)
                        if img_match:
                            # 找到标签
                            full_tag = img_match.group(0)
                            image_id = img_match.group(1).strip()
                            
                            logger.info(f"[GuideService] 流式捕获图片标签: {image_id}")
                            
                            # 从候选索引中精确匹配
                            target_image = image_index.get(image_id)
                            
                            if target_image:
                                # 构造前端期望的数据结构
                                img_data = {
                                    "fileId": target_image.get("fileId", ""),
                                    "imageId": target_image.get("imageId", ""),
                                    "caption": target_image.get("name", ""),
                                }
                                await emitter.emit("SEARCH_IMAGES", {"images": [img_data]})
                                has_shown_image = True
                                logger.info(f"[GuideService] 发送图片: name={target_image.get('name')}, fileId={img_data['fileId']}, imageId={img_data['imageId']}")
                            else:
                                # 候选列表中没有，尝试解析路径构造
                                if "/" in image_id:
                                        
                                    # 构造符合前端期望的数据结构 (BUG1 修复)
                                    # 前端期望: { fileId, imageId, caption }
                                    # 从 image_id 路径中提取 fileId 和 imageId
                                    path_parts = image_id.replace("\\", "/").split("/")
                                    if len(path_parts) >= 2:
                                        file_id_part = path_parts[-2]
                                        image_id_part = path_parts[-1]
                                    else:
                                        file_id_part = path_parts[0] if path_parts else "unknown"
                                        image_id_part = path_parts[-1] if path_parts else "unknown"
                                    
                                    manual_img = {
                                        "fileId": file_id_part,
                                        "imageId": image_id_part,
                                        "caption": "相关文物",
                                        "score": 1.0
                                    }
                                    await emitter.emit("SEARCH_IMAGES", {"images": [manual_img]})
                                    has_shown_image = True

                            # 从 tag_buffer 中移除该标签
                            tag_buffer = tag_buffer.replace(full_tag, "", 1)
                        else:
                            break
                    
                    # 检查是否还有残留的 partial tag (例如 "[[IM" 或仅 "[")
                    # 使用 find_partial_tag_start 精确检测跨 chunk 分割的情况
                    
                    text_to_process = ""
                    partial_start = find_partial_tag_start(tag_buffer)
                    
                    if partial_start >= 0:
                        # 找到可能的 partial tag，只处理它之前的文本
                        text_to_process = tag_buffer[:partial_start]
                        tag_buffer = tag_buffer[partial_start:]  # 保留 partial tag 等待后续 chunk
                    else:
                        # 没有 partial tag，全部作为文本处理
                        text_to_process = tag_buffer
                        tag_buffer = ""
                    
                    if not text_to_process:
                        continue

                    # === 2. TTS 处理 ===
                    if enable_tts and session_id:
                        sentences = sentence_buffer.feed(text_to_process)
                        for sentence in sentences:
                            await tts_queue.put((sentence, False))
                    
                    # === 3. 展示文本处理 ===
                    display_buffer += text_to_process
                    
                    # 清洗 Emotion 标签 (且要确保不破坏未闭合的 emotion 标签)
                    # 此时 text_to_process 已经不包含 IMAGE 标签了
                    
                    # 检查是否有未闭合的标签（如 "[frie" 等待 "ndly]"）
                    clean_text = re.sub(r'\[[a-zA-Z_]+\]', '', display_buffer)
                    last_bracket = display_buffer.rfind('[')
                    if last_bracket != -1:
                        after_bracket = display_buffer[last_bracket:]
                        if ']' not in after_bracket:
                            safe_part = display_buffer[:last_bracket]
                            clean_text = re.sub(r'\[[a-zA-Z_]+\]', '', safe_part)
                            if clean_text:
                                await emitter.emit("MESSAGE_CHUNK", {"content": clean_text})
                                full_response += clean_text
                            display_buffer = display_buffer[last_bracket:]
                            continue
                    
                    if clean_text:
                        await emitter.emit("MESSAGE_CHUNK", {"content": clean_text})
                        full_response += clean_text
                    display_buffer = ""
            
            # 循环结束，处理残留
            if tag_buffer:
                # 剩下的 tag_buffer 可能是无法解析的乱码或截断，作为文本处理
                display_buffer += tag_buffer
            
            if display_buffer:
                clean_remaining = re.sub(r'\[[a-zA-Z_]+\]', '', display_buffer)
                if clean_remaining:
                    await emitter.emit("MESSAGE_CHUNK", {"content": clean_remaining})
                    full_response += clean_remaining
            
            # 文本结束
            await emitter.emit("MESSAGE_END", {"full_text": full_response})
            
            # === 图片兜底逻辑 ===
            if not has_shown_image and image_candidates:
                # LLM 没引用任何图片，使用第一个候选作为兜底
                top_candidate = image_candidates[0]
                img_data = {
                    "fileId": top_candidate.get("fileId", ""),
                    "imageId": top_candidate.get("imageId", ""),
                    "caption": top_candidate.get("name", ""),
                }
                logger.info(f"[GuideService] LLM 未引用图片，兜底展示: {top_candidate.get('name')}")
                await emitter.emit("SEARCH_IMAGES", {"images": [img_data]})
            
            # TTS 结束处理
            if enable_tts and session_id:
                remaining = sentence_buffer.flush()
                if remaining:
                    await tts_queue.put((remaining, True))
                else:
                    await tts_queue.put(("", True))
                await tts_queue.put(None)
                await tts_queue.join()
                if tts_worker_task:
                    await tts_complete_event.wait()
                    await tts_worker_task
                
                # 持久化情感记忆：保存本次生成结束时的情感状态
                if emotion_memory.get("current"):
                    await self.message_history.save_emotion(session_id, emotion_memory["current"])
            
        except Exception as e:
            logger.error(f"[GuideService] LLM 生成失败: {e}")
            full_response = f"抱歉，我暂时无法为您提供导览服务。请稍后再试。错误: {str(e)}"
            await emitter.emit("MESSAGE_END", {"full_text": full_response, "error": True})
        
        return full_response
    
    async def _emit_tts_chunk_pooled(
        self, 
        emitter: SSEEmitter, 
        text: str, 
        person_type: str,
        session_id: str,
        is_final: bool = False,
        emotion_memory: dict | None = None
    ):
        """
        推送 TTS 音频块（使用连接池 + 情感解析 + 情感记忆）
        
        优化点：
        1. 连接复用：同一会话内复用 WebSocket 连接
        2. 情感解析：解析 [excited] 等标记，传递 speaking_style
        3. 情感记忆：无标签时复用上一句的情感，保持语调连贯
        4. Session Context：保持语流上下文，语调更自然
        """
        # 空文本时只发送 is_final 标记
        if not text.strip():
            if is_final:
                await emitter.emit("TTS_CHUNK", {
                    "text": "",
                    "format": "pcm",
                    "audio_base64": "",
                    "is_final": True,
                })
            return
        
        try:
            settings = get_museum_settings()
            speaker = settings.get_voice_for_person(person_type)
            
            # 情感记忆：使用上一句的情感作为默认值
            remembered_emotion = emotion_memory.get("current") if emotion_memory else None
            default_emotion = remembered_emotion or get_default_emotion(person_type)
            
            # 解析情感标记
            segments = parse_emotion_tags(text, default_emotion)
            
            # 更新情感记忆：如果本句有新标签，记住它
            if emotion_memory and segments:
                last_emotion = segments[-1].emotion
                if last_emotion:
                    emotion_memory["current"] = last_emotion
            
            # 获取连接池中的长连接
            tts_pool = get_tts_connection_pool()
            conn = await tts_pool.get_connection(session_id, speaker)
            
            # 逐段合成（每段可能有不同情感）
            logger.debug("[GuideService] TTS 开始合成: 共%d段, 原文=%s", len(segments), text[:50])
            for segment in segments:
                clean_text = strip_emotion_tags(segment.text)
                if not clean_text.strip():
                    continue
                
                logger.debug("[GuideService] TTS 合成段: emotion=%s, text=%s", 
                           segment.emotion, clean_text[:50])
                
                # 使用长连接流式合成
                async for audio_data in conn.synthesize_streaming(
                    clean_text, 
                    speaking_style=segment.speaking_style
                ):
                    audio_base64 = base64.b64encode(audio_data).decode("utf-8")
                    await emitter.emit("TTS_CHUNK", {
                        "text": clean_text,
                        "format": "pcm",
                        "audio_base64": audio_base64,
                        "is_final": False,
                    })
            
            # 发送最终标记
            if is_final:
                await emitter.emit("TTS_CHUNK", {
                    "text": "",
                    "format": "pcm",
                    "audio_base64": "",
                    "is_final": True,
                })
                
        except Exception as e:
            logger.warning(f"[GuideService] TTS 合成失败: {e}")
            await emitter.emit("TTS_CHUNK", {
                "text": strip_emotion_tags(text),
                "format": "pcm",
                "audio_base64": "",
                "is_final": is_final,
            })


# ========== 单例工厂 ==========

_guide_service: Optional[GuideService] = None


def get_guide_service() -> GuideService:
    """获取导览服务单例"""
    global _guide_service
    if _guide_service is None:
        _guide_service = GuideService()
    return _guide_service
