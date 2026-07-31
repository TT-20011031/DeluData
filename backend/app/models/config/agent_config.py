"""
智能体配置模型

存储智能体全局配置（如重试次数），支持数据库持久化
使用 workspace_id 隔离不同工作区的配置
"""
import logging
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, String, Integer, DateTime, Text, select

from app.core.db.database import Base, get_async_db_manager
from app.core.db.tenant_mixin import TenantMixin

logger = logging.getLogger(__name__)

# ========== 常量定义 ==========

# 默认模板 ID
DEFAULT_TEMPLATE_ID = "default"
# 自定义 Prompt 最大长度
MAX_CUSTOM_PROMPT_LENGTH = 500


# 执行模式枚举 (审计建议: 使用 StrEnum 提升类型安全)
class ExecutionMode(str, Enum):
    """执行模式枚举"""
    AUTO = "auto"           # 智能模式（默认）
    RAG_ONLY = "rag_only"   # 纯知识库问答
    SQL_ONLY = "sql_only"   # 纯 SQL 查询
    CHART_ONLY = "chart_only"  # 纯图表生成（需数据）
    OFFICE_ONLY = "office_only"  # 纯文档生成（需数据）

# 兼容性：保留列表形式（用于校验）
EXECUTION_MODES = [mode.value for mode in ExecutionMode]
DEFAULT_EXECUTION_MODE = ExecutionMode.AUTO.value


# ========== SQLAlchemy ORM 模型 ==========

class AgentConfigModel(Base, TenantMixin):
    """智能体配置 ORM 模型（继承 TenantMixin 实现自动租户过滤）"""
    __tablename__ = "agent_configs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    # workspace_id 继承自 TenantMixin，不再手动定义
    max_retries = Column(Integer, default=2)  # 最大重试次数
    always_confirm = Column(Integer, default=0)  # 始终确认计划 (0=False, 1=True)
    # [NEW] 执行模式: auto|rag_only|sql_only|chart_only|office_only
    execution_mode = Column(String(20), nullable=False, default=DEFAULT_EXECUTION_MODE)
    # [NEW] Synthesizer 自定义提示词配置
    synthesizer_template = Column(String(64), nullable=False, default=DEFAULT_TEMPLATE_ID)  # 模板 ID
    synthesizer_custom_prompt = Column(Text, nullable=False, default="")  # 用户微调 Prompt
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ========== Pydantic 模型 ==========

class AgentConfig(BaseModel):
    """智能体配置 (Pydantic)"""
    workspace_id: str
    max_retries: int = 2
    always_confirm: bool = False  # 始终确认计划
    # [NEW] 执行模式 - 使用枚举类型
    execution_mode: ExecutionMode = ExecutionMode.AUTO
    # [NEW] Synthesizer 自定义提示词
    synthesizer_template: str = DEFAULT_TEMPLATE_ID
    synthesizer_custom_prompt: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    @classmethod
    def from_orm(cls, orm_obj: AgentConfigModel) -> "AgentConfig":
        """从 ORM 模型转换"""
        # 将字符串转换为枚举，默认 AUTO
        try:
            mode = ExecutionMode(orm_obj.execution_mode) if orm_obj.execution_mode else ExecutionMode.AUTO
        except ValueError:
            mode = ExecutionMode.AUTO
        
        return cls(
            workspace_id=orm_obj.workspace_id,
            max_retries=orm_obj.max_retries,
            always_confirm=bool(orm_obj.always_confirm),
            execution_mode=mode,
            synthesizer_template=orm_obj.synthesizer_template or DEFAULT_TEMPLATE_ID,
            synthesizer_custom_prompt=orm_obj.synthesizer_custom_prompt or "",
            created_at=orm_obj.created_at,
            updated_at=orm_obj.updated_at
        )


class AgentConfigUpdate(BaseModel):
    """智能体配置更新请求 (Schema Validation)"""
    max_retries: Optional[int] = Field(None, ge=0, le=10, description="最大重试次数 (0-10)")
    always_confirm: Optional[bool] = Field(None, description="始终确认计划")
    # [NEW] 执行模式 - 使用枚举类型
    execution_mode: Optional[ExecutionMode] = Field(
        None,
        description="执行模式 (auto|rag_only|sql_only|chart_only|office_only)"
    )
    # [NEW] Synthesizer 自定义提示词配置
    synthesizer_template: Optional[str] = Field(
        None, 
        max_length=64, 
        description="模板 ID (系统模板或用户模板)"
    )
    synthesizer_custom_prompt: Optional[str] = Field(
        None, 
        max_length=MAX_CUSTOM_PROMPT_LENGTH, 
        description=f"用户微调 Prompt (≤{MAX_CUSTOM_PROMPT_LENGTH}字)")
    
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


# ========== 异步 CRUD 函数 ==========

async def get_agent_config_async(workspace_id: str) -> Optional[AgentConfig]:
    """获取智能体配置 (异步)"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(AgentConfigModel).where(AgentConfigModel.workspace_id == workspace_id)
        )
        orm_obj = result.scalar_one_or_none()
        
        if orm_obj:
            return AgentConfig.from_orm(orm_obj)
        return None


async def save_agent_config_async(
    workspace_id: str,
    max_retries: Optional[int] = None,
    always_confirm: Optional[bool] = None,
    execution_mode: Optional[str] = None,
    synthesizer_template: Optional[str] = None,
    synthesizer_custom_prompt: Optional[str] = None
) -> AgentConfig:
    """
    保存智能体配置 (异步) - 创建或增量更新
    
    遵循设计原则：
    - Async First: 全异步 I/O
    - Schema Validation: 严格模式校验
    - Zero Tech Debt: 支持增量更新，只更新传入的字段
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # 检查是否已存在
        result = await session.execute(
            select(AgentConfigModel).where(AgentConfigModel.workspace_id == workspace_id)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            # 增量更新：只更新传入的字段
            if max_retries is not None:
                existing.max_retries = max_retries
            if always_confirm is not None:
                existing.always_confirm = 1 if always_confirm else 0
            if execution_mode is not None:
                # 校验执行模式
                if execution_mode in EXECUTION_MODES:
                    existing.execution_mode = execution_mode
                else:
                    logger.warning(f"无效的执行模式: {execution_mode}, 保持原值")
            if synthesizer_template is not None:
                existing.synthesizer_template = synthesizer_template
            if synthesizer_custom_prompt is not None:
                existing.synthesizer_custom_prompt = synthesizer_custom_prompt
            existing.updated_at = datetime.now()
            logger.info(f"更新工作区 {workspace_id} 的智能体配置")
            return AgentConfig.from_orm(existing)
        else:
            # 创建新记录（使用默认值）
            new_config = AgentConfigModel(
                workspace_id=workspace_id,
                max_retries=max_retries if max_retries is not None else 2,
                always_confirm=1 if always_confirm else 0,
                execution_mode=execution_mode if execution_mode in EXECUTION_MODES else DEFAULT_EXECUTION_MODE,
                synthesizer_template=synthesizer_template or DEFAULT_TEMPLATE_ID,
                synthesizer_custom_prompt=synthesizer_custom_prompt or ""
            )
            session.add(new_config)
            await session.flush()  # 确保 ID 生成
            logger.info(f"创建工作区 {workspace_id} 的智能体配置")
            return AgentConfig.from_orm(new_config)


async def get_or_create_agent_config_async(workspace_id: str) -> AgentConfig:
    """获取或创建智能体配置 (异步)"""
    config = await get_agent_config_async(workspace_id)
    if config:
        return config
    # 使用默认值创建
    return await save_agent_config_async(workspace_id, max_retries=2)
