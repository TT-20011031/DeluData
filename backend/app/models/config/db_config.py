"""
数据库配置模型

存储用户的数据库连接配置，支持加密密码
使用 MySQL 持久化存储
"""
import logging
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from cryptography.fernet import Fernet
from sqlalchemy import Column, String, Integer, DateTime, Boolean, select, delete
from sqlalchemy.ext.asyncio import AsyncSession
import os
import base64

from app.core.db.database import Base, get_async_db_manager
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import AsyncAdaptedQueuePool
import threading

from app.core.db.mysql_connection_policy import create_async_mysql_engine

logger = logging.getLogger(__name__)

# ========== 工作空间连接池缓存 ==========
# 线程安全的全局连接池缓存，避免重复创建 Engine
_workspace_engines: dict[str, AsyncEngine] = {}
_engines_lock = threading.Lock()


# ========== 加密配置 ==========

_ENCRYPTION_KEY = os.getenv("DB_ENCRYPTION_KEY", "")
if not _ENCRYPTION_KEY:
    # 开发环境/本地演示使用固定密钥，防止重启后无法解密
    # 生产环境请务必设置 DB_ENCRYPTION_KEY 环境变量
    # Fernet 密钥必须是 url-safe base64 编码的 32 字节
    logger.warning("未检测到 DB_ENCRYPTION_KEY，使用默认开发密钥。生产环境请务必设置环境变量！")
    # 这是一个预先生成的有效 Fernet 密钥
    _ENCRYPTION_KEY = "9LtaInh-duMH4ZsmMYLGZITdDz6UVHea-M1aV5xhZ8c="

try:
    _fernet = Fernet(_ENCRYPTION_KEY.encode() if isinstance(_ENCRYPTION_KEY, str) else _ENCRYPTION_KEY)
    logger.info("Fernet 加密初始化成功")
except Exception as e:
    # 如果密钥无效，生成新的有效密钥
    logger.error(f"DB_ENCRYPTION_KEY 无效 ({e})，生成新密钥（旧连接将失效）")
    _fernet = Fernet(Fernet.generate_key())


# ========== SQLAlchemy ORM 模型 ==========

class UserDBConfigModel(Base):
    """用户/工作空间数据库配置 ORM 模型"""
    __tablename__ = "user_db_configs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=True, index=True)  # [DEPRECATED] 保留兼容旧数据，移除 unique
    workspace_id = Column(String(128), unique=True, nullable=True, index=True)  # [NEW] 工作空间级别共享
    configured_by = Column(String(64), nullable=True, comment="配置者用户ID")  # [NEW] 审计追踪
    host = Column(String(255), nullable=False)
    port = Column(Integer, default=3306)
    username = Column(String(64), nullable=False)
    encrypted_password = Column(String(512), nullable=False)
    database = Column(String(64), nullable=False)
    # 只读账号配置（用于Navicat模式数据查询）
    readonly_username = Column(String(64), nullable=True)
    readonly_encrypted_password = Column(String(512), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


# ========== Pydantic 模型 ==========

class UserDBConfig(BaseModel):
    """用户/工作空间数据库配置 (Pydantic)"""
    user_id: Optional[str] = None  # [DEPRECATED] 保留兼容
    workspace_id: Optional[str] = None  # [NEW] 工作空间级别
    configured_by: Optional[str] = None  # [NEW] 配置者
    host: str
    port: int = 3306
    username: str
    encrypted_password: str
    database: str
    # 只读账号配置（可选）
    readonly_username: Optional[str] = None
    readonly_encrypted_password: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    @classmethod
    def encrypt_password(cls, password: str) -> str:
        """加密密码"""
        return _fernet.encrypt(password.encode()).decode()
    
    def decrypt_password(self) -> str:
        """解密密码"""
        return _fernet.decrypt(self.encrypted_password.encode()).decode()
    
    def decrypt_readonly_password(self) -> Optional[str]:
        """解密只读账号密码"""
        if self.readonly_encrypted_password:
            return _fernet.decrypt(self.readonly_encrypted_password.encode()).decode()
        return None
    
    def get_connection_url(self) -> str:
        """获取数据库连接 URL"""
        password = self.decrypt_password()
        return f"mysql+pymysql://{self.username}:{password}@{self.host}:{self.port}/{self.database}"
    
    def get_readonly_connection_url(self) -> Optional[str]:
        """获取只读数据库连接 URL"""
        if not self.readonly_username or not self.readonly_encrypted_password:
            return None
        password = self.decrypt_readonly_password()
        return f"mysql+pymysql://{self.readonly_username}:{password}@{self.host}:{self.port}/{self.database}"
    
    def has_readonly_config(self) -> bool:
        """检查是否配置了只读账号"""
        return bool(self.readonly_username and self.readonly_encrypted_password)
    
    @classmethod
    def from_orm(cls, orm_obj: UserDBConfigModel) -> "UserDBConfig":
        """从 ORM 模型转换"""
        return cls(
            user_id=orm_obj.user_id,
            workspace_id=orm_obj.workspace_id,
            configured_by=orm_obj.configured_by,
            host=orm_obj.host,
            port=orm_obj.port,
            username=orm_obj.username,
            encrypted_password=orm_obj.encrypted_password,
            database=orm_obj.database,
            readonly_username=orm_obj.readonly_username,
            readonly_encrypted_password=orm_obj.readonly_encrypted_password,
            is_active=orm_obj.is_active,
            created_at=orm_obj.created_at,
            updated_at=orm_obj.updated_at
        )


class TableSchema(BaseModel):
    """表结构信息"""
    name: str
    columns: list[dict]
    row_count: Optional[int] = None
    business_name: Optional[str] = None
    description: Optional[str] = None
    is_sensitive: bool = False
    queryable: bool = True


class DBConnectionStatus(BaseModel):
    """数据库连接状态"""
    is_connected: bool
    host: Optional[str] = None
    database: Optional[str] = None
    tables: list[str] = []
    source: Optional[str] = None
    can_manage_connection: bool = False
    can_manage_semantic: bool = False
    can_query_sql: bool = False
    can_ask: bool = False


# ========== 异步 CRUD 函数 ==========

async def save_user_db_config_async(config: UserDBConfig) -> None:
    """保存用户数据库配置 (异步)"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # 检查是否已存在
        result = await session.execute(
            select(UserDBConfigModel).where(UserDBConfigModel.user_id == config.user_id)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            # 更新现有记录
            existing.host = config.host
            existing.port = config.port
            existing.username = config.username
            existing.encrypted_password = config.encrypted_password
            existing.database = config.database
            existing.readonly_username = config.readonly_username
            existing.readonly_encrypted_password = config.readonly_encrypted_password
            existing.is_active = True
            existing.updated_at = datetime.now()
        else:
            # 创建新记录
            new_config = UserDBConfigModel(
                user_id=config.user_id,
                host=config.host,
                port=config.port,
                username=config.username,
                encrypted_password=config.encrypted_password,
                database=config.database,
                readonly_username=config.readonly_username,
                readonly_encrypted_password=config.readonly_encrypted_password,
                is_active=True
            )
            session.add(new_config)
        
        logger.info(f"保存用户 {config.user_id} 的数据库配置")


async def get_user_db_config_async(user_id: str) -> Optional[UserDBConfig]:
    """获取用户数据库配置 (异步) - 兼容旧逻辑"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserDBConfigModel).where(UserDBConfigModel.user_id == user_id)
        )
        orm_obj = result.scalar_one_or_none()
        
        if orm_obj:
            return UserDBConfig.from_orm(orm_obj)
        return None


async def get_workspace_db_config_async(workspace_id: str) -> Optional[UserDBConfig]:
    """
    获取工作空间数据库配置 (异步)
    
    [NEW] 支持工作空间级别共享，管理员配置后所有员工可用
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserDBConfigModel).where(
                UserDBConfigModel.workspace_id == workspace_id,
                UserDBConfigModel.is_active == True
            )
        )
        orm_obj = result.scalar_one_or_none()
        
        if orm_obj:
            return UserDBConfig.from_orm(orm_obj)
        return None


async def get_workspace_admin_db_config_async(workspace_id: str) -> Optional[UserDBConfig]:
    """Return the latest active DB connection saved by an admin in this workspace."""
    from app.models.auth.rbac import RoleModel, UserModel
    from sqlalchemy.orm import selectinload

    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        users_result = await session.execute(
            select(UserModel)
            .where(
                UserModel.workspace_id == workspace_id,
                UserModel.disabled == False,  # noqa: E712
            )
            .options(selectinload(UserModel.roles).selectinload(RoleModel.permissions))
        )
        admin_user_ids = [
            user.id
            for user in users_result.scalars().all()
            if user.is_admin
        ]
        if not admin_user_ids:
            return None

        config_result = await session.execute(
            select(UserDBConfigModel)
            .where(
                UserDBConfigModel.user_id.in_(admin_user_ids),
                UserDBConfigModel.is_active == True,  # noqa: E712
            )
            .order_by(UserDBConfigModel.updated_at.desc())
        )
        orm_obj = config_result.scalars().first()
        if not orm_obj:
            return None

        config = UserDBConfig.from_orm(orm_obj)
        config.workspace_id = workspace_id
        config.configured_by = config.user_id
        return config


async def save_workspace_db_config_async(
    workspace_id: str,
    config: UserDBConfig,
    configured_by: str
) -> None:
    """
    保存工作空间数据库配置 (异步)
    
    [NEW] 管理员配置后，同工作空间所有用户可用
    
    Args:
        workspace_id: 工作空间 ID
        config: 数据库配置
        configured_by: 配置者用户 ID（用于审计）
    """
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # 检查是否已存在
        result = await session.execute(
            select(UserDBConfigModel).where(UserDBConfigModel.workspace_id == workspace_id)
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            # Legacy production schemas keep user_id NOT NULL and unique. Reuse
            # the configuring admin's row so one record serves both lookups.
            result = await session.execute(
                select(UserDBConfigModel).where(UserDBConfigModel.user_id == configured_by)
            )
            existing = result.scalar_one_or_none()

        if existing:
            existing.workspace_id = workspace_id
            # 更新现有记录
            existing.host = config.host
            existing.port = config.port
            existing.username = config.username
            existing.encrypted_password = config.encrypted_password
            existing.database = config.database
            existing.readonly_username = config.readonly_username
            existing.readonly_encrypted_password = config.readonly_encrypted_password
            existing.configured_by = configured_by
            existing.is_active = True
            existing.updated_at = datetime.now()
        else:
            # 创建新记录
            new_config = UserDBConfigModel(
                workspace_id=workspace_id,
                configured_by=configured_by,
                host=config.host,
                port=config.port,
                username=config.username,
                encrypted_password=config.encrypted_password,
                database=config.database,
                readonly_username=config.readonly_username,
                readonly_encrypted_password=config.readonly_encrypted_password,
                is_active=True
            )
            session.add(new_config)
        
        logger.info(f"[审计] 用户 {configured_by} 保存了工作空间 {workspace_id} 的数据库配置")


async def is_user_connected_async(user_id: str) -> bool:
    """检查用户是否已连接数据库 (异步)"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserDBConfigModel).where(
                UserDBConfigModel.user_id == user_id,
                UserDBConfigModel.is_active == True
            )
        )
        return result.scalar_one_or_none() is not None


async def disconnect_user_async(user_id: str) -> None:
    """断开用户数据库连接 (异步) - 删除配置"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        await session.execute(
            delete(UserDBConfigModel).where(UserDBConfigModel.user_id == user_id)
        )
        logger.info(f"删除用户 {user_id} 的数据库配置")


async def disconnect_workspace_async(workspace_id: str) -> None:
    """断开工作区共享数据库连接 (异步) - 删除配置"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        await session.execute(
            delete(UserDBConfigModel).where(UserDBConfigModel.workspace_id == workspace_id)
        )
        logger.info(f"删除工作区 {workspace_id} 的共享数据库配置")


async def deactivate_user_connection_async(user_id: str) -> None:
    """停用用户数据库连接 (异步) - 仅标记为非活跃"""
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(
            select(UserDBConfigModel).where(UserDBConfigModel.user_id == user_id)
        )
        orm_obj = result.scalar_one_or_none()
        if orm_obj:
            orm_obj.is_active = False
            logger.info(f"停用用户 {user_id} 的数据库连接")


# ========== 同步兼容函数 (过渡期使用) ==========
# TODO: 迁移完成后删除

_user_configs: dict[str, UserDBConfig] = {}
_active_sessions: dict[str, bool] = {}


def save_user_db_config(config: UserDBConfig) -> None:
    """保存用户数据库配置 (同步兼容)"""
    _user_configs[config.user_id] = config
    _active_sessions[config.user_id] = True
    # 同时尝试异步保存到数据库
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(save_user_db_config_async(config))
        else:
            loop.run_until_complete(save_user_db_config_async(config))
    except Exception as e:
        logger.warning(f"异步保存数据库配置失败: {e}")


def get_user_db_config(user_id: str) -> Optional[UserDBConfig]:
    """获取用户数据库配置 (同步兼容)"""
    # 优先从内存缓存获取
    if user_id in _user_configs:
        return _user_configs[user_id]
    
    # 内存没有则尝试从数据库异步加载（用于重启后恢复）
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 如果在异步上下文中，无法同步等待，返回 None
            # 调用方应使用 get_user_db_config_async
            return None
        else:
            config = loop.run_until_complete(get_user_db_config_async(user_id))
            if config:
                _user_configs[user_id] = config
                _active_sessions[user_id] = config.is_active
            return config
    except Exception as e:
        logger.warning(f"同步加载数据库配置失败: {e}")
        return None


def is_user_connected(user_id: str) -> bool:
    """检查用户是否已连接数据库 (同步兼容)"""
    return _active_sessions.get(user_id, False)


def disconnect_user(user_id: str) -> None:
    """断开用户数据库连接 (同步兼容)"""
    _active_sessions[user_id] = False
    # 同时尝试异步删除数据库记录
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(disconnect_user_async(user_id))
        else:
            loop.run_until_complete(disconnect_user_async(user_id))
    except Exception as e:
        logger.warning(f"异步删除数据库配置失败: {e}")


def activate_user_connection(user_id: str) -> None:
    """激活用户数据库连接 (同步兼容)"""
    if user_id in _user_configs:
        _active_sessions[user_id] = True


# ========== 工作空间连接池管理 ==========

def get_workspace_engine(workspace_id: str, config: UserDBConfig) -> AsyncEngine:
    """
    获取工作空间的数据库连接引擎（带连接池）
    
    [NEW] 实现连接池复用，避免重复创建 Engine
    
    Args:
        workspace_id: 工作空间 ID
        config: 数据库配置
        
    Returns:
        AsyncEngine: 异步数据库引擎
    """
    global _workspace_engines
    
    with _engines_lock:
        if workspace_id in _workspace_engines:
            logger.debug(f"复用工作空间 {workspace_id} 的连接池")
            return _workspace_engines[workspace_id]
        
        # 创建新的连接池
        connection_url = config.get_connection_url().replace(
            "mysql+pymysql://", "mysql+aiomysql://"
        )
        
        engine = create_async_mysql_engine(
            connection_url,
            poolclass=AsyncAdaptedQueuePool,
            pool_size=5,               # 连接池大小
            max_overflow=10,           # 最大溢出连接数
            pool_pre_ping=True,        # 连接健康检查
            pool_recycle=3600,         # 连接回收时间（秒）
        )
        
        _workspace_engines[workspace_id] = engine
        logger.info(f"创建工作空间 {workspace_id} 的连接池")
        
        return engine


def close_workspace_engine(workspace_id: str) -> None:
    """
    关闭并移除工作空间连接池
    
    Args:
        workspace_id: 工作空间 ID
    """
    global _workspace_engines
    
    with _engines_lock:
        if workspace_id in _workspace_engines:
            engine = _workspace_engines.pop(workspace_id)
            # 注意：dispose 需要在异步上下文中调用
            # 这里只是从缓存移除，实际关闭由 GC 处理
            logger.info(f"移除工作空间 {workspace_id} 的连接池缓存")


# ========== 审计日志 SQL 执行 ==========

async def execute_user_sql(
    workspace_id: str,
    user_id: str,
    sql: str,
    params: dict = None
) -> list:
    """
    执行用户 SQL 并记录审计日志
    
    [NEW] 用于记录 user_id 和 workspace_id 的 SQL 执行审计
    
    Args:
        workspace_id: 工作空间 ID
        user_id: 执行用户 ID
        sql: SQL 语句
        params: SQL 参数
        
    Returns:
        查询结果列表
    """
    from sqlalchemy import text
    
    # 获取配置
    config = await get_workspace_db_config_async(workspace_id)
    if not config:
        raise ValueError(f"工作空间 {workspace_id} 未配置数据库连接")
    
    # 获取连接池引擎
    engine = get_workspace_engine(workspace_id, config)
    
    # 记录审计日志
    logger.info(f"[审计] 用户 {user_id} 在工作空间 {workspace_id} 执行 SQL: {sql[:100]}...")
    
    # 执行 SQL
    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params or {})
        rows = result.fetchall()
        await conn.commit()
        
        logger.debug(f"[审计] SQL 执行完成，返回 {len(rows)} 行")
        return [dict(row._mapping) for row in rows]
