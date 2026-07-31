"""
用户级智能体配置模型

存储用户个人的智能体配置，支持层级合并：
用户配置 > 工作区配置 > 系统默认值

遵循设计原则：
- Async First: 全异步 I/O
- Schema Validation: 严格 Pydantic 校验
- No Hardcoding: 默认值常量化
"""
import logging
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, String, Integer, DateTime, Text, select

from app.core.db.database import Base, get_async_db_manager
from app.core.db.tenant_mixin import TenantMixin
from app.models.config.agent_config import (
    ExecutionMode,
    EXECUTION_MODES,
    DEFAULT_EXECUTION_MODE,
    DEFAULT_TEMPLATE_ID,
    MAX_CUSTOM_PROMPT_LENGTH,
    get_agent_config_async,
)

logger = logging.getLogger(__name__)


# ========== 系统默认值 ==========

SYSTEM_DEFAULTS = {
    "max_retries": 2,
    "always_confirm": False,
    "execution_mode": ExecutionMode.AUTO,
    "synthesizer_template": DEFAULT_TEMPLATE_ID,
    "synthesizer_custom_prompt": "",
}


# ========== SQLAlchemy ORM 模型 ==========

class UserAgentConfigModel(Base, TenantMixin):
    """
    用户级智能体配置 ORM 模型
    
    字段允许为空，空值表示继承上级配置（工作区配置或系统默认值）
    """
    __tablename__ = "user_agent_configs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    # workspace_id 继承自 TenantMixin
    
    # 以下字段均可为空，空值表示继承工作区配置
    max_retries = Column(Integer, nullable=True, default=None)
    always_confirm = Column(Integer, nullable=True, default=None)  # 0/1/NULL
    execution_mode = Column(String(20), nullable=True, default=None)
    synthesizer_template = Column(String(64), nullable=True, default=None)
    synthesizer_custom_prompt = Column(Text, nullable=True, default=None)
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ========== Pydantic 模型 ==========

class UserAgentConfig(BaseModel):
    """
    用户级智能体配置 (Pydantic)
    
    表示合并后的完整配置（不会有 None 值）
    """
    user_id: str
    workspace_id: str
    max_retries: int = SYSTEM_DEFAULTS["max_retries"]
    always_confirm: bool = SYSTEM_DEFAULTS["always_confirm"]
    execution_mode: ExecutionMode = SYSTEM_DEFAULTS["execution_mode"]
    synthesizer_template: str = SYSTEM_DEFAULTS["synthesizer_template"]
    synthesizer_custom_prompt: str = SYSTEM_DEFAULTS["synthesizer_custom_prompt"]
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class UserAgentConfigUpdate(BaseModel):
    """用户级智能体配置更新请求"""
    max_retries: Optional[int] = Field(None, ge=0, le=10, description="最大重试次数 (0-10)")
    always_confirm: Optional[bool] = Field(None, description="始终确认计划")
    execution_mode: Optional[ExecutionMode] = Field(
        None,
        description="执行模式 (auto|rag_only|sql_only|chart_only|office_only)"
    )
    synthesizer_template: Optional[str] = Field(
        None, 
        max_length=64, 
        description="模板 ID"
    )
    synthesizer_custom_prompt: Optional[str] = Field(
        None, 
        max_length=MAX_CUSTOM_PROMPT_LENGTH, 
        description=f"用户微调 Prompt (≤{MAX_CUSTOM_PROMPT_LENGTH}字)"
    )
    
    @field_validator('execution_mode', mode='before')
    @classmethod
    def validate_execution_mode(cls, v):
        """execution_mode 校验：字符串转枚举"""
        if v is None:
            return None
        if isinstance(v, ExecutionMode):
            return v
        if isinstance(v, str):
            try:
                return ExecutionMode(v)
            except ValueError:
                raise ValueError(f"无效的执行模式: {v}，允许值: {EXECUTION_MODES}")
        raise ValueError(f"执行模式必须为字符串或 ExecutionMode 枚举")


class UserAgentConfigResponse(BaseModel):
    """用户级智能体配置响应"""
    user_id: str
    workspace_id: str
    max_retries: int
    always_confirm: bool
    execution_mode: str
    synthesizer_template: str
    synthesizer_custom_prompt: str
    
    @classmethod
    def from_config(cls, config: UserAgentConfig) -> "UserAgentConfigResponse":
        """从配置对象转换"""
        return cls(
            user_id=config.user_id,
            workspace_id=config.workspace_id,
            max_retries=config.max_retries,
            always_confirm=config.always_confirm,
            execution_mode=config.execution_mode.value,
            synthesizer_template=config.synthesizer_template,
            synthesizer_custom_prompt=config.synthesizer_custom_prompt,
        )


# ========== 异步 CRUD 函数 ==========

async def _get_user_config_from_db(
    user_id: str, 
    workspace_id: str
) -> Optional[UserAgentConfigModel]:
    """从数据库获取用户配置原始记录"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserAgentConfigModel).where(
                UserAgentConfigModel.user_id == user_id,
                UserAgentConfigModel.workspace_id == workspace_id
            )
        )
        return result.scalar_one_or_none()


async def get_user_agent_config_async(
    user_id: str, 
    workspace_id: str
) -> UserAgentConfig:
    """
    获取用户配置（带层级合并）
    
    优先级：用户配置 > 工作区配置 > 系统默认值
    
    Args:
        user_id: 用户 ID
        workspace_id: 工作区 ID
    
    Returns:
        合并后的完整配置（不会有 None 值）
    """
    # 1. 查询用户配置
    user_config = await _get_user_config_from_db(user_id, workspace_id)
    
    # 2. 查询工作区配置
    workspace_config = await get_agent_config_async(workspace_id)
    
    # 3. 层级合并
    def resolve(user_val, ws_val, default_val):
        """解析配置值：用户配置 > 工作区配置 > 系统默认值"""
        if user_val is not None:
            return user_val
        if ws_val is not None:
            return ws_val
        return default_val
    
    # 解析 max_retries
    max_retries = resolve(
        user_config.max_retries if user_config else None,
        workspace_config.max_retries if workspace_config else None,
        SYSTEM_DEFAULTS["max_retries"]
    )
    
    # 解析 always_confirm (数据库存储 0/1/NULL)
    user_always_confirm = None
    if user_config and user_config.always_confirm is not None:
        user_always_confirm = bool(user_config.always_confirm)
    always_confirm = resolve(
        user_always_confirm,
        workspace_config.always_confirm if workspace_config else None,
        SYSTEM_DEFAULTS["always_confirm"]
    )
    
    # 解析 execution_mode
    user_mode = None
    if user_config and user_config.execution_mode:
        try:
            user_mode = ExecutionMode(user_config.execution_mode)
        except ValueError:
            user_mode = None
    execution_mode = resolve(
        user_mode,
        workspace_config.execution_mode if workspace_config else None,
        SYSTEM_DEFAULTS["execution_mode"]
    )
    
    # 解析 synthesizer_template
    synthesizer_template = resolve(
        user_config.synthesizer_template if user_config else None,
        workspace_config.synthesizer_template if workspace_config else None,
        SYSTEM_DEFAULTS["synthesizer_template"]
    )
    
    # 解析 synthesizer_custom_prompt
    synthesizer_custom_prompt = resolve(
        user_config.synthesizer_custom_prompt if user_config else None,
        workspace_config.synthesizer_custom_prompt if workspace_config else None,
        SYSTEM_DEFAULTS["synthesizer_custom_prompt"]
    )
    
    return UserAgentConfig(
        user_id=user_id,
        workspace_id=workspace_id,
        max_retries=max_retries,
        always_confirm=always_confirm,
        execution_mode=execution_mode,
        synthesizer_template=synthesizer_template,
        synthesizer_custom_prompt=synthesizer_custom_prompt,
        created_at=user_config.created_at if user_config else datetime.now(),
        updated_at=user_config.updated_at if user_config else datetime.now(),
    )


async def save_user_agent_config_async(
    user_id: str,
    workspace_id: str,
    max_retries: Optional[int] = None,
    always_confirm: Optional[bool] = None,
    execution_mode: Optional[str] = None,
    synthesizer_template: Optional[str] = None,
    synthesizer_custom_prompt: Optional[str] = None
) -> UserAgentConfig:
    """
    保存用户配置 (异步) - 创建或增量更新
    
    传入 None 表示不修改该字段（保持原值或继承上级配置）
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # 检查是否已存在
        result = await session.execute(
            select(UserAgentConfigModel).where(
                UserAgentConfigModel.user_id == user_id,
                UserAgentConfigModel.workspace_id == workspace_id
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            # 增量更新：只更新传入的字段
            if max_retries is not None:
                existing.max_retries = max_retries
            if always_confirm is not None:
                existing.always_confirm = 1 if always_confirm else 0
            if execution_mode is not None:
                if execution_mode in EXECUTION_MODES:
                    existing.execution_mode = execution_mode
                else:
                    logger.warning(f"无效的执行模式: {execution_mode}, 保持原值")
            if synthesizer_template is not None:
                existing.synthesizer_template = synthesizer_template
            if synthesizer_custom_prompt is not None:
                existing.synthesizer_custom_prompt = synthesizer_custom_prompt
            existing.updated_at = datetime.now()
            logger.info(f"更新用户 {user_id} 在工作区 {workspace_id} 的配置")
        else:
            # 创建新记录（字段可以为空，表示继承）
            new_config = UserAgentConfigModel(
                user_id=user_id,
                workspace_id=workspace_id,
                max_retries=max_retries,
                always_confirm=1 if always_confirm else (0 if always_confirm is False else None),
                execution_mode=execution_mode if execution_mode in EXECUTION_MODES else None,
                synthesizer_template=synthesizer_template,
                synthesizer_custom_prompt=synthesizer_custom_prompt,
            )
            session.add(new_config)
            await session.flush()
            logger.info(f"创建用户 {user_id} 在工作区 {workspace_id} 的配置")
        
        # 返回合并后的完整配置
        return await get_user_agent_config_async(user_id, workspace_id)


async def reset_user_agent_config_async(
    user_id: str,
    workspace_id: str
) -> UserAgentConfig:
    """
    重置用户配置为默认值（删除用户自定义配置）
    
    重置后，用户将继承工作区配置或系统默认值
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserAgentConfigModel).where(
                UserAgentConfigModel.user_id == user_id,
                UserAgentConfigModel.workspace_id == workspace_id
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            await session.delete(existing)
            logger.info(f"重置用户 {user_id} 在工作区 {workspace_id} 的配置")
        
        # 返回继承后的配置（工作区配置或系统默认值）
        return await get_user_agent_config_async(user_id, workspace_id)
