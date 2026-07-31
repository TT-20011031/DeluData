"""
模板分组模型

为文档模板提供分组管理功能
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Integer, DateTime, Text, select, delete, func, update
from sqlalchemy.orm import aliased

from app.core.db.database import Base, get_async_db_manager
from app.models.config.template import TemplateModel

logger = logging.getLogger(__name__)


# ========== SQLAlchemy ORM 模型 ==========

class TemplateGroupModel(Base):
    """模板分组 ORM 模型"""
    __tablename__ = "template_groups"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="分组名称")
    description = Column(Text, nullable=True, comment="分组描述")
    color = Column(String(20), default="#10B981", comment="分组颜色")
    workspace_id = Column(String(64), nullable=False, index=True, comment="工作空间ID")
    created_by = Column(String(64), nullable=False, comment="创建者ID")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ========== Pydantic 模型 ==========

class TemplateGroup(BaseModel):
    """模板分组 (Pydantic)"""
    id: Optional[int] = None
    name: str
    description: Optional[str] = None
    color: str = "#10B981"
    workspace_id: str
    created_by: str
    example_count: int = 0  # 关联的模板数量
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    @classmethod
    def from_orm(cls, orm_obj: TemplateGroupModel, count: int = 0) -> "TemplateGroup":
        """从 ORM 模型转换"""
        return cls(
            id=orm_obj.id,
            name=orm_obj.name,
            description=orm_obj.description,
            color=orm_obj.color,
            workspace_id=orm_obj.workspace_id,
            created_by=orm_obj.created_by,
            example_count=count,
            created_at=orm_obj.created_at,
            updated_at=orm_obj.updated_at
        )


class TemplateGroupCreate(BaseModel):
    """创建模板分组请求"""
    name: str
    description: Optional[str] = None
    color: str = "#10B981"


class TemplateGroupUpdate(BaseModel):
    """更新模板分组请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None


# ========== 异步 CRUD 函数 ==========

async def create_template_group_async(
    group: TemplateGroupCreate,
    workspace_id: str,
    user_id: str
) -> TemplateGroup:
    """创建模板分组"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        orm_obj = TemplateGroupModel(
            name=group.name,
            description=group.description,
            color=group.color,
            workspace_id=workspace_id,
            created_by=user_id
        )
        session.add(orm_obj)
        await session.flush()
        await session.refresh(orm_obj)
        
        logger.info(f"创建模板分组: {orm_obj.id} - {group.name}")
        return TemplateGroup.from_orm(orm_obj, 0)


async def get_template_group_async(group_id: int) -> Optional[TemplateGroup]:
    """获取单个模板分组"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(TemplateGroupModel).where(TemplateGroupModel.id == group_id)
        )
        orm_obj = result.scalar_one_or_none()
        
        if orm_obj:
            # 获取模板数量
            count_result = await session.execute(
                select(func.count(TemplateModel.id)).where(TemplateModel.group_id == group_id)
            )
            count = count_result.scalar() or 0
            return TemplateGroup.from_orm(orm_obj, count)
        return None


async def list_template_groups_async(workspace_id: str) -> List[TemplateGroup]:
    """
    获取模板分组列表（含模板数量）
    使用 LEFT JOIN 优化查询性能
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # 使用 LEFT JOIN 一次查询获取分组和数量
        query = (
            select(
                TemplateGroupModel,
                func.count(TemplateModel.id).label("template_count")
            )
            .outerjoin(TemplateModel, TemplateModel.group_id == TemplateGroupModel.id)
            .where(TemplateGroupModel.workspace_id == workspace_id)
            .group_by(TemplateGroupModel.id)
            .order_by(TemplateGroupModel.created_at.desc())
        )
        
        result = await session.execute(query)
        rows = result.all()
        
        return [TemplateGroup.from_orm(row[0], row[1]) for row in rows]


async def update_template_group_async(
    group_id: int,
    update_data: TemplateGroupUpdate
) -> Optional[TemplateGroup]:
    """更新模板分组"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(TemplateGroupModel).where(TemplateGroupModel.id == group_id)
        )
        orm_obj = result.scalar_one_or_none()
        
        if not orm_obj:
            return None
        
        # 更新字段
        if update_data.name is not None:
            orm_obj.name = update_data.name
        if update_data.description is not None:
            orm_obj.description = update_data.description
        if update_data.color is not None:
            orm_obj.color = update_data.color
        
        orm_obj.updated_at = datetime.now()
        
        logger.info(f"更新模板分组: {group_id}")
        return TemplateGroup.from_orm(orm_obj, 0)


async def delete_template_group_async(group_id: int) -> bool:
    """
    删除模板分组（软关联策略）
    删除分组时，将组内模板的 group_id 设为 None
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # 先将关联的模板 group_id 设为 None
        await session.execute(
            update(TemplateModel)
            .where(TemplateModel.group_id == group_id)
            .values(group_id=None)
        )
        
        # 删除分组
        result = await session.execute(
            delete(TemplateGroupModel).where(TemplateGroupModel.id == group_id)
        )
        
        if result.rowcount > 0:
            logger.info(f"删除模板分组: {group_id}")
            return True
        return False
