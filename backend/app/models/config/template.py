"""
文档模板配置模型

存储用户上传的 Word/Excel 模板，支持 Jinja2 变量定义和示例数据
用于 OfficeWorker 的模板渲染功能
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, select, delete, JSON
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.database import Base, get_async_db_manager

logger = logging.getLogger(__name__)


# ========== SQLAlchemy ORM 模型 ==========

class TemplateModel(Base):
    """模板 ORM 模型"""
    __tablename__ = "templates"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="模板名称")
    description = Column(Text, nullable=True, comment="模板描述")
    file_path = Column(String(255), nullable=False, comment="文件存储路径")
    file_type = Column(String(10), nullable=False, comment="文件类型: docx/xlsx")
    keywords = Column(String(500), nullable=True, comment="匹配关键词，逗号分隔")
    
    # 增强字段
    variables_schema = Column(JSON, nullable=True, comment="变量定义 JSON")
    # 示例: {"sales": {"type": "number", "desc": "销售额"}, "chart": {"type": "image"}}
    
    example_context = Column(JSON, nullable=True, comment="示例数据 JSON")
    # 示例: {"sales": 150000, "chart": "/path/to/sample.png"}
    bindings = Column(JSON, nullable=True, comment="变量绑定信息 JSON")
    
    is_active = Column(Boolean, default=True, comment="是否启用")
    group_id = Column(Integer, nullable=True, index=True, comment="所属分组ID")
    workspace_id = Column(String(64), nullable=False, index=True, comment="工作空间ID")
    created_by = Column(String(64), nullable=False, comment="创建者ID")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ========== Pydantic 模型 ==========

class Template(BaseModel):
    """模板 (Pydantic)"""
    id: Optional[int] = None
    name: str
    description: Optional[str] = None
    file_path: str
    file_type: str
    keywords: Optional[str] = None
    variables_schema: Optional[Dict[str, Any]] = None
    example_context: Optional[Dict[str, Any]] = None
    bindings: Optional[List[Dict[str, Any]]] = None
    is_active: bool = True
    group_id: Optional[int] = None
    workspace_id: str
    created_by: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    @classmethod
    def from_orm(cls, orm_obj: TemplateModel) -> "Template":
        """从 ORM 模型转换"""
        return cls(
            id=orm_obj.id,
            name=orm_obj.name,
            description=orm_obj.description,
            file_path=orm_obj.file_path,
            file_type=orm_obj.file_type,
            keywords=orm_obj.keywords,
            variables_schema=orm_obj.variables_schema,
            example_context=orm_obj.example_context,
            bindings=getattr(orm_obj, "bindings", None),
            is_active=orm_obj.is_active,
            group_id=orm_obj.group_id,
            workspace_id=orm_obj.workspace_id,
            created_by=orm_obj.created_by,
            created_at=orm_obj.created_at,
            updated_at=orm_obj.updated_at
        )


class TemplateCreate(BaseModel):
    """创建模板请求"""
    name: str
    description: Optional[str] = None
    file_type: str  # docx/xlsx
    keywords: Optional[str] = None
    variables_schema: Optional[Dict[str, Any]] = None
    example_context: Optional[Dict[str, Any]] = None
    bindings: Optional[List[Dict[str, Any]]] = None
    group_id: Optional[int] = None


class TemplateUpdate(BaseModel):
    """更新模板请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[str] = None
    variables_schema: Optional[Dict[str, Any]] = None
    example_context: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    group_id: Optional[int] = None
    bindings: Optional[List[Dict[str, Any]]] = None


class VariableDefinition(BaseModel):
    """变量定义"""
    type: str  # text, number, date, image, table
    desc: Optional[str] = None


# ========== 异步 CRUD 函数 ==========

async def create_template_async(
    template: TemplateCreate,
    file_path: str,
    workspace_id: str,
    user_id: str
) -> Template:
    """创建模板"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        orm_obj = TemplateModel(
            name=template.name,
            description=template.description,
            file_path=file_path,
            file_type=template.file_type,
            keywords=template.keywords,
            variables_schema=template.variables_schema,
            example_context=template.example_context,
            bindings=template.bindings,
            group_id=template.group_id,
            workspace_id=workspace_id,
            created_by=user_id,
            is_active=True
        )
        session.add(orm_obj)
        await session.flush()
        await session.refresh(orm_obj)
        
        logger.info(f"创建模板: {orm_obj.id} - {template.name}")
        return Template.from_orm(orm_obj)


async def get_template_async(template_id: int) -> Optional[Template]:
    """获取单个模板"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(TemplateModel).where(TemplateModel.id == template_id)
        )
        orm_obj = result.scalar_one_or_none()
        
        if orm_obj:
            return Template.from_orm(orm_obj)
        return None


async def list_templates_async(
    workspace_id: str,
    active_only: bool = False
) -> List[Template]:
    """获取模板列表"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        query = select(TemplateModel).where(
            TemplateModel.workspace_id == workspace_id
        )
        
        if active_only:
            query = query.where(TemplateModel.is_active == True)
        
        query = query.order_by(TemplateModel.created_at.desc())
        
        result = await session.execute(query)
        orm_objs = result.scalars().all()
        
        return [Template.from_orm(obj) for obj in orm_objs]


async def update_template_async(
    template_id: int,
    update_data: TemplateUpdate
) -> Optional[Template]:
    """更新模板"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(TemplateModel).where(TemplateModel.id == template_id)
        )
        orm_obj = result.scalar_one_or_none()
        
        if not orm_obj:
            return None
        
        # 更新字段
        if update_data.name is not None:
            orm_obj.name = update_data.name
        if update_data.description is not None:
            orm_obj.description = update_data.description
        if update_data.keywords is not None:
            orm_obj.keywords = update_data.keywords
        if update_data.variables_schema is not None:
            orm_obj.variables_schema = update_data.variables_schema
        if update_data.example_context is not None:
            orm_obj.example_context = update_data.example_context
        if update_data.bindings is not None:
            orm_obj.bindings = update_data.bindings
        if update_data.is_active is not None:
            orm_obj.is_active = update_data.is_active
        
        orm_obj.updated_at = datetime.now()
        
        logger.info(f"更新模板: {template_id}")
        return Template.from_orm(orm_obj)


async def delete_template_async(template_id: int) -> bool:
    """删除模板"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            delete(TemplateModel).where(TemplateModel.id == template_id)
        )
        
        if result.rowcount > 0:
            logger.info(f"删除模板: {template_id}")
            return True
        return False


async def match_templates_async(
    workspace_id: str,
    query: str
) -> List[Template]:
    """
    根据查询匹配模板（低配版：关键词匹配）
    
    TODO: 后续可升级为向量检索
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # 基础查询：只查询激活的模板
        db_query = select(TemplateModel).where(
            TemplateModel.workspace_id == workspace_id,
            TemplateModel.is_active == True
        )
        
        result = await session.execute(db_query)
        orm_objs = result.scalars().all()
        
        # 简单的关键词匹配评分
        scored_results = []
        query_lower = query.lower()
        
        for obj in orm_objs:
            score = 0
            text_to_search = f"{obj.name} {obj.description or ''} {obj.keywords or ''}".lower()
            
            # 关键词匹配
            if obj.keywords:
                for keyword in obj.keywords.split(','):
                    if keyword.strip().lower() in query_lower:
                        score += 2  # 关键词匹配权重更高
            
            # 名称和描述匹配
            words = query_lower.split()
            for word in words:
                if word in text_to_search:
                    score += 1
            
            if score > 0:
                scored_results.append((score, obj))
        
        # 按分数排序，取前3个
        scored_results.sort(key=lambda x: x[0], reverse=True)
        top_results = [Template.from_orm(obj) for _, obj in scored_results[:3]]
        
        return top_results
