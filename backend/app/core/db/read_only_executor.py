"""
只读SQL执行器 - 数据库级安全隔离 (全局连接池版)

安全策略：
1. 使用仅有SELECT权限的数据库账号（数据库层面隔离）
2. 强制LIMIT注入（默认1000条，用户未指定时自动添加）
3. 执行超时熔断（默认5秒，防止慢查询）

性能优化：
- 使用全局连接池缓存，基于(user_id, connection_url)的Key进行管理
- 连接池复用，避免高并发下TCP握手开销
- 支持连接池过期清理
"""
import logging
import time
from typing import Optional, List, Tuple, Dict
from dataclasses import dataclass
from threading import Lock

import sqlparse
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.core.db.mysql_connection_policy import create_mysql_engine

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    """查询结果"""
    columns: List[str]
    rows: List[Tuple]
    row_count: int
    truncated: bool = False  # 是否被LIMIT截断
    error: Optional[str] = None
    execution_time_ms: float = 0


class _CachedEngine:
    """缓存的Engine包装器"""
    def __init__(self, engine: Engine, connection_url: str, connect_timeout_sec: int):
        self.engine = engine
        self.connection_url = connection_url
        self.connect_timeout_sec = connect_timeout_sec
        self.created_at = time.time()
        self.last_used_at = time.time()
    
    def touch(self):
        """更新最后使用时间"""
        self.last_used_at = time.time()
    
    def is_expired(self, max_idle_sec: int = 600) -> bool:
        """检查是否过期（默认10分钟未使用）"""
        return time.time() - self.last_used_at > max_idle_sec


class ReadOnlyExecutorPool:
    """
    只读执行器连接池管理器 (全局单例)
    
    基于 user_id 缓存连接池，实现连接复用
    """
    
    _instance: Optional["ReadOnlyExecutorPool"] = None
    _lock = Lock()
    
    DEFAULT_LIMIT = 1000
    DEFAULT_TIMEOUT_SEC = 5
    DEFAULT_CONNECT_TIMEOUT_SEC = 5
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._engines: Dict[str, _CachedEngine] = {}
                    cls._instance._engines_lock = Lock()
                    logger.info("ReadOnlyExecutorPool 单例初始化完成")
        return cls._instance
    
    def get_engine(
        self,
        user_id: str,
        connection_url: str,
        connect_timeout_sec: int = DEFAULT_CONNECT_TIMEOUT_SEC,
    ) -> Engine:
        """
        获取或创建连接池（基于user_id缓存）
        
        如果连接URL变更，会自动销毁旧连接池并创建新的
        """
        cache_key = user_id
        
        with self._engines_lock:
            cached = self._engines.get(cache_key)
            
            # 检查是否需要创建新连接池
            if (
                cached is None
                or cached.connection_url != connection_url
                or cached.connect_timeout_sec != connect_timeout_sec
            ):
                # 销毁旧连接池
                if cached:
                    cached.engine.dispose()
                    logger.info(f"销毁用户 {user_id} 的旧连接池（URL已变更）")
                
                # 创建新连接池
                engine = create_mysql_engine(
                    connection_url,
                    pool_pre_ping=True,
                    pool_size=5,
                    pool_recycle=3600,
                    connect_args={"connect_timeout": connect_timeout_sec},
                )
                self._engines[cache_key] = _CachedEngine(engine, connection_url, connect_timeout_sec)
                logger.info(f"为用户 {user_id} 创建新的只读连接池")
            
            # 更新使用时间
            self._engines[cache_key].touch()
            return self._engines[cache_key].engine
    
    def cleanup_expired(self, max_idle_sec: int = 600):
        """清理过期的连接池"""
        with self._engines_lock:
            expired_keys = [
                key for key, cached in self._engines.items() 
                if cached.is_expired(max_idle_sec)
            ]
            for key in expired_keys:
                self._engines[key].engine.dispose()
                del self._engines[key]
                logger.info(f"清理过期连接池: {key}")
    
    def dispose_user(self, user_id: str):
        """销毁指定用户的连接池"""
        with self._engines_lock:
            if user_id in self._engines:
                self._engines[user_id].engine.dispose()
                del self._engines[user_id]
                logger.info(f"销毁用户 {user_id} 的连接池")
    
    def dispose_all(self):
        """销毁所有连接池"""
        with self._engines_lock:
            for cached in self._engines.values():
                cached.engine.dispose()
            self._engines.clear()
            logger.info("销毁所有只读连接池")


# 全局单例实例
_pool = ReadOnlyExecutorPool()


def get_readonly_pool() -> ReadOnlyExecutorPool:
    """获取全局连接池管理器"""
    return _pool


class ReadOnlyExecutor:
    """
    只读SQL执行器
    
    使用全局连接池，安全执行只读查询
    """
    
    def __init__(self, user_id: str, connection_url: str, connect_timeout_sec: int | None = None):
        """
        初始化只读执行器（使用全局连接池）
        
        Args:
            user_id: 用户ID（用于连接池缓存Key）
            connection_url: 只读账号的数据库连接URL
        """
        self.user_id = user_id
        self.connection_url = connection_url
        self.connect_timeout_sec = connect_timeout_sec or ReadOnlyExecutorPool.DEFAULT_CONNECT_TIMEOUT_SEC
        self.engine = _pool.get_engine(user_id, connection_url, self.connect_timeout_sec)
    
    def execute_query(
        self, 
        sql: str, 
        timeout_sec: int = ReadOnlyExecutorPool.DEFAULT_TIMEOUT_SEC,
        max_rows: int = ReadOnlyExecutorPool.DEFAULT_LIMIT
    ) -> QueryResult:
        """
        执行只读查询（带安全约束）
        
        安全措施：
        1. 使用sqlparse清理注释后注入LIMIT（防止--注释绕过）
        2. 设置MySQL执行超时
        3. 依赖只读账号权限做根本性安全保障
        """
        start_time = time.time()
        
        try:
            # 1. 清理SQL并注入LIMIT
            processed_sql = self._process_sql(sql, max_rows)
            logger.info(f"执行只读查询: {processed_sql[:200]}...")
            
            with self.engine.connect() as conn:
                # 2. 设置MySQL执行超时（毫秒）
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {timeout_sec * 1000}"))
                
                # 3. 执行查询
                result = conn.execute(text(processed_sql))
                
                # 4. 获取结果
                columns = list(result.keys())
                rows = result.fetchall()
                
                execution_time = (time.time() - start_time) * 1000
                
                return QueryResult(
                    columns=columns,
                    rows=rows,
                    row_count=len(rows),
                    truncated=len(rows) >= max_rows,
                    execution_time_ms=execution_time
                )
                
        except SQLAlchemyError as e:
            error_msg = str(e)
            # 检测权限错误（这是预期的安全拦截）
            if "denied" in error_msg.lower() or "permission" in error_msg.lower():
                logger.warning(f"只读账号权限拦截: {error_msg}")
                return QueryResult(
                    columns=[],
                    rows=[],
                    row_count=0,
                    error="权限不足：只允许执行SELECT查询"
                )
            
            logger.error(f"SQL执行错误: {e}")
            return QueryResult(
                columns=[],
                rows=[],
                row_count=0,
                error=f"查询执行失败: {error_msg}"
            )
        except Exception as e:
            logger.error(f"未知错误: {e}")
            return QueryResult(
                columns=[],
                rows=[],
                row_count=0,
                error=f"系统错误: {str(e)}"
            )
    
    def _process_sql(self, sql: str, max_rows: int) -> str:
        """
        处理SQL：清理注释并注入LIMIT
        
        使用sqlparse解析AST，而非简单字符串拼接，避免注释绕过
        """
        # 1. 使用sqlparse格式化SQL，移除注释
        formatted = sqlparse.format(
            sql,
            strip_comments=True,  # 移除所有注释
            strip_whitespace=True
        )
        
        # 2. 检查是否已有LIMIT
        if not self._has_limit(formatted):
            # 移除末尾分号后添加LIMIT
            formatted = formatted.rstrip().rstrip(';')
            formatted = f"{formatted} LIMIT {max_rows}"
        
        return formatted
    
    def _has_limit(self, sql: str) -> bool:
        """
        检查SQL是否已包含LIMIT子句
        
        使用sqlparse解析，准确判断
        """
        parsed = sqlparse.parse(sql)
        if not parsed:
            return False
        
        statement = parsed[0]
        # 遍历tokens查找LIMIT关键字
        for token in statement.flatten():
            if token.ttype is sqlparse.tokens.Keyword and token.value.upper() == 'LIMIT':
                return True
        return False
    
    def get_table_data(
        self,
        table_name: str,
        page: int = 1,
        page_size: int = 50,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC"
    ) -> QueryResult:
        """
        获取表数据（分页）
        """
        # 表名安全校验（允许中文、字母、数字、下划线）
        import re
        if not re.match(r'^[\u4e00-\u9fa5a-zA-Z_][\u4e00-\u9fa5a-zA-Z0-9_]*$', table_name):
            return QueryResult(
                columns=[],
                rows=[],
                row_count=0,
                error="无效的表名"
            )
        
        offset = (page - 1) * page_size
        
        # 构建 SQL
        sql = f"SELECT * FROM `{table_name}`"
        
        # 添加排序
        if sort_by and re.match(r'^[a-zA-Z0-9_]+$', sort_by):
            order = "DESC" if sort_order.upper() == "DESC" else "ASC"
            sql += f" ORDER BY `{sort_by}` {order}"
            
        sql += f" LIMIT {page_size} OFFSET {offset}"
        
        return self.execute_query(sql, max_rows=page_size)
    
    def get_table_count(self, table_name: str) -> int:
        """获取表总行数"""
        import re
        if not re.match(r'^[\u4e00-\u9fa5a-zA-Z_][\u4e00-\u9fa5a-zA-Z0-9_]*$', table_name):
            return 0
        
        result = self.execute_query(f"SELECT COUNT(*) as cnt FROM `{table_name}`")
        if result.rows and len(result.rows) > 0:
            return result.rows[0][0]
        return 0
