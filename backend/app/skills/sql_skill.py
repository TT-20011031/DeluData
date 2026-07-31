"""
DeluData 智能问数系统 - SqlSkill SQL技能

职责：
1. 集成 Vanna.ai 进行 Text-to-SQL
2. AST 解析实现执行防火墙 (第二道围栏)
3. 安全的查询执行
"""
from typing import List, Optional, Set, Tuple
import re
import time
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.skills.base import SecureSkill
from app.models.common.context import UserContext
from app.models.common.execution import QueryResult
from app.config import get_settings
from app.models.config.sql_example import search_sql_examples_async, SqlExample


class AccessDeniedError(Exception):
    """越权访问错误"""
    def __init__(self, message: str, unauthorized_tables: List[str] = None):
        super().__init__(message)
        self.unauthorized_tables = unauthorized_tables or []


class SqlParseError(Exception):
    """SQL 解析错误"""
    pass


class SqlSkill(SecureSkill):
    """
    SQL 技能
    
    定位：专注于 "数据提取"，而非 "业务计算"
    
    实现执行防火墙：
    - 在执行前解析 SQL 获取所有引用的表
    - 校验这些表是否在用户权限范围内
    - 越权则直接拒绝执行
    """
    
    # 危险 SQL 关键字黑名单
    DANGEROUS_KEYWORDS = [
        'DROP', 'DELETE', 'TRUNCATE', 'ALTER', 'CREATE', 
        'INSERT', 'UPDATE', 'GRANT', 'REVOKE', 'EXEC',
        'EXECUTE', 'INTO OUTFILE', 'LOAD_FILE'
    ]
    
    def __init__(
        self, 
        db_engine: Engine,
        vanna_instance = None,
        max_rows: int = 10000
    ):
        """
        初始化 SqlSkill
        
        Args:
            db_engine: SQLAlchemy 数据库引擎
            vanna_instance: Vanna AI 实例 (可选，用于 Text-to-SQL)
            max_rows: 单次查询最大返回行数
        """
        super().__init__(name="SqlSkill")
        self.engine = db_engine
        self.vanna = vanna_instance
        self.max_rows = max_rows
    
    async def execute(
        self,
        sql: str,
        user_context: UserContext,
        engine: Optional[Engine] = None
    ) -> QueryResult:
        """
        执行 SQL 查询
        
        Args:
            sql: SQL 语句
            user_context: 用户上下文
            engine: 数据库引擎 (可选，若不提供则使用默认引擎)
            
        Returns:
            查询结果
        """
        return await self.execute_safe_query(sql, user_context, engine)
    
    async def generate_sql(
        self,
        question: str,
        schema_context: str = "",
        workspace_id: str = "default"
    ) -> str:
        """
        使用 Vanna AI 生成 SQL，并参考配置的 SQL 示例
        
        Args:
            question: 自然语言问题
            schema_context: 表结构上下文
            workspace_id: 工作空间ID，用于获取相关的 SQL 示例
            
        Returns:
            生成的 SQL 语句
        """
        if self.vanna is None:
            raise ValueError("Vanna 实例未初始化，无法使用 Text-to-SQL 功能")
        
        self.log_execution("生成SQL", f"question='{question}'")
        
        try:
            # 获取相关的 SQL 示例作为参考
            examples_context = await self._get_sql_examples_context(question, workspace_id)
            
            # 构建增强的问题（包含示例参考）
            enhanced_question = question
            if examples_context:
                enhanced_question = f"{question}\n\n参考示例:\n{examples_context}"
                self.log_execution("找到相关示例", f"{len(examples_context)} 字符")
            
            # Vanna 生成 SQL
            sql = self.vanna.generate_sql(enhanced_question)
            
            # 清理 SQL
            sql = self._clean_generated_sql(sql)
            
            self.log_execution("SQL生成完成", sql)
            return sql
            
        except Exception as e:
            self.log_error(e, "generate_sql")
            raise
    
    async def _get_sql_examples_context(
        self,
        question: str,
        workspace_id: str
    ) -> str:
        """
        根据问题获取相关的 SQL 示例，构建上下文
        
        Args:
            question: 用户问题
            workspace_id: 工作空间ID
            
        Returns:
            示例上下文字符串
        """
        try:
            # 提取关键词
            keywords = self._extract_keywords(question)
            
            if not keywords:
                return ""
            
            # 搜索相关示例
            examples = await search_sql_examples_async(workspace_id, keywords)
            
            if not examples:
                return ""
            
            # 构建上下文
            context_parts = []
            for i, example in enumerate(examples[:3], 1):  # 最多取3个示例
                context_parts.append(
                    f"示例{i}:\n问题: {example.question}\nSQL: {example.sql}"
                )
            
            return "\n\n".join(context_parts)
            
        except Exception as e:
            self.log_error(e, "_get_sql_examples_context")
            return ""
    
    def _extract_keywords(self, text: str) -> List[str]:
        """
        从文本中提取关键词
        
        Args:
            text: 输入文本
            
        Returns:
            关键词列表
        """
        # 简单的关键词提取：移除停用词，提取有意义的词
        stop_words = {
            '的', '了', '是', '在', '我', '有', '和', '就', '不', '人', '都', '一',
            '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着',
            '没有', '看', '好', '自己', '这', '什么', '查询', '显示', '列出',
            '请', '帮', '我要', '给我', '想要', '需要', '怎么', '如何',
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'select', 'from', 'where', 'and', 'or', 'show', 'get', 'find'
        }
        
        # 分词（简单按空格和标点分割）
        words = re.split(r'[\s,，。！？、；：""''（）\[\]【】]+', text.lower())
        
        # 过滤停用词和短词
        keywords = [w for w in words if w and len(w) >= 2 and w not in stop_words]
        
        return keywords[:10]  # 最多返回10个关键词
    
    async def execute_safe_query(
        self,
        sql: str,
        user_context: UserContext,
        engine: Optional[Engine] = None
    ) -> QueryResult:
        """
        安全执行 SQL 查询 (带执行防火墙)
        
        流程：
        1. 验证用户上下文
        2. 安全审计 SQL
        3. 解析表名并校验权限
        4. 执行查询
        
        Args:
            sql: SQL 语句  
            user_context: 用户上下文
            engine: 数据库引擎 (可选，若不提供则使用默认引擎)
            
        Returns:
            查询结果
            
        Raises:
            AccessDeniedError: 当检测到越权访问时
        """
        if not self.validate_user_context(user_context):
            return QueryResult(
                success=False,
                sql=sql,
                error="用户上下文无效"
            )
        
        self.log_execution("执行安全查询", f"sql='{sql[:100]}...'")
        
        start_time = time.time()
        
        try:
            # 1. 安全审计
            self._security_audit(sql)
            
            # 2. 解析 SQL 中的所有表名
            tables = self._parse_tables_from_sql(sql)
            self.log_execution("解析到的表", str(tables))
            
            # 3. 执行防火墙：校验权限
            self._execute_firewall(tables, user_context)
            
            # 4. 添加行数限制
            safe_sql = self._add_limit_if_needed(sql)
            
            # 5. 执行查询
            execution_engine = engine if engine else self.engine
            with execution_engine.connect() as conn:
                result = conn.execute(text(safe_sql))
                df = pd.DataFrame(result.fetchall(), columns=result.keys())
            
            execution_time = (time.time() - start_time) * 1000
            
            self.log_execution(
                "查询完成", 
                f"返回 {len(df)} 行，耗时 {execution_time:.2f}ms"
            )
            
            return QueryResult(
                success=True,
                data=df,
                sql=safe_sql,
                row_count=len(df),
                execution_time_ms=execution_time
            )
            
        except AccessDeniedError as e:
            self.log_execution("访问被拒绝", str(e))
            return QueryResult(
                success=False,
                sql=sql,
                error=f"权限不足: {e}"
            )
            
        except Exception as e:
            self.log_error(e, "execute_safe_query")
            return QueryResult(
                success=False,
                sql=sql,
                error=str(e)
            )
    
    def _security_audit(self, sql: str):
        """
        SQL 安全审计
        
        检查是否包含危险操作
        
        Args:
            sql: SQL 语句
            
        Raises:
            AccessDeniedError: 检测到危险操作
        """
        sql_upper = sql.upper()
        
        for keyword in self.DANGEROUS_KEYWORDS:
            # 使用正则确保是完整单词匹配
            pattern = r'\b' + keyword + r'\b'
            if re.search(pattern, sql_upper):
                raise AccessDeniedError(
                    f"检测到危险操作: {keyword}，查询被拒绝"
                )
    
    def _parse_tables_from_sql(self, sql: str) -> Set[str]:
        """
        从 SQL 中解析所有引用的表名
        
        使用正则表达式匹配常见的表引用模式：
        - FROM table_name
        - JOIN table_name
        - INTO table_name
        
        Args:
            sql: SQL 语句
            
        Returns:
            表名集合
        """
        tables = set()
        
        # 移除注释
        sql_clean = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
        sql_clean = re.sub(r'/\*.*?\*/', '', sql_clean, flags=re.DOTALL)
        
        # 移除字符串字面量
        sql_clean = re.sub(r"'[^']*'", "''", sql_clean)
        sql_clean = re.sub(r'"[^"]*"', '""', sql_clean)
        
        # 匹配表名的正则模式
        patterns = [
            r'\bFROM\s+([`\[\]"\w]+(?:\s*,\s*[`\[\]"\w]+)*)',
            r'\bJOIN\s+([`\[\]"\w]+)',
            r'\bINTO\s+([`\[\]"\w]+)',
            r'\bUPDATE\s+([`\[\]"\w]+)',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, sql_clean, re.IGNORECASE)
            for match in matches:
                # 处理多表情况 (FROM a, b, c)
                table_list = match.split(',')
                for table in table_list:
                    # 清理表名
                    table = table.strip()
                    table = re.sub(r'[`\[\]"]', '', table)  # 移除引号
                    table = table.split()[0]  # 移除别名
                    if table and not table.upper() in ['SELECT', 'WHERE', 'AND', 'OR']:
                        tables.add(table.lower())
        
        return tables
    
    def _execute_firewall(
        self, 
        tables: Set[str], 
        user_context: UserContext
    ):
        """
        执行防火墙：校验表访问权限
        
        Args:
            tables: SQL 中引用的表名集合
            user_context: 用户上下文
            
        Raises:
            AccessDeniedError: 检测到越权访问
        """
        # 单租户模式下，如果没有配置白名单则允许所有
        if not user_context.allowed_tables:
            return
        
        allowed = set(t.lower() for t in user_context.allowed_tables)
        unauthorized = tables - allowed
        
        if unauthorized:
            raise AccessDeniedError(
                f"越权访问被拦截: 无权访问表 {unauthorized}",
                unauthorized_tables=list(unauthorized)
            )
    
    def _add_limit_if_needed(self, sql: str) -> str:
        """
        如果 SQL 没有 LIMIT 子句，自动添加
        
        Args:
            sql: SQL 语句
            
        Returns:
            添加了 LIMIT 的 SQL
        """
        sql_upper = sql.upper().strip()
        
        # 检查是否已有 LIMIT
        if 'LIMIT' in sql_upper:
            return sql
        
        # 只对 SELECT 语句添加 LIMIT
        if sql_upper.startswith('SELECT'):
            return f"{sql.rstrip(';')} LIMIT {self.max_rows}"
        
        return sql
    
    def _clean_generated_sql(self, sql: str) -> str:
        """
        清理 Vanna 生成的 SQL
        
        Args:
            sql: 原始 SQL
            
        Returns:
            清理后的 SQL
        """
        if not sql:
            return sql
        
        # 移除 markdown 代码块标记
        sql = re.sub(r'^```sql\s*', '', sql, flags=re.IGNORECASE)
        sql = re.sub(r'^```\s*', '', sql)
        sql = re.sub(r'\s*```$', '', sql)
        
        return sql.strip()
    
    async def explain_query(self, sql: str) -> Optional[str]:
        """
        获取 SQL 执行计划
        
        Args:
            sql: SQL 语句
            
        Returns:
            执行计划文本
        """
        try:
            explain_sql = f"EXPLAIN {sql}"
            with self.engine.connect() as conn:
                result = conn.execute(text(explain_sql))
                rows = result.fetchall()
                return "\n".join([str(row) for row in rows])
        except Exception as e:
            self.log_error(e, "explain_query")
            return None
