"""
DeluData 智能问数系统 - DocSkill 知识库检索技能 (v2.5 组件化重构版)

职责（门面层）：
1. 对外提供统一的知识库操作接口
2. 权限校验与安全控制
3. 委托具体实现给解耦的子模块

架构：
    DocSkill (门面) → DocumentIngestor (入库编排)
                    → HybridRetriever (混合检索)
                    → Reranker (重排序)
                    → ContextExpander (上下文扩展)

组件化模块：
- document_parser.py: 文档解析
- document_ingestor.py: 文档入库
- image_linker.py: 图片关联
"""
from typing import Any, Callable, Dict, List, Optional
from pathlib import Path
from threading import Event
import logging
import re

import chromadb
from sqlalchemy import select

from app.skills.base import SecureSkill
from app.models.common.context import UserContext
from app.models.common.execution import DocumentChunk
from app.core.db.database import get_async_db_context
from app.core.storage.service import get_storage_service
from app.core.utils.storage_path import resolve_storage_path
from langchain_core.messages import BaseMessage
from app.config import get_settings
from app.api.events import emit_step_update, emit_thinking_log

# 导入解耦模块
from app.core.rag.base_retriever import BaseRetriever
from app.core.rag.retriever_factory import get_retriever
from app.core.rag.query_rewriter import QueryRewriter, get_query_rewriter
from app.core.rag.hybrid_retriever import get_hybrid_retriever
from app.core.rag.reranker import Reranker, get_reranker
from app.core.rag.context_expander import ContextExpander, get_context_expander
from app.core.rag.document_ingestor import DocumentIngestor, get_document_ingestor
from app.core.rag.tokenization import tokenize_mixed_text
from app.models.knowledge.graph import File
from app.core.security.data_scope import (
    can_access_metadata,
    normalize_visibility,
    resolve_scope_dept_ids,
)

logger = logging.getLogger(__name__)


class DocSkill(SecureSkill):
    """
    知识库检索技能（v2.5 门面层）
    
    将具体实现委托给：
    - DocumentIngestor: 文档入库（解析、切分、存储）
    - HybridRetriever: 知识库检索
    - Reranker: 重排序
    - ContextExpander: 上下文扩展
    
    特性：
    - 向后兼容：保持所有原有公共 API
    - 依赖注入：支持测试时传入 mock
    - 权限前置：继承 SecureSkill 进行安全校验
    """
    
    COLLECTION_NAME = "tenant_docs"
    
    def __init__(
        self,
        chroma_client: Optional[chromadb.ClientAPI] = None,
        ingestor: Optional[DocumentIngestor] = None,
        retriever: Optional[BaseRetriever] = None,
        reranker: Optional[Reranker] = None,
        context_expander: Optional[ContextExpander] = None,
        query_rewriter: Optional[QueryRewriter] = None
    ):
        """
        初始化 DocSkill
        
        Args:
            chroma_client: ChromaDB 客户端（可选，默认使用持久化客户端）
            ingestor: 文档入库服务（可选，默认使用单例）
            retriever: 混合检索器（可选，默认使用单例）
            reranker: 重排序器（可选，默认使用单例）
            context_expander: 上下文扩展器（可选，默认使用单例）
            query_rewriter: 查询改写器（可选，默认使用单例）
        """
        super().__init__(name="DocSkill")
        
        settings = get_settings()
        
        if chroma_client is None:
            persist_path = Path(settings.chroma.persist_dir).resolve()
            persist_path.mkdir(parents=True, exist_ok=True)
            chroma_client = chromadb.PersistentClient(path=str(persist_path))
        
        self.client = chroma_client
        self._collection = None
        
        # 依赖注入 - 支持测试时传入 mock
        self._ingestor = ingestor
        self._query_rewriter = query_rewriter
        self._retriever = retriever
        self._reranker = reranker
        self._context_expander = context_expander
    
    # ========== 属性（懒加载） ==========
    
    @property
    def collection(self):
        """懒加载 Collection"""
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"description": "User uploaded documents for RAG"}
            )
        return self._collection
    
    @property
    def ingestor(self) -> DocumentIngestor:
        if self._ingestor is None:
            self._ingestor = get_document_ingestor(self.client)
        return self._ingestor
    
    @property
    def query_rewriter(self) -> QueryRewriter:
        if self._query_rewriter is None:
            self._query_rewriter = get_query_rewriter()
        return self._query_rewriter
    
    @property
    def retriever(self) -> BaseRetriever:
        if self._retriever is None:
            self._retriever = get_hybrid_retriever(self.client)
        return self._retriever
    
    @property
    def reranker(self) -> Reranker:
        if self._reranker is None:
            self._reranker = get_reranker()
        return self._reranker
    
    @property
    def context_expander(self) -> ContextExpander:
        if self._context_expander is None:
            self._context_expander = get_context_expander(self.client)
        return self._context_expander
    
    # ========== 公共接口（保持向后兼容） ==========
    
    async def execute(
        self,
        query: str,
        user_context: UserContext,
        top_k: int = 5,
        session_id: str = "",
        parent_step_id: str = ""
    ) -> List[DocumentChunk]:
        """
        执行文档检索（BaseSkill 接口实现）
        
        Args:
            query: 检索查询
            user_context: 用户上下文
            top_k: 返回结果数量
            session_id: 会话ID (用于SSE推送)
            parent_step_id: 父任务步骤ID
            
        Returns:
            相关文档切片列表
        """
        return await self.query_knowledge_base(
            query, user_context, top_k, session_id, parent_step_id
        )
    
    async def query_knowledge_base(
        self,
        query: str,
        user_context: UserContext,
        top_k: int = 5,
        session_id: str = "",
        parent_step_id: str = "",
        original_query: Optional[str] = None,
        messages: List[BaseMessage] = [],
        include_images: bool = True,
        file_ids: Optional[List[str]] = None,
        visibilities: Optional[List[str]] = None,
        dept_ids: Optional[List[str]] = None,
        deep_search: bool = False,
        preserve_related_candidates: bool = False,
    ) -> List[DocumentChunk]:
        """
        查询知识库（优化版）
        
        漏斗型筛选：Query Rewrite → Hybrid Search → Rerank → Expand
        
        Args:
            query: 检索查询
            user_context: 用户上下文
            top_k: 返回结果数量
            session_id: 会话ID (用于SSE推送)
            parent_step_id: 父任务步骤ID
            original_query: 原始查询（用于改写参考）
            messages: 消息历史
        """
        if not self.validate_user_context(user_context):
            return []
        
        try:
            # 1. 查询改写
            if session_id:
                await emit_step_update(
                    session_id, "doc_rewrite", "running", 
                    "正在理解您的问题...", parent_step_id=parent_step_id
                )
            
            queries = await self.query_rewriter.rewrite(
                query, 
                original_query=original_query, 
                messages=messages,
                num_variants=3
            )
            
            if session_id:
                await emit_step_update(
                    session_id, "doc_rewrite", "completed", 
                    "已理解问题核心", parent_step_id=parent_step_id
                )
            
            # 2. 混合检索 (Top-N)
            if session_id:
                await emit_step_update(
                    session_id, "doc_retrieval", "running", 
                    "正在翻阅知识库...", parent_step_id=parent_step_id
                )
                
            settings = get_settings()
            merged_top_n = settings.rag.merged_top_n

            mode = "enhanced" if deep_search else "fast"
            retriever = self._retriever or get_retriever(mode, self.client)

            if deep_search and session_id:
                await emit_thinking_log(session_id, "正在分析文档结构并执行深度检索...")

            # 文件名/完整术语精确命中不是“零召回兜底”，而是稳定候选源。
            # 每次检索都执行，避免宽泛产品词先召回其他文件后掩盖实体文件名命中。
            exact_hits = await self._exact_term_fallback_search(
                query=query,
                original_query=original_query,
                user_context=user_context,
                top_k=top_k,
                include_images=include_images,
                file_ids=file_ids,
                visibilities=visibilities,
                dept_ids=dept_ids,
            )

            try:
                candidates = await retriever.search(
                    queries=queries,
                    user_context=user_context,
                    top_n=merged_top_n,
                    include_images=include_images,
                    file_ids=file_ids,
                    visibilities=visibilities,
                    dept_ids=dept_ids,
                    session_id=session_id,
                )
            except Exception as exc:
                if not exact_hits:
                    raise
                logger.warning(
                    "[DocSkill] semantic retrieval failed; using exact filename hits: %s",
                    exc,
                )
                candidates = []
            
            if not candidates:
                if exact_hits:
                    if session_id:
                        await emit_step_update(
                            session_id,
                            "doc_retrieval",
                            "completed",
                            f"通过术语精确匹配找到 {len(exact_hits)} 条资料",
                            parent_step_id=parent_step_id,
                        )
                    return exact_hits
                if session_id:
                    await emit_step_update(
                        session_id, "doc_retrieval", "completed", 
                        "未找到相关资料", parent_step_id=parent_step_id
                    )
                return []
            
            if session_id:
                source_files = self._collect_source_files(candidates)
                file_names = [f["name"] for f in source_files[:3]]
                detail_msg = f"检索到 {', '.join(file_names)} 等 {len(candidates)} 个相关片段"
                await emit_step_update(
                    session_id, "doc_retrieval", "completed", detail_msg, 
                    parent_step_id=parent_step_id,
                    metadata={"source_files": source_files}
                )
            
            reranked = candidates
            if not deep_search:
                # 3. 重排序 (Top-K)
                if session_id:
                    await emit_step_update(
                        session_id, "doc_rerank", "running", 
                        "正在筛选最相关的内容...", parent_step_id=parent_step_id
                    )
                    
                reranked = await self.reranker.rerank(query, candidates, top_k)

                # 分数过滤 - 从配置读取阈值
                score_threshold = settings.rag.rerank_score_threshold
                original_count = len(reranked)
                reranked = [
                    doc for doc in reranked 
                    if (doc.metadata or {}).get("rerank_fallback")
                    or (getattr(doc, 'rerank_score', 0) or 0) >= score_threshold
                ]
                filtered_count = original_count - len(reranked)

                if session_id:
                    msg = f"筛选出 {len(reranked)} 条核心内容"
                    if filtered_count > 0:
                        msg += f" (已过滤 {filtered_count} 条低置信度内容)"
                    await emit_step_update(
                        session_id, "doc_rerank", "completed", msg, 
                        parent_step_id=parent_step_id
                    )
            elif session_id:
                await emit_step_update(
                    session_id, "doc_rerank", "completed",
                    f"深度检索已返回 {min(len(reranked), top_k)} 条结构化结果",
                    parent_step_id=parent_step_id
                )

            if exact_hits:
                merged: list[DocumentChunk] = []
                seen_chunk_ids: set[str] = set()
                exact_file_ids = {
                    str((getattr(chunk, "metadata", {}) or {}).get("file_id") or "")
                    for chunk in exact_hits
                    if (getattr(chunk, "metadata", {}) or {}).get("exact_file_name_match")
                }
                exact_file_ids.discard("")
                semantic_candidates = reranked
                if exact_file_ids and not preserve_related_candidates:
                    semantic_candidates = [
                        chunk
                        for chunk in reranked
                        if str((getattr(chunk, "metadata", {}) or {}).get("file_id") or "")
                        in exact_file_ids
                    ]
                for chunk in [*exact_hits, *semantic_candidates]:
                    chunk_id = str(
                        getattr(chunk, "chunk_id", "")
                        or (getattr(chunk, "metadata", {}) or {}).get("chunk_id")
                        or ""
                    )
                    if chunk_id and chunk_id in seen_chunk_ids:
                        continue
                    if chunk_id:
                        seen_chunk_ids.add(chunk_id)
                    merged.append(chunk)
                reranked = merged[: max(1, top_k)]
            if deep_search:
                reranked = reranked[:top_k]
            
            # 4. 上下文扩展（deep_search 跳过，避免二次膨胀）
            if (not deep_search) and settings.rag.enable_context_expansion:
                if session_id:
                    await emit_step_update(
                        session_id, "doc_expand", "running", 
                        "正在补充上下文信息...", parent_step_id=parent_step_id
                    )
                    
                expanded = await self.context_expander.expand(reranked)
                
                if session_id:
                    await emit_step_update(
                        session_id, "doc_expand", "completed", 
                        "内容补充完成", parent_step_id=parent_step_id
                    )
                
            # 图片引用
                if include_images:
                    expanded = await self._process_image_references(
                        expanded, settings.rag.image_score_threshold
                    )
                return expanded
            elif deep_search and session_id:
                await emit_step_update(
                    session_id, "doc_expand", "completed",
                    "深度检索模式已跳过上下文扩展",
                    parent_step_id=parent_step_id
                )
            
            # 图片引用
            if include_images:
                reranked = await self._process_image_references(
                    reranked, settings.rag.image_score_threshold
                )
            return reranked
            
        except Exception as e:
            self.log_error(e, "query_knowledge_base")
            return []

    @staticmethod
    def _build_exact_fallback_terms(query: str, original_query: Optional[str] = None) -> list[str]:
        text = f"{original_query or ''} {query or ''}".strip()
        if not text:
            return []

        terms: list[str] = []
        compound_found = False
        generated_short_terms: set[str] = set()

        def add_term_variants(value: str) -> None:
            value = str(value or "").strip()
            if not value:
                return
            candidates = [value]
            for marker in ("关于", "查询", "检索", "请问", "介绍", "说明"):
                if marker in value:
                    tail = value.split(marker)[-1].strip()
                    if tail and tail != value:
                        candidates.append(tail)
            for candidate in candidates:
                for stop in ("是什么", "什么是", "的", "功效", "作用", "适用人群", "适宜人群", "成本", "价格", "配方", "主治", "功能"):
                    if stop in candidate:
                        candidate = candidate.split(stop)[0].strip()
                        break
                if candidate:
                    terms.append(candidate)

        for term in re.findall(r"[A-Za-z][A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+", text):
            compound_found = True
            terms.extend([
                term,
                re.sub(r"[-_]+", " ", term),
                re.sub(r"[-_]+", "", term),
            ])

        for match in re.findall(r"\b([A-Za-z][A-Za-z0-9]{2,})\s+([A-Za-z][A-Za-z0-9]{2,})\b", text):
            compound_found = True
            spaced = " ".join(match)
            hyphenated = "-".join(match)
            compact = "".join(match)
            terms.extend([spaced, hyphenated, compact])

        compact_query = re.sub(r"\s+", "", text)

        # 通用中文问句兜底：保留主题短语，并由分词结果生成相邻复合词。
        # 例如“新建需求单方式有哪些”会生成“新建需求单方式”和“需求单”。
        question_prefixes = ("请问", "请帮我", "帮我", "查询", "检索", "介绍", "说明")
        question_suffixes = (
            "有哪几种",
            "有哪些",
            "是什么",
            "怎么做",
            "如何操作",
            "如何",
            "吗",
            "呢",
        )
        stop_tokens = {"请", "帮", "我", "有", "哪些", "什么", "怎么", "如何", "吗", "呢"}
        alias_pairs = get_settings().rag.repair_synonym_pairs or {}

        for raw_question in (original_query, query):
            core = re.sub(r"[\s,，。；;！？!?]+", "", str(raw_question or ""))
            if not core:
                continue
            for prefix in question_prefixes:
                if core.startswith(prefix):
                    core = core[len(prefix):]
                    break
            for suffix in question_suffixes:
                if core.endswith(suffix):
                    core = core[:-len(suffix)]
                    break
            if len(core) >= 4:
                compound_found = True
                terms.append(core)

            tokens = [
                token
                for token in tokenize_mixed_text(core, use_jieba=True)
                if token and token not in stop_tokens
            ]
            for window_size in (2, 3):
                for start in range(0, max(0, len(tokens) - window_size + 1)):
                    window_tokens = tokens[start:start + window_size]
                    if len(window_tokens[0]) < 2:
                        continue
                    phrase = "".join(window_tokens).strip()
                    if len(phrase) < 3:
                        continue
                    terms.append(phrase)
                    if len(phrase) < 4:
                        generated_short_terms.add(phrase)
                    compound_found = True

            for source, target in alias_pairs.items():
                source = str(source or "").strip()
                target = str(target or "").strip()
                if not source or not target or source not in core:
                    continue
                terms.append(core.replace(source, target))
                for action in ("新建", "创建", "填写", "进入"):
                    if action in core:
                        terms.append(f"{action}{target}")

        for term in re.findall(r"[A-Za-z][A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+[\u4e00-\u9fff]{2,20}", compact_query):
            compound_found = True
            terms.append(term)

        dosage_suffixes = "颗粒|含片|口服液|胶囊|片剂|丸剂|丸|粉剂|粉|饮品|饮|茶|膏|合剂|散剂|冲剂"
        for term in re.findall(rf"[\u4e00-\u9fffA-Za-z0-9]{{2,40}}?(?:{dosage_suffixes})", compact_query):
            compound_found = True
            add_term_variants(term)

        info_keywords = "功效|作用|适用人群|适宜人群|成本|价格|配方|主治|功能"
        for term in re.findall(rf"([\u4e00-\u9fffA-Za-z0-9]{{4,40}}?)(?:的)?(?:{info_keywords})", compact_query):
            compound_found = True
            add_term_variants(term)

        technical_terms = [
            "点焊",
            "焊接",
            "受力",
            "合理受力",
            "受拉力矩",
            "翻倒力矩",
            "剪切",
            "应力",
            "失效",
            "连接",
            "螺栓",
            "螺母",
            "力矩",
            "扭矩",
        ]
        matched_technical_terms = [term for term in technical_terms if term in compact_query]
        if matched_technical_terms:
            compound_found = True
            terms.extend(matched_technical_terms)
            if "点焊" in compact_query and "连接" in compact_query:
                terms.append("点焊连接")
            if "连接" in compact_query and "受力" in compact_query:
                terms.append("连接受力")
            if "合理" in compact_query and "受力" in compact_query:
                terms.append("合理受力")
            for start in range(0, len(matched_technical_terms)):
                phrase = "".join(matched_technical_terms[start : start + 3])
                if len(phrase) >= 4:
                    terms.append(phrase)

        maintenance_terms = [
            "压片机",
            "设备",
            "操作面板",
            "设备操作面板",
            "操作显示屏",
            "操作控制台",
            "清扫",
            "清洁",
            "保全",
            "保养",
            "基准",
            "清扫基准",
            "清洁基准",
            "润滑",
            "给油",
            "点检",
            "周期",
            "担当",
            "所需时间",
        ]
        matched_maintenance_terms = [term for term in maintenance_terms if term in compact_query]
        if matched_maintenance_terms:
            compound_found = True
            terms.extend(matched_maintenance_terms)
            if "清扫" in compact_query and "基准" in compact_query:
                terms.append("清扫基准")
            if "清洁" in compact_query and "基准" in compact_query:
                terms.append("清洁基准")

            panel_terms = ["设备操作面板", "操作面板", "操作显示屏", "操作控制台"]
            matched_panel_terms = [term for term in panel_terms if term in compact_query]
            for panel_term in matched_panel_terms:
                terms.append(panel_term)
                if "清扫" in compact_query:
                    terms.append(f"{panel_term}清扫")
                if "清洁" in compact_query:
                    terms.append(f"{panel_term}清洁")
                if "基准" in compact_query:
                    terms.append(f"{panel_term}基准")
                if "清扫" in compact_query and "基准" in compact_query:
                    terms.append(f"{panel_term}清扫基准")
                if "清洁" in compact_query and "基准" in compact_query:
                    terms.append(f"{panel_term}清洁基准")

            equipment_terms = ["压片机", "设备"]
            matched_equipment_terms = [term for term in equipment_terms if term in compact_query]
            for equipment_term in matched_equipment_terms:
                if matched_panel_terms:
                    for panel_term in matched_panel_terms:
                        terms.append(f"{equipment_term}{panel_term}")
                if "清扫" in compact_query:
                    terms.append(f"{equipment_term}清扫")
                if "保全" in compact_query or "保养" in compact_query:
                    terms.append(f"{equipment_term}保全")
                    terms.append(f"{equipment_term}保养")

        if not compound_found:
            for term in re.findall(r"[A-Za-z][A-Za-z0-9]{4,}", text):
                terms.append(term)

        exact_short_terms = set(technical_terms) | set(maintenance_terms) | generated_short_terms
        deduped = [term for term in dict.fromkeys(t.strip() for t in terms) if term.strip()]
        return [
            term
            for term in deduped
            if len(term) >= 4 or term in exact_short_terms
        ]

    @staticmethod
    def _normalize_exact_text(value: str) -> str:
        return re.sub(r"[\s\-_]+", "", str(value or "").lower())

    async def _load_matching_file_names(
        self,
        workspace_id: str,
        normalized_terms: list[tuple[str, str]],
    ) -> dict[str, str]:
        """Return file_id -> display filename for files whose names match exact terms."""
        if not workspace_id or not normalized_terms:
            return {}
        try:
            async with get_async_db_context() as session:
                stmt = select(File.id, File.name).where(
                    File.workspace_id == workspace_id,
                    File.is_deleted.is_(False),
                )
                result = await session.execute(stmt)
                matches: dict[str, str] = {}
                for file_id, name in result.all():
                    display_name = str(name or "")
                    display_name_norm = self._normalize_exact_text(display_name)
                    if any(
                        term.lower() in display_name.lower() or term_norm in display_name_norm
                        for term, term_norm in normalized_terms
                    ):
                        matches[str(file_id)] = display_name
                return matches
        except Exception as exc:
            logger.warning("[DocSkill] exact fallback file-name scan failed: %s", exc)
            return {}

    async def load_file_chunks_by_ids(
        self,
        *,
        file_ids: List[str],
        user_context: UserContext,
        query: str = "",
        max_chunks_per_file: int = 3,
        include_images: bool = True,
        visibilities: Optional[List[str]] = None,
        dept_ids: Optional[List[str]] = None,
    ) -> List[DocumentChunk]:
        """Load evidence directly from explicit files when semantic retrieval misses."""
        required_file_ids = [str(file_id) for file_id in file_ids if str(file_id)]
        if not required_file_ids:
            return []

        workspace_id = str(getattr(user_context, "workspace_id", "") or "default")
        try:
            scope_dept_ids = await resolve_scope_dept_ids(user_context)
        except Exception:
            scope_dept_ids = []
        allowed_visibilities = {
            normalize_visibility(value)
            for value in (visibilities or [])
            if str(value)
        }
        allowed_dept_ids = {str(value) for value in (dept_ids or []) if str(value)}
        query_terms = [
            self._normalize_exact_text(term)
            for term in self._build_exact_fallback_terms(query, query)
        ]
        query_terms = [term for term in query_terms if term]

        try:
            raw = self.collection.get(
                where={
                    "$and": [
                        {"workspace_id": {"$eq": workspace_id}},
                        {"file_id": {"$in": required_file_ids}},
                    ]
                },
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            logger.warning("[DocSkill] required-file collection scan failed: %s", exc)
            return []

        documents = raw.get("documents") or []
        metadatas = raw.get("metadatas") or []
        chunk_ids = raw.get("ids") or []
        grouped: dict[str, list[DocumentChunk]] = {
            file_id: [] for file_id in required_file_ids
        }
        for index, content in enumerate(documents):
            metadata = (
                metadatas[index]
                if index < len(metadatas) and isinstance(metadatas[index], dict)
                else {}
            )
            file_id = str(metadata.get("file_id") or "")
            if file_id not in grouped:
                continue
            if not include_images and metadata.get("type") == "image_summary":
                continue
            if (
                allowed_visibilities
                and normalize_visibility(metadata.get("visibility"))
                not in allowed_visibilities
            ):
                continue
            if allowed_dept_ids and str(metadata.get("dept_id") or "") not in allowed_dept_ids:
                continue
            if not can_access_metadata(metadata, user_context, scope_dept_ids):
                continue

            searchable = self._normalize_exact_text(
                " ".join(
                    [
                        str(content or ""),
                        str(metadata.get("source_file") or ""),
                        str(metadata.get("header_path") or ""),
                        str(metadata.get("summary") or ""),
                    ]
                )
            )
            matched_term_count = sum(
                1 for term in query_terms if term and term in searchable
            )
            score = min(0.93, 0.84 + 0.02 * matched_term_count)
            chunk_metadata = dict(metadata)
            chunk_metadata["required_file_fallback"] = True
            chunk_id = str(
                chunk_ids[index]
                if index < len(chunk_ids)
                else metadata.get("chunk_id") or f"required_{index}"
            )
            grouped[file_id].append(
                DocumentChunk(
                    content=str(content or ""),
                    source_file=str(metadata.get("source_file") or ""),
                    chunk_id=chunk_id,
                    score=score,
                    rerank_score=score,
                    metadata=chunk_metadata,
                    parent_id=metadata.get("parent_id"),
                    prev_id=metadata.get("prev_id"),
                    next_id=metadata.get("next_id"),
                    summary=metadata.get("summary"),
                    header_path=metadata.get("header_path"),
                )
            )

        selected: list[DocumentChunk] = []
        per_file_limit = max(1, int(max_chunks_per_file))
        for file_id in required_file_ids:
            file_chunks = sorted(
                grouped[file_id],
                key=lambda chunk: getattr(chunk, "rerank_score", 0) or 0,
                reverse=True,
            )
            selected.extend(file_chunks[:per_file_limit])
        return selected

    async def _exact_term_fallback_search(
        self,
        *,
        query: str,
        original_query: Optional[str],
        user_context: UserContext,
        top_k: int,
        include_images: bool,
        file_ids: Optional[List[str]],
        visibilities: Optional[List[str]],
        dept_ids: Optional[List[str]],
    ) -> List[DocumentChunk]:
        """Deterministic fallback for literal product terms such as Supervisor-Worker."""
        terms = self._build_exact_fallback_terms(query, original_query)
        if not terms:
            return []

        workspace_id = str(getattr(user_context, "workspace_id", "") or "default")
        try:
            scope_dept_ids = await resolve_scope_dept_ids(user_context)
        except Exception:
            scope_dept_ids = []

        allowed_file_ids = {str(fid) for fid in (file_ids or []) if str(fid)}
        allowed_visibilities = {normalize_visibility(v) for v in (visibilities or []) if str(v)}
        allowed_dept_ids = {str(d) for d in (dept_ids or []) if str(d)}
        normalized_terms = [(term, self._normalize_exact_text(term)) for term in terms]
        normalized_terms = [(term, norm) for term, norm in normalized_terms if norm]
        matching_file_names = await self._load_matching_file_names(workspace_id, normalized_terms)

        try:
            raw = self.collection.get(
                where={"workspace_id": {"$eq": workspace_id}},
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            logger.warning("[DocSkill] exact fallback collection scan failed: %s", exc)
            return []

        documents = raw.get("documents") or []
        metadatas = raw.get("metadatas") or []
        ids = raw.get("ids") or []
        chunks: list[DocumentChunk] = []
        seen_ids: set[str] = set()

        for idx, content in enumerate(documents):
            metadata = metadatas[idx] if idx < len(metadatas) and isinstance(metadatas[idx], dict) else {}
            chunk_id = str(ids[idx] if idx < len(ids) else metadata.get("chunk_id") or f"exact_{idx}")
            if chunk_id in seen_ids:
                continue
            if not include_images and metadata.get("type") == "image_summary":
                continue
            if allowed_file_ids and str(metadata.get("file_id") or "") not in allowed_file_ids:
                continue
            if allowed_visibilities and normalize_visibility(metadata.get("visibility")) not in allowed_visibilities:
                continue
            if allowed_dept_ids and str(metadata.get("dept_id") or "") not in allowed_dept_ids:
                continue
            if not can_access_metadata(metadata, user_context, scope_dept_ids):
                continue

            searchable = " ".join([
                str(content or ""),
                str(matching_file_names.get(str(metadata.get("file_id") or ""), "")),
                str(metadata.get("source_file") or ""),
                str(metadata.get("header_path") or ""),
                str(metadata.get("summary") or ""),
            ])
            searchable_norm = self._normalize_exact_text(searchable)
            matched_terms = [
                term
                for term, term_norm in normalized_terms
                if term.lower() in searchable.lower() or term_norm in searchable_norm
            ]
            file_name_matched = str(metadata.get("file_id") or "") in matching_file_names
            if matching_file_names and not file_name_matched:
                continue
            if not matched_terms and not file_name_matched:
                continue

            content_text = str(content or "")
            has_answer_keyword = any(
                keyword in content_text
                for keyword in (
                    "功效",
                    "作用",
                    "适用",
                    "人群",
                    "成本",
                    "价格",
                    "配方",
                    "主治",
                    "功能",
                    "基准",
                    "方法",
                    "路径",
                    "步骤",
                    "工具",
                    "所需时间",
                    "周期",
                    "担当",
                )
            )
            strong_term_count = sum(
                1
                for term in matched_terms
                if len(term) >= 4 or term in {"操作面板", "清扫基准", "清洁基准", "所需时间"}
            )
            maintenance_target_count = sum(
                1
                for term in matched_terms
                if term
                in {
                    "设备操作面板",
                    "操作面板",
                    "设备操作面板清扫基准",
                    "操作面板清扫基准",
                }
            )
            has_enumerated_answer = bool(
                re.search(r"(?:路径|步骤|方法)[一二三四五六七八九十\d]", content_text)
            )
            score = min(
                0.99,
                0.78
                + 0.02 * min(5, len(matched_terms))
                + 0.015 * min(4, strong_term_count)
                + 0.02 * min(3, maintenance_target_count)
                + (0.12 if has_enumerated_answer else 0.0),
            )
            if file_name_matched:
                score = max(score, 0.94 if has_answer_keyword else 0.88)
            meta = dict(metadata)
            meta["exact_fallback_terms"] = matched_terms[:5]
            meta["exact_file_name_match"] = file_name_matched
            if matching_file_names.get(str(metadata.get("file_id") or "")):
                meta["display_file_name"] = matching_file_names[str(metadata.get("file_id") or "")]
            chunks.append(
                DocumentChunk(
                    content=str(content or ""),
                    source_file=str(metadata.get("source_file") or ""),
                    chunk_id=chunk_id,
                    score=score,
                    rerank_score=score,
                    metadata=meta,
                    parent_id=metadata.get("parent_id"),
                    prev_id=metadata.get("prev_id"),
                    next_id=metadata.get("next_id"),
                    summary=metadata.get("summary"),
                    header_path=metadata.get("header_path"),
                )
            )
            seen_ids.add(chunk_id)

        if chunks:
            chunks.sort(key=lambda item: getattr(item, "rerank_score", 0) or 0, reverse=True)
            chunks = chunks[: max(1, top_k)]
            logger.info(
                "[DocSkill] exact fallback matched %d chunks for terms=%s",
                len(chunks),
                terms[:8],
            )
        return chunks
    
    async def wiki_navigate(
        self,
        query: str,
        user_context: UserContext,
        max_pages: Optional[int] = None,
        per_page_max_chars: Optional[int] = None,
        session_id: str = "",
        parent_step_id: str = "",
        domains: Optional[List[str]] = None,
        wiki_slugs: Optional[List[str]] = None,
        file_ids: Optional[List[str]] = None,
        visibilities: Optional[List[str]] = None,
        dept_ids: Optional[List[str]] = None,
        business_domain: Optional[str] = None,
        document_type: Optional[str] = None,
        confidentiality_level: Optional[str] = None,
    ) -> List[DocumentChunk]:
        """
        Wiki-First 导航式装载（M3.2）

        与 query_knowledge_base 平行：直接从预编译的 wiki_pages + 链接构造 INDEX，
        交给 LLM 选页后整页装载，最终输出与 query_knowledge_base 同形（List[DocumentChunk]），
        通过 metadata.kind="wiki_page" 让下游识别。

        Args:
            query: 用户原始问题
            user_context: 用户上下文（取 workspace_id）
            max_pages: 最多装载页数（默认 settings.wiki.index_load_top_k）
            per_page_max_chars: 单页正文截断（默认 3000）
            session_id, parent_step_id: 用于 SSE 进度推送

        Returns:
            DocumentChunk 列表；INDEX 为空 / LLM 失败 / 异常时返回 []。
        """
        if not self.validate_user_context(user_context):
            return []

        try:
            from app.core.wiki.navigator import get_wiki_navigator

            if session_id:
                await emit_step_update(
                    session_id, "wiki_navigate", "running",
                    "正在浏览 Wiki 索引...",
                    parent_step_id=parent_step_id,
                )

            navigator = get_wiki_navigator()
            chunks = await navigator.navigate(
                workspace_id=user_context.workspace_id,
                query=query,
                max_pages=max_pages,
                per_page_max_chars=per_page_max_chars,
                domains=domains,
                wiki_slugs=wiki_slugs,
                file_ids=file_ids,
                visibilities=visibilities,
                dept_ids=dept_ids,
                business_domain=business_domain,
                document_type=document_type,
                confidentiality_level=confidentiality_level,
                user_context=user_context,
            )

            if session_id:
                if chunks:
                    msg = f"已装载 {len(chunks)} 个 Wiki 实体页"
                else:
                    msg = "未匹配到相关 Wiki 实体页"
                await emit_step_update(
                    session_id, "wiki_navigate", "completed",
                    msg, parent_step_id=parent_step_id,
                )
            return chunks
        except Exception as e:
            self.log_error(e, "wiki_navigate")
            return []

    async def ingest_document(
        self,
        file_path: str,
        user_context: UserContext,
        file_id: Optional[str] = None,
        visibility: str = "dept",
        allowed_users: Optional[List[str]] = None,
        target_dept_id: Optional[str] = None,
        cancel_event: Optional[Event] = None,
        progress_callback: Optional[Callable[[str, int, Optional[dict[str, Any]]], Any]] = None,
        notify_bm25: bool = True,
    ) -> Dict[str, Any]:
        """
        解析并存储文档（委托给 DocumentIngestor）
        
        Args:
            file_path: 文档路径
            user_context: 用户上下文
            file_id: 可选的文件 ID
            visibility: 可见性 - public | dept | private
            allowed_users: 额外允许访问的用户ID列表
            target_dept_id: 管理员可指定归属部门
            cancel_event: 可选取消信号（用于中止长时任务）
            
        Returns:
            入库结果
        """
        if not self.validate_user_context(user_context):
            return {"success": False, "error": "无效的用户上下文"}
        
        storage_service = get_storage_service()
        suffix = Path(file_path).suffix or Path(resolve_storage_path(file_path)).suffix
        async with storage_service.materialize(file_path, suffix=suffix) as local_file_path:
            resolved_file_path = resolve_storage_path(local_file_path)
            file_path_obj = Path(resolved_file_path)
            if not file_path_obj.exists():
                return {"success": False, "error": "文件不存在"}

            result = await self.ingestor.ingest(
                file_path=resolved_file_path,
                user_context=user_context,
                file_id=file_id,
                visibility=visibility,
                allowed_users=allowed_users,
                target_dept_id=target_dept_id,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )
        
        # 使 BM25 缓存失效
        if result.get("success") and notify_bm25:
            await self._notify_bm25_content_change_safe(user_context.workspace_id)
        
        return result
    
    async def delete_document(
        self,
        file_id: str,
        user_context: UserContext,
        raise_on_error: bool = False,
    ) -> bool:
        """
        删除文档
        
        Args:
            file_id: 文件 ID
            user_context: 用户上下文
            raise_on_error: 删除异常时是否抛出（默认 False，保持兼容）
            
        Returns:
            是否删除成功
        """
        import asyncio
        
        try:
            await asyncio.to_thread(
                self.collection.delete,
                where={
                    "$and": [
                        {"file_id": {"$eq": file_id}},
                        {"workspace_id": {"$eq": user_context.workspace_id}}
                    ]
                }
            )
            await self._notify_bm25_content_change_safe(
                user_context.workspace_id,
                disable_sparse=True,
            )
            return True
        except Exception as e:
            self.log_error(e, "delete_document")
            if raise_on_error:
                raise
            return False
    
    async def list_documents(
        self,
        user_context: UserContext
    ) -> List[Dict[str, str]]:
        """
        列出用户的所有文档
        
        Args:
            user_context: 用户上下文
            
        Returns:
            文档列表
        """
        try:
            results = self.collection.get(
                where={"workspace_id": {"$eq": user_context.workspace_id}},
                include=["metadatas"]
            )
            
            # 按文件 ID 去重
            files = {}
            for metadata in results.get("metadatas", []):
                file_id = metadata.get("file_id", "")
                if file_id and file_id not in files:
                    files[file_id] = {
                        "file_id": file_id,
                        "file_name": metadata.get("source_file", "")
                    }
            
            return list(files.values())
            
        except Exception as e:
            self.log_error(e, "list_documents")
            return []
    
    # ========== 私有辅助方法 ==========
    
    def _collect_source_files(self, candidates: List[DocumentChunk]) -> List[Dict]:
        """收集检索结果中的源文件信息"""
        source_files_dict = {}
        for c in candidates:
            file_id = c.metadata.get('file_id')
            if file_id and file_id not in source_files_dict:
                source_files_dict[file_id] = {
                    "id": file_id,
                    "name": c.metadata.get('source_file', '未知文件'),
                    "type": c.metadata.get('file_type', 'unknown')
                }
        return list(source_files_dict.values())

    async def _notify_bm25_content_change_safe(
        self,
        workspace_id: str,
        *,
        disable_sparse: bool = False,
    ) -> None:
        retriever = self.retriever
        try:
            if hasattr(retriever, "notify_bm25_content_change"):
                await retriever.notify_bm25_content_change(
                    workspace_id,
                    disable_sparse=disable_sparse,
                )
            elif hasattr(retriever, "invalidate_bm25_cache"):
                retriever.invalidate_bm25_cache(workspace_id)
        except Exception as exc:
            try:
                if hasattr(retriever, "invalidate_bm25_cache"):
                    retriever.invalidate_bm25_cache(workspace_id)
            except Exception:
                pass
            logger.warning(
                "BM25 lifecycle notify failed workspace=%s disable_sparse=%s error=%s",
                workspace_id,
                disable_sparse,
                exc,
            )
    
    async def _process_image_references(
        self, 
        chunks: List[DocumentChunk], 
        score_threshold: float
    ) -> List[DocumentChunk]:
        """
        处理图片引用
        
        为检索结果注入 [[IMAGE:...]] 引用标记
        """
        # 注入图片引用
        settings = get_settings()
        max_images_per_chunk = settings.rag.max_images_per_chunk

        def _safe_positive_int(raw: Any) -> Optional[int]:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None

        def _chunk_primary_page(metadata: Dict[str, Any]) -> Optional[int]:
            direct = _safe_positive_int(metadata.get("page_number"))
            if direct is not None:
                return direct

            raw_pages = metadata.get("page_numbers")
            if isinstance(raw_pages, list):
                candidates = raw_pages
            else:
                candidates = [raw_pages]
            import re
            for candidate in candidates:
                for match in re.findall(r"\d+", str(candidate or "")):
                    page = _safe_positive_int(match)
                    if page is not None:
                        return page
            return None

        # 预加载 text chunk 可能引用到的图片页码（权威来源：document_images）
        lookup_file_ids: set[str] = set()
        lookup_image_ids: set[str] = set()
        for chunk in chunks:
            metadata = chunk.metadata or {}
            if metadata.get("type", "text") != "text":
                continue
            file_id = str(metadata.get("file_id") or "").strip()
            raw_related = metadata.get("related_image_ids")
            if not file_id or not isinstance(raw_related, str) or not raw_related:
                continue
            for image_id in [i.strip() for i in raw_related.split(",") if i.strip()]:
                lookup_file_ids.add(file_id)
                lookup_image_ids.add(image_id)

        image_page_map: Dict[tuple[str, str], int] = {}
        if lookup_file_ids and lookup_image_ids:
            try:
                from sqlalchemy import select

                from app.core.db.database import get_async_db_manager
                from app.models.knowledge.graph import DocumentImage

                db_manager = get_async_db_manager()
                async with db_manager.session_scope() as session:
                    stmt = (
                        select(DocumentImage.file_id, DocumentImage.image_id, DocumentImage.page_number)
                        .where(DocumentImage.file_id.in_(list(lookup_file_ids)))
                        .where(DocumentImage.image_id.in_(list(lookup_image_ids)))
                    )
                    result = await session.execute(stmt)
                    for row in result:
                        try:
                            page = int(row.page_number or 1)
                        except (TypeError, ValueError):
                            page = 1
                        image_page_map[(str(row.file_id), str(row.image_id))] = max(1, page)
            except Exception as e:
                err_text = str(e)
                if "Unknown column 'document_images.image_id'" in err_text:
                    self.logger.warning(
                        "document_images 缺少 image_id 列，已回退 chunk 页码；请执行迁移: "
                        "python -m migrations.run_document_images_migration"
                    )
                else:
                    self.logger.warning("预加载图片页码失败，将回退 chunk 页码: %s", e)
        
        for chunk in chunks:
            chunk.metadata = chunk.metadata or {}
            chunk_type = chunk.metadata.get("type", "text")
            file_id = chunk.metadata.get("file_id", "")
            if not file_id:
                continue
            
            if chunk_type == "image_summary":
                score = getattr(chunk, 'rerank_score', 0) or 0
                if score >= score_threshold:
                    image_id = chunk.metadata.get("image_id", "")
                    tag = settings.rag.image_ref_tag
                    image_ref = f"[[{tag}:{file_id}:{image_id}:]]"
                    chunk.content = f"{image_ref}\n{chunk.content}"
                    page_number = _safe_positive_int(chunk.metadata.get("page_number"))
                    if page_number is not None:
                        chunk.metadata["image_page_number"] = page_number
                    
            elif chunk_type == "text":
                raw_related = chunk.metadata.get("related_image_ids")
                if not isinstance(raw_related, str) or not raw_related:
                    continue
                
                refs = []
                page_pairs: List[str] = []
                id_list = [i.strip() for i in raw_related.split(",") if i.strip()]
                chunk_page = _chunk_primary_page(chunk.metadata)
                
                for img_id in id_list[:max_images_per_chunk]:
                    tag = settings.rag.image_ref_tag
                    refs.append(f"[[{tag}:{file_id}:{img_id}:]]")
                    page_number = image_page_map.get((str(file_id), str(img_id)))
                    if page_number is None:
                        page_number = chunk_page
                    if page_number is not None:
                        page_pairs.append(f"{img_id}:{page_number}")
                
                if refs:
                    chunk.content = f"{chunk.content}\n\n{' '.join(refs)}"
                    if page_pairs:
                        chunk.metadata["related_image_pages"] = ",".join(page_pairs)
        
        return chunks


# ========== 单例工厂函数 ==========

_doc_skill_instance = None


def get_doc_skill() -> DocSkill:
    """
    获取 DocSkill 单例
    
    Returns:
        DocSkill 实例
    """
    global _doc_skill_instance
    if _doc_skill_instance is None:
        _doc_skill_instance = DocSkill()
    return _doc_skill_instance
