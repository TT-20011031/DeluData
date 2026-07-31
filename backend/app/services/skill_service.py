"""
Skill 服务层

职责：
1. Skill CRUD 操作
2. 向量化存储（独立 ChromaDB Collection）
3. 语义检索 + 阈值判断

设计原则遵循：
- Async First: 所有 IO 操作使用 async/await，Chroma 同步调用使用 to_thread
- Schema Validation: 使用 Pydantic 模型校验
- No Hardcoding: 阈值等配置从 settings 读取
- Design Rigor: 解耦的 Service 层设计
"""
import uuid
import asyncio
import logging
from typing import Optional, List, Tuple

from sqlalchemy import select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.database import get_async_db_context
from app.core.llm.async_embedding import get_async_embedding
from app.models.config.skill import Skill, SkillVisibility
from app.models.config.skill_schemas import (
    SkillCreateRequest,
    SkillUpdateRequest,
    SkillResponse,
    SkillSummaryResponse,
    SkillSearchResult,
    SkillStepSchema,
)
from app.models.common.context import UserContext
from app.config import get_settings

logger = logging.getLogger(__name__)


class SkillService:
    """
    Skill 操作手册服务
    
    提供 Skill 的 CRUD、向量化、检索功能
    """
    
    # 独立 Collection 名称（与知识库分离）
    COLLECTION_NAME = "skills_embeddings"
    
    def __init__(self):
        self._chroma_client = None
        self._embedding_client = None
        self._settings = get_settings()
    
    @property
    def score_threshold(self) -> float:
        """从配置获取基础相似度阈值"""
        return self._settings.skill.score_threshold
    
    @property
    def auto_use_threshold(self) -> float:
        """从配置获取自动使用阈值"""
        return self._settings.skill.auto_use_threshold
    
    @property
    def chroma_client(self):
        """懒加载 ChromaDB 客户端"""
        if not self._chroma_client:
            import chromadb
            self._chroma_client = chromadb.PersistentClient(
                path=self._settings.skill.chroma_persist_directory
            )
        return self._chroma_client
    
    @property
    def collection(self):
        """获取或创建 Skills 专用 Collection"""
        return self.chroma_client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={
                "hnsw:space": "cosine",  # 明确使用 Cosine 距离
                "description": "Skills embeddings - independent from knowledge base"
            }
        )
    
    @property
    def embedding_client(self):
        """懒加载 Embedding 客户端"""
        if not self._embedding_client:
            self._embedding_client = get_async_embedding()
        return self._embedding_client
    
    # ========== CRUD 操作 ==========
    
    async def create_skill(
        self,
        request: SkillCreateRequest,
        user_context: UserContext
    ) -> Skill:
        """
        创建 Skill
        
        Args:
            request: 创建请求
            user_context: 用户上下文
            
        Returns:
            创建的 Skill 实例
        """
        skill_id = str(uuid.uuid4())
        skill_data = None
        
        async with get_async_db_context() as session:
            skill = Skill(
                id=skill_id,
                workspace_id=user_context.workspace_id,
                title=request.title,
                description=request.description,
                steps=[s.model_dump() for s in request.steps],
                tags=request.tags or [],
                example_queries=request.example_queries or [],
                visibility=SkillVisibility(request.visibility.value),
                created_by=user_context.user_id
            )
            session.add(skill)
            await session.commit()
            await session.refresh(skill)
            
            # 保存一份用于返回（避免 session 关闭后无法访问）
            skill_data = self._skill_to_dict(skill)
        
        # 向量化存储（在事务外执行，包含错误处理）
        try:
            await self._embed_skill_by_data(skill_id, skill_data)
            logger.info(f"创建 Skill: {request.title} (id={skill_id})")
        except Exception as e:
            # 双写失败：记录错误，但不回滚 DB（最终一致性方案）
            logger.error(f"Skill 向量化失败，需要后台修复: {skill_id}, error={e}")
        
        # 重新加载完整对象
        return await self.get_skill(skill_id, user_context)
    
    async def get_skill(
        self,
        skill_id: str,
        user_context: Optional[UserContext] = None
    ) -> Optional[Skill]:
        """
        获取 Skill 详情
        
        Args:
            skill_id: Skill ID
            user_context: 用户上下文（用于权限检查，可选）
            
        Returns:
            Skill 实例或 None
        """
        async with get_async_db_context() as session:
            result = await session.execute(
                select(Skill).where(Skill.id == skill_id)
            )
            skill = result.scalar_one_or_none()
            
            if skill and user_context:
                # 权限检查：仅当前工作区可见
                if skill.workspace_id != user_context.workspace_id:
                    return None
            
            return skill
    
    async def list_skills(
        self,
        user_context: UserContext,
        include_global: bool = True,
        limit: int = 50,
        offset: int = 0
    ) -> List[Skill]:
        """
        列出用户可见的 Skills（支持分页）
        
        Args:
            user_context: 用户上下文
            include_global: 是否包含全局 Skills
            limit: 分页大小
            offset: 分页偏移
            
        Returns:
            Skill 列表
        """
        async with get_async_db_context() as session:
            visibility_filter = (
                or_(Skill.visibility == SkillVisibility.WORKSPACE, Skill.visibility == SkillVisibility.GLOBAL)
                if include_global
                else Skill.visibility == SkillVisibility.WORKSPACE
            )
            
            stmt = select(Skill).where(
                Skill.workspace_id == user_context.workspace_id,
                visibility_filter
            ).order_by(
                Skill.usage_count.desc(), 
                Skill.created_at.desc()
            ).limit(limit).offset(offset)
            
            result = await session.execute(stmt)
            return list(result.scalars().all())
    
    async def update_skill(
        self,
        skill_id: str,
        request: SkillUpdateRequest,
        user_context: UserContext,
        is_admin: bool = False
    ) -> Optional[Skill]:
        """
        更新 Skill
        
        Args:
            skill_id: Skill ID
            request: 更新请求
            user_context: 用户上下文
            is_admin: 是否为管理员（管理员可修改任意 Skill）
            
        Returns:
            更新后的 Skill 或 None
        """
        async with get_async_db_context() as session:
            skill = await session.get(Skill, skill_id)
            if not skill:
                return None
            
            # 权限检查：管理员可修改任意 Skill，普通用户只能修改自己创建的
            if not is_admin and skill.created_by != user_context.user_id:
                raise PermissionError("无权修改此 Skill")
            
            # 部分更新
            update_data = request.model_dump(exclude_unset=True)
            if 'steps' in update_data and update_data['steps']:
                update_data['steps'] = [
                    s.model_dump() if hasattr(s, 'model_dump') else s
                    for s in update_data['steps']
                ]
            if 'visibility' in update_data:
                update_data['visibility'] = SkillVisibility(update_data['visibility'].value)
            
            for key, value in update_data.items():
                setattr(skill, key, value)
            
            await session.commit()
            await session.refresh(skill)
            
            skill_data = self._skill_to_dict(skill)
        
        # 重新向量化（包含错误处理）
        try:
            await self._embed_skill_by_data(skill_id, skill_data)
            logger.info(f"更新 Skill: {skill_id}")
        except Exception as e:
            logger.error(f"Skill 重新向量化失败: {skill_id}, error={e}")
        
        return await self.get_skill(skill_id, user_context)
    
    async def delete_skill(
        self,
        skill_id: str,
        user_context: UserContext,
        is_admin: bool = False
    ) -> bool:
        """
        删除 Skill
        
        Args:
            skill_id: Skill ID
            user_context: 用户上下文
            is_admin: 是否为管理员（管理员可删除任意 Skill）
            
        Returns:
            是否删除成功
        """
        async with get_async_db_context() as session:
            skill = await session.get(Skill, skill_id)
            if not skill:
                return False
            
            # 权限检查：管理员可删除任意 Skill，普通用户只能删除自己创建的
            if not is_admin and skill.created_by != user_context.user_id:
                raise PermissionError("无权删除此 Skill")
            
            await session.delete(skill)
            await session.commit()
        
        # 从向量库删除（使用 to_thread 包装同步调用）
        try:
            await asyncio.to_thread(self.collection.delete, ids=[skill_id])
            logger.info(f"删除 Skill: {skill_id}")
        except Exception as e:
            logger.warning(f"从向量库删除 Skill 失败: {e}")
        
        return True
    
    async def increment_usage(self, skill_id: str):
        """
        增加使用次数
        
        Args:
            skill_id: Skill ID
        """
        async with get_async_db_context() as session:
            stmt = update(Skill).where(Skill.id == skill_id).values(
                usage_count=Skill.usage_count + 1
            )
            await session.execute(stmt)
            await session.commit()
    
    async def reindex_all_skills(self, workspace_id: Optional[str] = None) -> dict:
        """
        重新索引所有 Skills 到 ChromaDB
        
        用于修复 ChromaDB 与数据库不同步的问题
        
        Returns:
            {"total": N, "success": M, "failed": F}
        """
        total = 0
        success = 0
        failed = 0
        
        # 1. 获取所有 Skills
        async with get_async_db_context() as session:
            stmt = select(Skill)
            if workspace_id:
                stmt = stmt.where(Skill.workspace_id == workspace_id)
            result = await session.execute(stmt)
            skills = result.scalars().all()
            total = len(skills)
            
            for skill in skills:
                try:
                    skill_data = self._skill_to_dict(skill)
                    await self._embed_skill_by_data(skill.id, skill_data)
                    success += 1
                    logger.info(f"[Reindex] 成功索引 Skill: {skill.title}")
                except Exception as e:
                    failed += 1
                    logger.error(f"[Reindex] 索引失败: {skill.title}, error={e}")
        
        logger.info(f"[Reindex] 完成: total={total}, success={success}, failed={failed}")
        return {"total": total, "success": success, "failed": failed}
    
    # ========== 向量化 ==========
    
    def _skill_to_dict(self, skill: Skill) -> dict:
        """将 Skill ORM 对象转换为字典"""
        return {
            "id": skill.id,
            "title": skill.title,
            "description": skill.description,
            "tags": skill.tags or [],
            "example_queries": skill.example_queries or [],
            "workspace_id": skill.workspace_id or "",
            "visibility": skill.visibility.value if skill.visibility else "workspace"
        }
    
    async def _embed_skill_by_data(self, skill_id: str, skill_data: dict):
        """
        将 Skill 向量化并存储到 ChromaDB
        
        Args:
            skill_id: Skill ID
            skill_data: Skill 数据字典
        """
        # 拼接用于 Embedding 的文本：标题 + 描述 + 标签 + 示例问题
        parts = [
            skill_data["title"],
            skill_data["description"],
            " ".join(skill_data.get("tags", [])),
            " ".join(skill_data.get("example_queries", []))
        ]
        embed_text = "\n".join(filter(None, parts))
        
        # 生成嵌入向量
        embedding = await self.embedding_client.embed_single(embed_text)
        
        # 存储到 ChromaDB（使用 to_thread 包装同步调用，避免阻塞事件循环）
        await asyncio.to_thread(
            self.collection.upsert,
            ids=[skill_id],
            embeddings=[embedding],
            metadatas=[{
                "title": skill_data["title"],
                "workspace_id": skill_data.get("workspace_id", ""),
                "visibility": skill_data.get("visibility", "workspace")
            }],
            documents=[embed_text]
        )
    
    # ========== 检索 ==========
    
    async def search_skills(
        self,
        query: str,
        user_context: UserContext,
        top_k: int = 3
    ) -> Tuple[List[SkillSearchResult], str]:
        """
        语义检索 Skills
        
        Args:
            query: 用户查询
            user_context: 用户上下文
            top_k: 返回数量
            
        Returns:
            (检索结果列表, 决策: "auto" | "confirm" | "none")
        """
        # 1. 生成查询嵌入
        logger.info(f"[SkillService.search] Step 1: 生成查询嵌入, query={query[:30]}...")
        query_embedding = await self.embedding_client.embed_single(query)
        logger.info(f"[SkillService.search] Step 1 完成: embedding_dim={len(query_embedding)}")
        
        # 2. 构建权限过滤（全局 + 当前工作区）
        where_filter = {"workspace_id": {"$eq": user_context.workspace_id}}
        logger.info(f"[SkillService.search] where_filter: workspace_id={user_context.workspace_id}")
        
        # 3. 执行检索（使用 to_thread 包装同步调用）
        logger.info("[SkillService.search] Step 2: 执行 ChromaDB 检索...")
        try:
            # [DEBUG] 先不带过滤查询，看是否有任何 Skill
            debug_results = await asyncio.to_thread(
                self.collection.query,
                query_embeddings=[query_embedding],
                n_results=top_k,
                include=["distances", "metadatas"]
            )
            logger.info(f"[SkillService.search] DEBUG 无过滤查询: ids={debug_results.get('ids', [[]])[0]}, metadatas={debug_results.get('metadatas', [[]])[0]}")
            
            results = await asyncio.to_thread(
                self.collection.query,
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where_filter,
                include=["distances", "metadatas"]
            )
            logger.info(f"[SkillService.search] Step 2 完成: ids={results.get('ids', [[]])[0] if results else 'empty'}")
        except Exception as e:
            logger.warning(f"Skill 检索失败: {e}")
            return [], "none"
        
        if not results.get("ids") or not results["ids"][0]:
            logger.info("[SkillService.search] 未找到匹配 Skill")
            return [], "none"
        
        # 4. 转换分数并过滤
        skill_ids = results["ids"][0]
        distances = results.get("distances", [[]])[0]
        
        # 从数据库加载完整 Skill（添加多租户二次校验）
        async with get_async_db_context() as session:
            # 纵深防御：即使 Chroma 返回了 ID，也要在 DB 层再次校验 workspace 权限
            stmt = select(Skill).where(
                Skill.id.in_(skill_ids),
                Skill.workspace_id == user_context.workspace_id
            )
            db_result = await session.execute(stmt)
            skills_map = {s.id: s for s in db_result.scalars().all()}
        
        # 5. 计算相似度并过滤
        logger.info(f"[SkillService.search] Step 3: 从 DB 加载, skill_ids={skill_ids}, skills_map_size={len(skills_map)}, threshold={self.score_threshold}")
        search_results = []
        for i, skill_id in enumerate(skill_ids):
            if skill_id not in skills_map:
                logger.warning(f"[SkillService.search] Skill {skill_id} 不在 skills_map 中（DB 权限过滤）")
                continue
            
            # Cosine 距离直接转换: score = 1 - distance
            distance = distances[i] if i < len(distances) else 1.0
            score = max(0, min(1, 1 - distance))
            logger.info(f"[SkillService.search] Skill {skill_id}: distance={distance:.4f}, score={score:.4f}, threshold={self.score_threshold}")
            
            if score >= self.score_threshold:
                skill = skills_map[skill_id]
                search_results.append(SkillSearchResult(
                    skill=SkillResponse(
                        id=skill.id,
                        title=skill.title,
                        description=skill.description,
                        steps=[SkillStepSchema(**s) for s in skill.steps],
                        tags=skill.tags or [],
                        example_queries=skill.example_queries or [],
                        visibility=skill.visibility.value,
                        usage_count=skill.usage_count,
                        created_by=skill.created_by,
                        created_at=skill.created_at,
                        updated_at=skill.updated_at
                    ),
                    score=score
                ))
            else:
                logger.info(f"[SkillService.search] Skill {skill_id} score={score:.4f} 低于阈值 {self.score_threshold}")
        
        # 6. 决策逻辑
        if not search_results:
            return [], "none"
        
        # 单个高置信结果 → 自动使用
        if len(search_results) == 1 and search_results[0].score >= self.auto_use_threshold:
            return search_results, "auto"
        
        # 多个结果 → 需要用户确认
        return search_results, "confirm"
    
    def format_skill_for_prompt(self, skill: Skill) -> str:
        """
        格式化 Skill 为 Prompt 上下文
        
        Args:
            skill: Skill 实例
            
        Returns:
            格式化的文本
        """
        lines = [
            f"### {skill.title}",
            f"描述: {skill.description}",
            "",
            "步骤:"
        ]
        
        def _format_doc_scope(scope: dict) -> str:
            if not scope:
                return ""
            file_ids = scope.get("file_ids") or []
            folder_ids = scope.get("folder_ids") or []
            include_sub = scope.get("include_subfolders")
            parts = []
            if folder_ids:
                parts.append(f"文件夹: {len(folder_ids)}")
            if file_ids:
                parts.append(f"文件: {len(file_ids)}")
            if include_sub is not None:
                parts.append(f"含子目录: {'是' if include_sub else '否'}")
            return "，".join(parts)

        for step in skill.steps:
            # 支持 SkillStepSchema 对象和 dict 两种格式
            if hasattr(step, 'step'):
                # Pydantic 对象
                lines.append(f"  {step.step}. {step.action}")
                if step.tool:
                    lines.append(f"     工具: {step.tool}")
                if step.template:
                    lines.append(f"     模板: {step.template}")
                if getattr(step, "template_id", None):
                    lines.append(f"     模板ID: {step.template_id}")
                if getattr(step, "output_filename", None):
                    lines.append(f"     输出文件: {step.output_filename}")
                if getattr(step, "doc_scope", None):
                    scope_text = _format_doc_scope(step.doc_scope or {})
                    if scope_text:
                        lines.append(f"     文档范围: {scope_text}")
                if step.keywords:
                    lines.append(f"     关键词: {', '.join(step.keywords)}")
            else:
                # dict 格式
                lines.append(f"  {step['step']}. {step['action']}")
                if step.get('tool'):
                    lines.append(f"     工具: {step['tool']}")
                if step.get('template'):
                    lines.append(f"     模板: {step['template']}")
                if step.get('template_id'):
                    lines.append(f"     模板ID: {step.get('template_id')}")
                if step.get('output_filename'):
                    lines.append(f"     输出文件: {step.get('output_filename')}")
                if step.get('doc_scope'):
                    scope_text = _format_doc_scope(step.get('doc_scope') or {})
                    if scope_text:
                        lines.append(f"     文档范围: {scope_text}")
                if step.get('keywords'):
                    lines.append(f"     关键词: {', '.join(step['keywords'])}")
        
        # [Fix] 添加参数填写指引，确保 LLM 将模板ID等放入 params
        lines.append("")
        lines.append("⚠️ 重要：上述步骤中的「模板ID」「输出文件」等参数，必须填入对应步骤的 params 字段（如 params.template_id），不要只写在 description 里。")
        
        return "\n".join(lines)


# ========== 单例 ==========

_skill_service: Optional[SkillService] = None


def get_skill_service() -> SkillService:
    """获取 SkillService 单例"""
    global _skill_service
    if not _skill_service:
        _skill_service = SkillService()
    return _skill_service
