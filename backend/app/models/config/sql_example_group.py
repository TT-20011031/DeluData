"""
SQL 示例分组模型

用于将 SQL 示例按业务场景分类管理
"""
import logging
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Integer, DateTime, Text, select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.database import Base, get_async_db_manager

logger = logging.getLogger(__name__)


# ========== SQLAlchemy ORM 模型 ==========

class SqlExampleGroupModel(Base):
    """SQL 示例分组 ORM 模型"""
    __tablename__ = "sql_example_groups"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="分组名称")
    description = Column(String(500), nullable=True, comment="分组描述")
    color = Column(String(20), default='#3B82F6', comment="分组颜色")
    workspace_id = Column(String(64), nullable=False, index=True, comment="工作空间ID")
    created_by = Column(String(64), nullable=False, comment="创建者ID")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ========== Pydantic 模型 ==========

class SqlExampleGroup(BaseModel):
    """SQL 示例分组 (Pydantic)"""
    id: Optional[int] = None
    name: str
    description: Optional[str] = None
    color: str = '#3B82F6'
    workspace_id: str
    created_by: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    example_count: int = 0  # 组内示例数量（查询时填充）
    
    @classmethod
    def from_orm(cls, orm_obj: SqlExampleGroupModel, example_count: int = 0) -> "SqlExampleGroup":
        return cls(
            id=orm_obj.id,
            name=orm_obj.name,
            description=orm_obj.description,
            color=orm_obj.color,
            workspace_id=orm_obj.workspace_id,
            created_by=orm_obj.created_by,
            created_at=orm_obj.created_at,
            updated_at=orm_obj.updated_at or orm_obj.created_at,
            example_count=example_count
        )
    
    class Config:
        from_attributes = True


class SqlExampleGroupCreate(BaseModel):
    """创建分组请求"""
    name: str
    description: Optional[str] = None
    color: str = '#3B82F6'


class SqlExampleGroupUpdate(BaseModel):
    """更新分组请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None


# ========== CRUD 函数 ==========

async def create_sql_example_group_async(
    group: SqlExampleGroupCreate,
    workspace_id: str,
    user_id: str
) -> SqlExampleGroup:
    """创建 SQL 示例分组"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        orm_obj = SqlExampleGroupModel(
            name=group.name,
            description=group.description,
            color=group.color,
            workspace_id=workspace_id,
            created_by=user_id
        )
        session.add(orm_obj)
        await session.flush()
        await session.refresh(orm_obj)
        
        logger.info(f"创建 SQL 示例分组: {orm_obj.id} - {group.name}")
        return SqlExampleGroup.from_orm(orm_obj)


async def list_sql_example_groups_async(
    workspace_id: str,
    owner_id: str,
) -> List[SqlExampleGroup]:
    """
    获取分组列表（含示例数量）
    
    使用 LEFT JOIN 一次查询获取分组和计数，减少 RTT
    """
    from app.models.config.sql_example import SqlExampleModel
    from sqlalchemy import func, outerjoin
    
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # [优化] 使用 LEFT JOIN + GROUP BY 一次查询
        stmt = (
            select(
                SqlExampleGroupModel,
                func.count(SqlExampleModel.id).label('example_count')
            )
            .select_from(SqlExampleGroupModel)
            .outerjoin(
                SqlExampleModel,
                (SqlExampleGroupModel.id == SqlExampleModel.group_id)
                & (SqlExampleModel.workspace_id == workspace_id)
                & (SqlExampleModel.created_by == owner_id)
            )
            .where(
                SqlExampleGroupModel.workspace_id == workspace_id,
                SqlExampleGroupModel.created_by == owner_id,
            )
            .group_by(SqlExampleGroupModel.id)
            .order_by(SqlExampleGroupModel.created_at.desc())
        )
        
        result = await session.execute(stmt)
        rows = result.all()
        
        return [
            SqlExampleGroup.from_orm(row[0], row[1] or 0)
            for row in rows
        ]


async def get_sql_example_group_async(group_id: int) -> Optional[SqlExampleGroup]:
    """获取单个分组"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        stmt = select(SqlExampleGroupModel).where(SqlExampleGroupModel.id == group_id)
        result = await session.execute(stmt)
        orm_obj = result.scalar_one_or_none()
        
        if orm_obj:
            return SqlExampleGroup.from_orm(orm_obj)
        return None


async def update_sql_example_group_async(
    group_id: int,
    update_data: SqlExampleGroupUpdate
) -> Optional[SqlExampleGroup]:
    """更新分组"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        stmt = select(SqlExampleGroupModel).where(SqlExampleGroupModel.id == group_id)
        result = await session.execute(stmt)
        orm_obj = result.scalar_one_or_none()
        
        if not orm_obj:
            return None
        
        if update_data.name is not None:
            orm_obj.name = update_data.name
        if update_data.description is not None:
            orm_obj.description = update_data.description
        if update_data.color is not None:
            orm_obj.color = update_data.color
        
        orm_obj.updated_at = datetime.now()
        
        logger.info(f"更新 SQL 示例分组: {group_id}")
        return SqlExampleGroup.from_orm(orm_obj)


async def delete_sql_example_group_async(
    group_id: int,
    workspace_id: str,
    owner_id: str,
) -> bool:
    """
    删除分组（软关联策略：清空组内示例的 group_id）
    """
    from app.models.config.sql_example import SqlExampleModel
    
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # 1. 清空组内示例的 group_id（移至"未分组"）
        await session.execute(
            update(SqlExampleModel)
            .where(
                SqlExampleModel.group_id == group_id,
                SqlExampleModel.workspace_id == workspace_id,
                SqlExampleModel.created_by == owner_id,
            )
            .values(group_id=None)
        )
        
        # 2. 删除分组
        result = await session.execute(
            delete(SqlExampleGroupModel).where(
                SqlExampleGroupModel.id == group_id,
                SqlExampleGroupModel.workspace_id == workspace_id,
                SqlExampleGroupModel.created_by == owner_id,
            )
        )
        
        if result.rowcount > 0:
            logger.info(f"删除 SQL 示例分组: {group_id}")
            return True
        return False
