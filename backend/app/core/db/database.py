"""
DeluData 智能问数系统 - 数据库连接管理

提供同步和异步 SQLAlchemy 数据库连接的统一管理
"""
import logging
import asyncio
from contextlib import contextmanager, asynccontextmanager
from typing import Generator, AsyncGenerator

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase
from sqlalchemy.pool import QueuePool, AsyncAdaptedQueuePool

from app.config import get_settings

logger = logging.getLogger(__name__)


async def _run_async_cleanup(awaitable, action: str) -> None:
    """
    取消安全的异步清理执行器。

    目标：当外层协程已被取消时，仍尽量完成 rollback/close，避免连接回收残留到 pool finalize。
    """
    task = asyncio.create_task(awaitable)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # 外层取消后，继续等待清理任务结束，避免连接“半关闭”。
        try:
            await asyncio.shield(task)
        except Exception as exc:
            logger.debug("异步数据库%s在取消后失败: %s", action, exc)
    except Exception as exc:
        logger.debug("异步数据库%s失败: %s", action, exc)


# ========== ORM Base ==========

class Base(DeclarativeBase):
    """SQLAlchemy ORM 基类"""
    pass


# ========== 同步数据库管理器 ==========

class DatabaseManager:
    """
    同步数据库连接管理器
    
    职责：
    - 管理数据库连接池
    - 提供 Session 工厂
    - 支持上下文管理器模式
    """
    
    _instance = None
    
    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        settings = get_settings()
        
        # 创建引擎，配置连接池
        self._engine = create_engine(
            settings.db.connection_url,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # 自动检测连接是否有效
            echo=False  # 禁用 SQL 日志
        )
        
        # 创建 Session 工厂
        self._session_factory = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False
        )
        
        self._initialized = True
    
    @property
    def engine(self):
        """获取数据库引擎"""
        return self._engine
    
    def get_session(self) -> Session:
        """创建新的数据库会话"""
        return self._session_factory()
    
    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """
        提供事务作用域的会话上下文管理器
        
        使用方式:
            with db_manager.session_scope() as session:
                session.query(...)
        """
        session = self.get_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    def test_connection(self) -> bool:
        """测试数据库连接"""
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"数据库连接测试失败: {e}")
            return False
    
    def dispose(self):
        """关闭所有连接"""
        self._engine.dispose()


# ========== 异步数据库管理器 ==========

class AsyncDatabaseManager:
    """
    异步数据库连接管理器
    
    使用 aiomysql 驱动，支持全异步操作
    """
    
    _instance = None
    
    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        settings = get_settings()
        
        # 创建异步引擎
        self._engine = create_async_engine(
            settings.db.async_connection_url,
            poolclass=AsyncAdaptedQueuePool,
            pool_size=settings.db.async_pool_size,             # [优化] 异步连接池大小
            max_overflow=settings.db.async_max_overflow,       # [优化] 异步连接池溢出连接
            pool_recycle=settings.db.async_pool_recycle,       # [优化] 连接回收秒数
            pool_pre_ping=settings.db.async_pool_pre_ping,     # [恢复] 连接可用性探测
            pool_timeout=settings.db.async_pool_timeout,        # [新增] 连接池等待超时
            echo=False
        )
        
        # 创建异步 Session 工厂
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False
        )
        
        self._initialized = True
        logger.info("异步数据库管理器初始化完成")
    
    @property
    def engine(self):
        """获取异步数据库引擎"""
        return self._engine
    
    def get_session(self) -> AsyncSession:
        """创建新的异步数据库会话"""
        return self._session_factory()
    
    @asynccontextmanager
    async def session_scope(self) -> AsyncGenerator[AsyncSession, None]:
        """
        提供事务作用域的异步会话上下文管理器
        
        使用方式:
            async with db_manager.session_scope() as session:
                await session.execute(...)
        """
        session = self.get_session()
        try:
            yield session
            await session.commit()
        except BaseException:
            await _run_async_cleanup(session.rollback(), "rollback")
            raise
        finally:
            await _run_async_cleanup(session.close(), "close")
    
    async def test_connection(self) -> bool:
        """测试异步数据库连接"""
        try:
            async with self._engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("异步数据库连接测试成功")
            return True
        except Exception as e:
            logger.error(f"异步数据库连接测试失败: {e}")
            return False
    
    async def dispose(self):
        """关闭所有异步连接"""
        await self._engine.dispose()


# ========== 全局实例 ==========

_db_manager: DatabaseManager = None
_async_db_manager: AsyncDatabaseManager = None


def get_db_manager() -> DatabaseManager:
    """获取同步数据库管理器单例"""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def get_async_db_manager() -> AsyncDatabaseManager:
    """获取异步数据库管理器单例"""
    global _async_db_manager
    if _async_db_manager is None:
        _async_db_manager = AsyncDatabaseManager()
    return _async_db_manager


# ========== FastAPI 依赖注入 ==========

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI 依赖注入用的同步数据库会话生成器
    
    使用方式:
        @router.get("/items")
        def get_items(db: Session = Depends(get_db)):
            ...
    """
    db_manager = get_db_manager()
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖注入用的异步数据库会话生成器
    
    使用方式:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_async_db)):
            ...
    """
    db_manager = get_async_db_manager()
    session = db_manager.get_session()
    try:
        yield session
    finally:
        await _run_async_cleanup(session.close(), "close")


@asynccontextmanager
async def get_async_db_context() -> AsyncGenerator[AsyncSession, None]:
    """
    独立的异步数据库会话上下文管理器
    
    用于非 FastAPI 路由场景（如 Agent/Worker 内部）
    
    使用方式:
        async with get_async_db_context() as db:
            result = await db.execute(...)
    """
    db_manager = get_async_db_manager()
    session = db_manager.get_session()
    try:
        yield session
    finally:
        await _run_async_cleanup(session.close(), "close")

