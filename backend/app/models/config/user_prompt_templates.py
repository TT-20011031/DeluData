"""
用户自定义 Prompt 模板模型

遵循设计原则：
- Design Rigor: 模块化解耦，workspace 隔离
- Schema Validation: Pydantic 严格校验
- Async First: 全异步 I/O
- No Hardcoding: 模板内容配置外置
"""
import logging
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Integer, DateTime, Text, UniqueConstraint, select

from app.core.db.database import Base, get_async_db_manager
from app.core.db.tenant_mixin import TenantMixin

logger = logging.getLogger(__name__)

# ========== 常量定义 ==========

# 用户模板 ID 前缀（命名空间隔离）
USER_TEMPLATE_PREFIX = "usr_"
# 用户模板 Prompt 最大长度
MAX_TEMPLATE_PROMPT_LENGTH = 2000
# 系统保留的模板 ID（禁止用户使用）
RESERVED_TEMPLATE_IDS = frozenset(["default", "concise", "detailed", "executive", "technical"])


# ========== SQLAlchemy ORM 模型 ==========

class UserPromptTemplateModel(Base, TenantMixin):
    """用户自定义 Prompt 模板 ORM 模型（继承 TenantMixin 实现自动租户过滤）"""
    __tablename__ = "user_prompt_templates"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    # workspace_id 继承自 TenantMixin，不再手动定义
    template_id = Column(String(64), nullable=False)  # 格式: usr_{用户定义}
    name = Column(String(128), nullable=False)  # 显示名称
    description = Column(String(256), nullable=False, default="")
    prompt = Column(Text, nullable=False)  # Prompt 内容
    created_by = Column(String(64), nullable=True, index=True)  # 创建者用户 ID（私有模板隔离）
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    __table_args__ = (
        UniqueConstraint('workspace_id', 'template_id', name='uq_workspace_template'),
    )


# ========== Pydantic 模型 ==========

class UserPromptTemplate(BaseModel):
    """用户 Prompt 模板 (Pydantic)"""
    id: int
    workspace_id: str
    template_id: str
    name: str
    description: str = ""
    prompt: str
    created_at: datetime
    updated_at: datetime
    
    @classmethod
    def from_orm(cls, orm_obj: UserPromptTemplateModel) -> "UserPromptTemplate":
        """从 ORM 模型转换"""
        return cls(
            id=orm_obj.id,
            workspace_id=orm_obj.workspace_id,
            template_id=orm_obj.template_id,
            name=orm_obj.name,
            description=orm_obj.description or "",
            prompt=orm_obj.prompt,
            created_at=orm_obj.created_at,
            updated_at=orm_obj.updated_at
        )


class UserPromptTemplateCreate(BaseModel):
    """创建用户模板请求 (Schema Validation)"""
    name: str = Field(..., min_length=1, max_length=128, description="模板名称")
    description: str = Field("", max_length=256, description="模板描述")
    prompt: str = Field(
        ..., 
        min_length=1, 
        max_length=MAX_TEMPLATE_PROMPT_LENGTH, 
        description=f"Prompt 内容 (≤{MAX_TEMPLATE_PROMPT_LENGTH}字)"
    )


class UserPromptTemplateUpdate(BaseModel):
    """更新用户模板请求 (Schema Validation)"""
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=256)
    prompt: Optional[str] = Field(None, min_length=1, max_length=MAX_TEMPLATE_PROMPT_LENGTH)


# ========== 异步 CRUD 函数 ==========

def generate_template_id(name: str) -> str:
    """生成用户模板 ID（带命名空间前缀）"""
    import re
    import uuid
    # 清理名称，保留字母数字下划线
    clean_name = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fa5]', '', name)[:20]
    short_uuid = uuid.uuid4().hex[:8]
    return f"{USER_TEMPLATE_PREFIX}{clean_name}_{short_uuid}"


async def get_user_template_async(
    workspace_id: str, 
    template_id: str
) -> Optional[UserPromptTemplate]:
    """获取单个用户模板 (异步)"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserPromptTemplateModel).where(
                UserPromptTemplateModel.workspace_id == workspace_id,
                UserPromptTemplateModel.template_id == template_id
            )
        )
        orm_obj = result.scalar_one_or_none()
        if orm_obj:
            return UserPromptTemplate.from_orm(orm_obj)
        return None


async def get_user_template_content_async(
    workspace_id: str, 
    template_id: str
) -> Optional[str]:
    """
    仅获取用户模板的 Prompt 内容 (性能优化)
    
    用于 Graph 入口预解析，避免加载完整模板数据
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserPromptTemplateModel.prompt).where(
                UserPromptTemplateModel.workspace_id == workspace_id,
                UserPromptTemplateModel.template_id == template_id
            )
        )
        row = result.scalar_one_or_none()
        return row


async def list_user_templates_async(
    workspace_id: str,
    user_id: str = None
) -> List[UserPromptTemplate]:
    """
    列出用户模板 (异步)
    
    Args:
        workspace_id: 工作区 ID
        user_id: 用户 ID（如果提供，只返回该用户创建的模板）
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        query = select(UserPromptTemplateModel).where(
            UserPromptTemplateModel.workspace_id == workspace_id
        )
        
        # 如果提供了 user_id，只返回该用户创建的模板
        if user_id:
            query = query.where(UserPromptTemplateModel.created_by == user_id)
        
        result = await session.execute(
            query.order_by(UserPromptTemplateModel.created_at.desc())
        )
        orm_list = result.scalars().all()
        return [UserPromptTemplate.from_orm(obj) for obj in orm_list]


async def create_user_template_async(
    workspace_id: str,
    data: UserPromptTemplateCreate,
    created_by: str = None
) -> UserPromptTemplate:
    """
    创建用户模板 (异步)
    
    Args:
        workspace_id: 工作区 ID
        data: 模板数据
        created_by: 创建者用户 ID（用于私有模板隔离）
    """
    template_id = generate_template_id(data.name)
    
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        new_template = UserPromptTemplateModel(
            workspace_id=workspace_id,
            template_id=template_id,
            name=data.name,
            description=data.description,
            prompt=data.prompt,
            created_by=created_by
        )
        session.add(new_template)
        await session.flush()
        logger.info(f"创建用户模板: workspace={workspace_id}, template_id={template_id}, created_by={created_by}")
        return UserPromptTemplate.from_orm(new_template)


async def update_user_template_async(
    workspace_id: str,
    template_id: str,
    data: UserPromptTemplateUpdate
) -> Optional[UserPromptTemplate]:
    """更新用户模板 (异步)"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserPromptTemplateModel).where(
                UserPromptTemplateModel.workspace_id == workspace_id,
                UserPromptTemplateModel.template_id == template_id
            )
        )
        existing = result.scalar_one_or_none()
        
        if not existing:
            return None
        
        # 增量更新
        if data.name is not None:
            existing.name = data.name
        if data.description is not None:
            existing.description = data.description
        if data.prompt is not None:
            existing.prompt = data.prompt
        existing.updated_at = datetime.now()
        
        logger.info(f"更新用户模板: workspace={workspace_id}, template_id={template_id}")
        return UserPromptTemplate.from_orm(existing)


async def delete_user_template_async(
    workspace_id: str,
    template_id: str
) -> bool:
    """删除用户模板 (异步)"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserPromptTemplateModel).where(
                UserPromptTemplateModel.workspace_id == workspace_id,
                UserPromptTemplateModel.template_id == template_id
            )
        )
        existing = result.scalar_one_or_none()
        
        if not existing:
            return False
        
        await session.delete(existing)
        logger.info(f"删除用户模板: workspace={workspace_id}, template_id={template_id}")
        return True
