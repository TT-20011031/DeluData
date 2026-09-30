"""
SQL 示例配置模型

存储用户配置的问题描述与示例 SQL，用于辅助 Text-to-SQL 生成
使用 MySQL 持久化存储
"""
import logging
from datetime import datetime
from typing import Any, Optional, List
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Column, String, Integer, DateTime, Text, Boolean, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.database import Base, get_async_db_manager
from app.core.db.tenant_mixin import TenantMixin
from app.models.config.sql_example_embeddings import (
    add_sql_example_embedding,
    update_sql_example_embedding,
    delete_sql_example_embedding
)

logger = logging.getLogger(__name__)


# ========== SQLAlchemy ORM 模型 ==========

class SqlExampleModel(Base, TenantMixin):
    """SQL 示例 ORM 模型（继承 TenantMixin 实现自动租户过滤）"""
    __tablename__ = "sql_examples"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(String(500), nullable=False, comment="问题描述")
    sql = Column(Text, nullable=False, comment="示例 SQL")
    description = Column(String(1000), nullable=True, comment="补充说明")
    tables = Column(String(500), nullable=True, comment="涉及的表名，逗号分隔")
    is_active = Column(Boolean, default=True, comment="是否启用")
    group_id = Column(Integer, nullable=True, index=True, comment="所属分组ID")
    # workspace_id 继承自 TenantMixin，不再手动定义
    created_by = Column(String(64), nullable=False, comment="创建者ID")
    normalized_question = Column(String(500), nullable=False, default="", comment="参数化后的问题")
    validation_status = Column(String(20), nullable=False, default="draft", index=True, comment="draft/valid/invalid/stale")
    validation_errors = Column(JSON, nullable=False, default=list, comment="校验错误")
    parameters_json = Column(JSON, nullable=False, default=list, comment="动态参数定义")
    intent_json = Column(JSON, nullable=False, default=dict, comment="安全的结构化语义意图")
    schema_fingerprint = Column(String(128), nullable=True, comment="校验时语义模型指纹")
    authorization_revision = Column(Integer, nullable=False, default=0, comment="校验时权限版本")
    validation_model_version = Column(Integer, nullable=False, default=1, comment="示例校验模型版本")
    validated_at = Column(DateTime, nullable=True)
    last_matched_at = Column(DateTime, nullable=True)
    match_count = Column(Integer, nullable=False, default=0)
    # [架构优化] 向量同步状态：synced/pending_add/pending_update/pending_delete
    vector_sync_status = Column(String(20), default='synced', comment="向量库同步状态")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ========== Pydantic 模型 ==========

class SqlExample(BaseModel):
    """SQL 示例 (Pydantic)"""
    id: Optional[int] = None
    question: str
    sql: str
    description: Optional[str] = None
    tables: Optional[str] = None
    is_active: bool = True
    group_id: Optional[int] = None
    workspace_id: str
    created_by: str
    normalized_question: str = ""
    validation_status: str = "draft"
    validation_errors: list[dict[str, Any]] = Field(default_factory=list)
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    intent: dict[str, Any] = Field(default_factory=dict)
    schema_fingerprint: Optional[str] = None
    authorization_revision: int = 0
    validation_model_version: int = 1
    validated_at: Optional[datetime] = None
    last_matched_at: Optional[datetime] = None
    match_count: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    @classmethod
    def from_orm(cls, orm_obj: SqlExampleModel) -> "SqlExample":
        """从 ORM 模型转换"""
        return cls(
            id=orm_obj.id,
            question=orm_obj.question,
            sql=orm_obj.sql,
            description=orm_obj.description,
            tables=orm_obj.tables,
            is_active=orm_obj.is_active,
            group_id=orm_obj.group_id,
            workspace_id=orm_obj.workspace_id,
            created_by=orm_obj.created_by,
            normalized_question=orm_obj.normalized_question or "",
            validation_status=orm_obj.validation_status or "draft",
            validation_errors=list(orm_obj.validation_errors or []),
            parameters=list(orm_obj.parameters_json or []),
            intent=dict(orm_obj.intent_json or {}),
            schema_fingerprint=orm_obj.schema_fingerprint,
            authorization_revision=int(orm_obj.authorization_revision or 0),
            validation_model_version=int(orm_obj.validation_model_version or 1),
            validated_at=orm_obj.validated_at,
            last_matched_at=orm_obj.last_matched_at,
            match_count=int(orm_obj.match_count or 0),
            created_at=orm_obj.created_at,
            updated_at=orm_obj.updated_at
        )


class SqlExampleCreate(BaseModel):
    """创建 SQL 示例请求"""
    question: str
    sql: str
    description: Optional[str] = None
    tables: Optional[str] = None
    group_id: Optional[int] = None
    is_active: bool = False
    parameters: list[dict[str, Any]] = Field(default_factory=list)


class SqlExampleUpdate(BaseModel):
    """更新 SQL 示例请求"""
    question: Optional[str] = None
    sql: Optional[str] = None
    description: Optional[str] = None
    tables: Optional[str] = None
    is_active: Optional[bool] = None
    group_id: Optional[int] = None
    parameters: Optional[list[dict[str, Any]]] = None


class SqlExampleValidationData(BaseModel):
    """仅供服务层写入的校验结果。"""

    normalized_question: str = ""
    status: str = "draft"
    errors: list[dict[str, Any]] = Field(default_factory=list)
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    intent: dict[str, Any] = Field(default_factory=dict)
    schema_fingerprint: Optional[str] = None
    authorization_revision: int = 0
    model_version: int = 1
    preview_sql: str = ""


# ========== 异步 CRUD 函数 ==========

async def create_sql_example_async(
    example: SqlExampleCreate,
    workspace_id: str,
    user_id: str,
    validation: Optional[SqlExampleValidationData] = None,
) -> SqlExample:
    """创建 SQL 示例（带向量同步补偿机制）"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        validation = validation or SqlExampleValidationData()
        can_enable = validation.status == "valid"
        orm_obj = SqlExampleModel(
            question=example.question,
            sql=example.sql,
            description=example.description,
            tables=example.tables,
            group_id=example.group_id,
            workspace_id=workspace_id,
            created_by=user_id,
            is_active=bool(example.is_active and can_enable),
            normalized_question=validation.normalized_question,
            validation_status=validation.status,
            validation_errors=validation.errors,
            parameters_json=validation.parameters,
            intent_json=validation.intent,
            schema_fingerprint=validation.schema_fingerprint,
            authorization_revision=validation.authorization_revision,
            validation_model_version=validation.model_version,
            validated_at=datetime.now() if can_enable else None,
            vector_sync_status='pending_add'  # 先标记为待同步
        )
        session.add(orm_obj)
        await session.flush()
        await session.refresh(orm_obj)
        
        logger.info(f"创建 SQL 示例: {orm_obj.id} - {example.question[:50]}")
        
        # 同步向量库（使用解耦接口）
        sync_success = await add_sql_example_embedding(
            example_id=orm_obj.id,
            question=orm_obj.question,
            sql=orm_obj.sql,
            workspace_id=orm_obj.workspace_id,
            owner_id=orm_obj.created_by,
            description=orm_obj.description or "",
            tables=orm_obj.tables or "",
            is_active=bool(orm_obj.is_active),
            validation_status=orm_obj.validation_status,
            normalized_question=orm_obj.normalized_question or "",
        )
        
        # [补偿机制] 更新同步状态
        orm_obj.vector_sync_status = 'synced' if sync_success else 'pending_add'
        
        return SqlExample.from_orm(orm_obj)


async def get_sql_example_async(example_id: int) -> Optional[SqlExample]:
    """获取单个 SQL 示例"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(SqlExampleModel).where(SqlExampleModel.id == example_id)
        )
        orm_obj = result.scalar_one_or_none()
        
        if orm_obj:
            return SqlExample.from_orm(orm_obj)
        return None


async def list_sql_examples_async(
    workspace_id: str,
    owner_id: Optional[str] = None,
    active_only: bool = False,
    validation_status: Optional[str] = None,
    group_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0
) -> dict:
    """
    获取 SQL 示例列表（支持分页）
    
    Returns:
        {"items": [...], "total": N, "limit": L, "offset": O}
    """
    from sqlalchemy import func
    
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # 基础查询条件
        base_where = [SqlExampleModel.workspace_id == workspace_id]

        if owner_id is not None:
            base_where.append(SqlExampleModel.created_by == owner_id)
        
        if active_only:
            base_where.append(SqlExampleModel.is_active == True)

        if validation_status:
            base_where.append(SqlExampleModel.validation_status == validation_status)
        
        if group_id is not None:
            base_where.append(SqlExampleModel.group_id == group_id)
        
        # 总数查询
        count_query = select(func.count(SqlExampleModel.id)).where(*base_where)
        total = (await session.execute(count_query)).scalar()
        
        # 分页查询
        # MySQL 不支持 NULLS LAST，使用 CASE WHEN 模拟
        from sqlalchemy import case
        query = select(SqlExampleModel).where(*base_where)
        query = query.order_by(
            case((SqlExampleModel.group_id.is_(None), 1), else_=0),  # NULL 排最后
            SqlExampleModel.group_id,
            SqlExampleModel.created_at.desc()
        )
        query = query.limit(limit).offset(offset)
        
        result = await session.execute(query)
        orm_objs = result.scalars().all()
        
        return {
            "items": [SqlExample.from_orm(obj) for obj in orm_objs],
            "total": total,
            "limit": limit,
            "offset": offset
        }


async def update_sql_example_async(
    example_id: int,
    update_data: SqlExampleUpdate,
    validation: Optional[SqlExampleValidationData] = None,
) -> Optional[SqlExample]:
    """更新 SQL 示例（带向量同步补偿机制）"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(SqlExampleModel).where(SqlExampleModel.id == example_id)
        )
        orm_obj = result.scalar_one_or_none()
        
        if not orm_obj:
            return None
        
        # 更新字段
        if update_data.question is not None:
            orm_obj.question = update_data.question
        if update_data.sql is not None:
            orm_obj.sql = update_data.sql
        if update_data.description is not None:
            orm_obj.description = update_data.description
        if update_data.tables is not None:
            orm_obj.tables = update_data.tables
        if validation is not None:
            orm_obj.normalized_question = validation.normalized_question
            orm_obj.validation_status = validation.status
            orm_obj.validation_errors = validation.errors
            orm_obj.parameters_json = validation.parameters
            orm_obj.intent_json = validation.intent
            orm_obj.schema_fingerprint = validation.schema_fingerprint
            orm_obj.authorization_revision = validation.authorization_revision
            orm_obj.validation_model_version = validation.model_version
            orm_obj.validated_at = datetime.now() if validation.status == "valid" else None
            if validation.status != "valid":
                orm_obj.is_active = False
        elif update_data.parameters is not None:
            orm_obj.parameters_json = update_data.parameters
        if update_data.is_active is not None:
            orm_obj.is_active = bool(
                update_data.is_active and orm_obj.validation_status == "valid"
            )
        if "group_id" in update_data.model_fields_set:
            orm_obj.group_id = update_data.group_id
        
        orm_obj.updated_at = datetime.now()
        
        logger.info(f"更新 SQL 示例: {example_id}")
        
        # 同步向量库（使用解耦接口）
        sync_success = await update_sql_example_embedding(
            example_id=orm_obj.id,
            question=orm_obj.question,
            sql=orm_obj.sql,
            workspace_id=orm_obj.workspace_id,
            owner_id=orm_obj.created_by,
            description=orm_obj.description or "",
            tables=orm_obj.tables or "",
            is_active=bool(orm_obj.is_active),
            validation_status=orm_obj.validation_status,
            normalized_question=orm_obj.normalized_question or "",
        )
        
        # [补偿机制] 更新同步状态
        orm_obj.vector_sync_status = 'synced' if sync_success else 'pending_update'
        
        return SqlExample.from_orm(orm_obj)


async def delete_sql_example_async(example_id: int) -> bool:
    """删除 SQL 示例（先删向量库再删数据库，保证一致性）"""
    # [架构优化] 先尝试删除向量库，失败则不删数据库
    sync_success = await delete_sql_example_embedding(example_id)
    
    if not sync_success:
        logger.warning(f"向量库删除失败，但继续删除数据库: {example_id}")
    
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            delete(SqlExampleModel).where(SqlExampleModel.id == example_id)
        )
        
        if result.rowcount > 0:
            logger.info(f"删除 SQL 示例: {example_id}")
            return True
        return False


# [已废弃] search_sql_examples_async 已移除
# 请使用 sql_example_embeddings.search_sql_examples_by_similarity 进行向量相似度搜索


async def batch_delete_sql_examples_async(
    example_ids: List[int],
    workspace_id: str,
    owner_id: str,
) -> int:
    """
    批量删除 SQL 示例（事务 + 批量向量删除）
    
    Args:
        example_ids: 要删除的示例 ID 列表
        workspace_id: 工作空间 ID（用于权限验证）
        
    Returns:
        实际删除的数量
    """
    from app.models.config.sql_example_embeddings import batch_delete_sql_example_embeddings
    
    if not example_ids:
        return 0
    
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        owned_ids = list(
            (
                await session.execute(
                    select(SqlExampleModel.id).where(
                        SqlExampleModel.id.in_(example_ids),
                        SqlExampleModel.workspace_id == workspace_id,
                        SqlExampleModel.created_by == owner_id,
                    )
                )
            ).scalars().all()
        )

        if not owned_ids:
            return 0

        # 事务内批量删除数据库记录
        result = await session.execute(
            delete(SqlExampleModel).where(
                SqlExampleModel.id.in_(owned_ids),
                SqlExampleModel.workspace_id == workspace_id,
                SqlExampleModel.created_by == owner_id,
            )
        )
        
        deleted_count = result.rowcount
        
        if deleted_count > 0:
            logger.info(f"批量删除 SQL 示例: count={deleted_count}, ids={owned_ids[:5]}...")
            
            # 批量删除向量库（使用原生批量接口）
            vector_deleted = await batch_delete_sql_example_embeddings(owned_ids)
            if not vector_deleted:
                raise RuntimeError(
                    f"批量删除 SQL 示例向量失败: ids={owned_ids[:5]}"
                )
        
        return deleted_count


async def batch_move_to_group_async(
    example_ids: List[int],
    group_id: Optional[int],
    workspace_id: str,
    owner_id: str,
) -> int:
    """
    批量移动 SQL 示例到指定分组
    
    Args:
        example_ids: 要移动的示例 ID 列表
        group_id: 目标分组 ID（None 表示移至"未分组"）
        workspace_id: 工作空间 ID（用于权限验证）
        
    Returns:
        实际更新的数量
    """
    from sqlalchemy import update
    
    if not example_ids:
        return 0
    
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            update(SqlExampleModel)
            .where(
                SqlExampleModel.id.in_(example_ids),
                SqlExampleModel.workspace_id == workspace_id,
                SqlExampleModel.created_by == owner_id,
            )
            .values(group_id=group_id, updated_at=datetime.now())
        )
        
        updated_count = result.rowcount
        
        if updated_count > 0:
            logger.info(f"批量移动 SQL 示例到分组: group_id={group_id}, count={updated_count}")
        
        return updated_count
