"""
文档样式配置模型

支持双层隔离：
- workspace_id + user_id=NULL → 工作空间默认
- workspace_id + user_id=具体用户 → 用户个人偏好

优先级：用户偏好 > 工作空间默认 > 系统内置默认（modern_business）
"""
import json
import logging
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Integer, DateTime, Text, UniqueConstraint, select

from app.core.db.database import Base, get_async_db_manager
from app.core.db.tenant_mixin import TenantMixin
from app.core.utils.docx_style_engine import PRESET_THEMES, DEFAULT_THEME

logger = logging.getLogger(__name__)


# ========== SQLAlchemy ORM 模型 ==========

class DocStyleConfigModel(Base, TenantMixin):
    """
    文档样式配置 ORM 模型

    user_id 为 NULL 时表示工作空间默认配置；
    user_id 非 NULL 时表示用户个人偏好。
    """
    __tablename__ = "doc_style_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_doc_style_ws_user"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=True, index=True)
    config_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ========== Pydantic 模型 ==========

class DocStyleConfigUpdate(BaseModel):
    """文档样式配置更新请求"""
    preset_theme: Optional[str] = Field(None, description="预设主题名 (modern_business|academic|clean_minimal)")
    config: Optional[dict] = Field(None, description="自定义样式配置 JSON（与预设主题合并）")


class DocStyleConfigResponse(BaseModel):
    """文档样式配置响应"""
    workspace_id: str
    user_id: Optional[str] = None
    preset_theme: Optional[str] = None
    config: dict = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ========== 异步 CRUD ==========

async def get_doc_style_config(
    workspace_id: str,
    user_id: Optional[str] = None,
) -> Optional[dict]:
    """
    获取文档样式配置（原始 JSON）

    Args:
        workspace_id: 工作空间 ID
        user_id: 用户 ID（None 获取工作空间默认）

    Returns:
        配置字典，不存在返回 None
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        stmt = select(DocStyleConfigModel).where(
            DocStyleConfigModel.workspace_id == workspace_id,
            DocStyleConfigModel.user_id == user_id,
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        try:
            return json.loads(row.config_json)
        except (json.JSONDecodeError, TypeError):
            return {}


async def get_resolved_doc_style(
    workspace_id: str,
    user_id: Optional[str] = None,
) -> dict:
    """
    获取合并后的样式配置

    优先级：用户偏好 > 工作空间默认 > 系统内置默认

    Returns:
        可直接传入 resolve_style_config() 的字典
    """
    ws_config = await get_doc_style_config(workspace_id, user_id=None)
    user_config = None
    if user_id:
        user_config = await get_doc_style_config(workspace_id, user_id=user_id)

    from app.core.utils.docx_style_engine import resolve_style_config

    preset = None
    if ws_config and "preset_theme" in ws_config:
        preset = ws_config.pop("preset_theme", None)
    if user_config and "preset_theme" in user_config:
        preset = user_config.pop("preset_theme", None)

    return resolve_style_config(
        user_config=user_config,
        workspace_config=ws_config,
        preset_name=preset,
    )


async def save_doc_style_config(
    workspace_id: str,
    user_id: Optional[str] = None,
    config: Optional[dict] = None,
) -> dict:
    """
    保存文档样式配置（创建或更新）

    Args:
        workspace_id: 工作空间 ID
        user_id: 用户 ID（None 保存工作空间默认）
        config: 样式配置字典

    Returns:
        保存后的配置字典
    """
    config = config or {}
    config_str = json.dumps(config, ensure_ascii=False)

    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        stmt = select(DocStyleConfigModel).where(
            DocStyleConfigModel.workspace_id == workspace_id,
            DocStyleConfigModel.user_id == user_id,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.config_json = config_str
            existing.updated_at = datetime.now()
            logger.info("更新文档样式配置 workspace=%s user=%s", workspace_id, user_id)
        else:
            new_row = DocStyleConfigModel(
                workspace_id=workspace_id,
                user_id=user_id,
                config_json=config_str,
            )
            session.add(new_row)
            logger.info("创建文档样式配置 workspace=%s user=%s", workspace_id, user_id)

    return config


async def delete_doc_style_config(
    workspace_id: str,
    user_id: Optional[str] = None,
) -> bool:
    """
    删除文档样式配置（重置为继承上级）

    Returns:
        是否成功删除
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        stmt = select(DocStyleConfigModel).where(
            DocStyleConfigModel.workspace_id == workspace_id,
            DocStyleConfigModel.user_id == user_id,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            await session.delete(existing)
            logger.info("删除文档样式配置 workspace=%s user=%s", workspace_id, user_id)
            return True
        return False
