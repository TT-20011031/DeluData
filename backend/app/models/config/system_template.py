"""
系统回复风格模板模型

存储管理员可管理的系统预设模板，遵循设计原则：
- Async First: 全异步 I/O
- Schema Validation: 严格 Pydantic 校验
- No Hardcoding: 模板内容存数据库，支持动态管理
"""
import logging
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    Text,
    Boolean,
    UniqueConstraint,
    select,
    text,
)

from app.core.db.database import Base, get_async_db_manager
from app.core.db.tenant_mixin import TenantMixin

logger = logging.getLogger(__name__)


# ========== SQLAlchemy ORM 模型 ==========

class SystemTemplateModel(Base, TenantMixin):
    """
    系统回复风格模板 ORM 模型
    
    管理员可增删改的系统预设模板
    """
    __tablename__ = "system_templates"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "template_id",
            name="uq_system_templates_workspace_template",
        ),
    )
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(String(64), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    prompt = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False)  # 是否为默认模板
    sort_order = Column(Integer, default=0)  # 排序权重
    created_by = Column(String(64), nullable=True)  # 创建者 ID
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ========== Pydantic 模型 ==========

class SystemTemplate(BaseModel):
    """系统模板响应"""
    template_id: str
    name: str
    description: str = ""
    prompt: str
    is_default: bool = False
    sort_order: int = 0


class SystemTemplateCreate(BaseModel):
    """创建系统模板请求"""
    template_id: str = Field(..., min_length=1, max_length=64, pattern=r'^[a-z0-9_]+$')
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    prompt: str = Field(..., min_length=1, max_length=5000)
    is_default: bool = False
    sort_order: int = 0


class SystemTemplateUpdate(BaseModel):
    """更新系统模板请求"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    prompt: Optional[str] = Field(None, min_length=1, max_length=5000)
    is_default: Optional[bool] = None
    sort_order: Optional[int] = None


# ========== 异步 CRUD 函数 ==========

async def list_system_templates_async(workspace_id: str) -> List[SystemTemplate]:
    """获取所有系统模板"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(SystemTemplateModel)
            .where(SystemTemplateModel.workspace_id == workspace_id)
            .order_by(SystemTemplateModel.sort_order, SystemTemplateModel.created_at)
        )
        models = result.scalars().all()
        
        return [
            SystemTemplate(
                template_id=m.template_id,
                name=m.name,
                description=m.description or "",
                prompt=m.prompt,
                is_default=m.is_default,
                sort_order=m.sort_order
            )
            for m in models
        ]


async def list_effective_system_templates_async(workspace_id: str) -> List[SystemTemplate]:
    """
    获取“有效系统模板”列表（只读，不写库）。

    合并策略：
    - 优先使用数据库中的模板（支持管理员自定义）
    - 保证代码内置系统模板始终可见（避免新工作区为空）
    - default 模板始终展示代码基线，避免历史脏数据造成认知偏差
    """
    db_templates = await list_system_templates_async(workspace_id)
    db_by_id = {tpl.template_id: tpl for tpl in db_templates}

    from app.templates_config.synthesizer_templates import get_all_system_templates

    system_templates = get_all_system_templates()
    merged: List[SystemTemplate] = []

    for idx, (template_id, tpl) in enumerate(system_templates.items()):
        existing = db_by_id.get(template_id)

        if existing and template_id != "default":
            merged.append(existing)
            continue

        merged.append(
            SystemTemplate(
                template_id=template_id,
                name=tpl.get("name", existing.name if existing else template_id),
                description=tpl.get("description", ""),
                prompt=tpl.get("prompt", existing.prompt if existing else ""),
                is_default=(template_id == "default"),
                sort_order=existing.sort_order if existing else idx,
            )
        )

    # 追加数据库中的扩展模板（非内置）
    for tpl in db_templates:
        if tpl.template_id not in system_templates:
            merged.append(tpl)

    return merged


async def get_system_template_async(
    template_id: str, 
    workspace_id: str
) -> Optional[SystemTemplate]:
    """获取单个系统模板"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(SystemTemplateModel).where(
                SystemTemplateModel.template_id == template_id,
                SystemTemplateModel.workspace_id == workspace_id
            )
        )
        model = result.scalar_one_or_none()
        
        if not model:
            return None
        
        return SystemTemplate(
            template_id=model.template_id,
            name=model.name,
            description=model.description or "",
            prompt=model.prompt,
            is_default=model.is_default,
            sort_order=model.sort_order
        )


async def create_system_template_async(
    workspace_id: str,
    template_id: str,
    name: str,
    prompt: str,
    description: str = "",
    is_default: bool = False,
    sort_order: int = 0,
    created_by: str = None
) -> SystemTemplate:
    """创建系统模板"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        model = SystemTemplateModel(
            workspace_id=workspace_id,
            template_id=template_id,
            name=name,
            description=description,
            prompt=prompt,
            is_default=is_default,
            sort_order=sort_order,
            created_by=created_by
        )
        session.add(model)
        await session.flush()
        
        logger.info(f"创建系统模板: {template_id}")
        
        return SystemTemplate(
            template_id=model.template_id,
            name=model.name,
            description=model.description or "",
            prompt=model.prompt,
            is_default=model.is_default,
            sort_order=model.sort_order
        )


async def update_system_template_async(
    template_id: str,
    workspace_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    prompt: Optional[str] = None,
    is_default: Optional[bool] = None,
    sort_order: Optional[int] = None
) -> Optional[SystemTemplate]:
    """更新系统模板"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(SystemTemplateModel).where(
                SystemTemplateModel.template_id == template_id,
                SystemTemplateModel.workspace_id == workspace_id
            )
        )
        model = result.scalar_one_or_none()
        
        if not model:
            return None
        
        if name is not None:
            model.name = name
        if description is not None:
            model.description = description
        if prompt is not None:
            model.prompt = prompt
        if is_default is not None:
            model.is_default = is_default
        if sort_order is not None:
            model.sort_order = sort_order
        model.updated_at = datetime.now()
        
        logger.info(f"更新系统模板: {template_id}")
        
        return SystemTemplate(
            template_id=model.template_id,
            name=model.name,
            description=model.description or "",
            prompt=model.prompt,
            is_default=model.is_default,
            sort_order=model.sort_order
        )


async def delete_system_template_async(
    template_id: str,
    workspace_id: str
) -> bool:
    """删除系统模板"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(SystemTemplateModel).where(
                SystemTemplateModel.template_id == template_id,
                SystemTemplateModel.workspace_id == workspace_id
            )
        )
        model = result.scalar_one_or_none()
        
        if not model:
            return False
        
        await session.delete(model)
        logger.info(f"删除系统模板: {template_id}")
        return True


async def init_default_templates_async(workspace_id: str, created_by: str = None):
    """
    初始化默认系统模板
    
    规则：
    - 非 default 模板：仅补齐缺失项（INSERT IGNORE）
    - default 模板：每次强制以代码基线覆盖（UPSERT）
    """
    from app.templates_config.synthesizer_templates import SYSTEM_TEMPLATES
    
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        inserted = 0
        default_upserted = 0
        for idx, (tid, tpl) in enumerate(SYSTEM_TEMPLATES.items()):
            params = {
                "workspace_id": workspace_id,
                "template_id": tid,
                "name": tpl["name"],
                "description": tpl.get("description", ""),
                "prompt": tpl["prompt"],
                "is_default": 1 if tid == "default" else 0,
                "sort_order": idx,
                "created_by": created_by,
            }

            if tid == "default":
                upsert_result = await session.execute(
                    text(
                        """
                        INSERT INTO system_templates (
                            workspace_id,
                            template_id,
                            name,
                            description,
                            prompt,
                            is_default,
                            sort_order,
                            created_by,
                            created_at,
                            updated_at
                        ) VALUES (
                            :workspace_id,
                            :template_id,
                            :name,
                            :description,
                            :prompt,
                            :is_default,
                            :sort_order,
                            :created_by,
                            NOW(),
                            NOW()
                        )
                        ON DUPLICATE KEY UPDATE
                            name = VALUES(name),
                            description = VALUES(description),
                            prompt = VALUES(prompt),
                            is_default = 1,
                            sort_order = VALUES(sort_order),
                            updated_at = NOW()
                        """
                    ),
                    params,
                )
                default_upserted += int(upsert_result.rowcount or 0)
                continue

            insert_result = await session.execute(
                text(
                    """
                    INSERT IGNORE INTO system_templates (
                        workspace_id,
                        template_id,
                        name,
                        description,
                        prompt,
                        is_default,
                        sort_order,
                        created_by,
                        created_at,
                        updated_at
                    ) VALUES (
                        :workspace_id,
                        :template_id,
                        :name,
                        :description,
                        :prompt,
                        :is_default,
                        :sort_order,
                        :created_by,
                        NOW(),
                        NOW()
                    )
                    """
                ),
                params,
            )
            inserted += int(insert_result.rowcount or 0)
        
        logger.info(
            "初始化工作区 %s 的系统模板完成: candidates=%s inserted=%s default_upserted=%s",
            workspace_id,
            len(SYSTEM_TEMPLATES),
            inserted,
            default_upserted,
        )
