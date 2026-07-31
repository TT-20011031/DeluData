"""
DeluData 智能问数系统 - SchemaSkill 元数据检索技能

职责：
1. 向量化存储表结构元数据到 ChromaDB
2. 带租户隔离的语义检索
3. 权限过滤 (第一道围栏 - 语义防火墙)
"""
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
import chromadb
from chromadb.config import Settings as ChromaSettings

from app.skills.base import SecureSkill
from app.models.common.context import UserContext
from app.config import get_settings


@dataclass
class TableSchema:
    """
    表结构定义
    
    Attributes:
        table_name: 表名
        description: 表描述
        columns: 列定义列表
        database: 所属数据库
        workspace_id: 所属工作空间
    """
    table_name: str
    description: str = ""
    columns: List[Dict[str, str]] = field(default_factory=list)
    database: str = ""
    workspace_id: str = "default"
    
    def to_text(self) -> str:
        """转换为文本描述，用于向量化"""
        cols_desc = ", ".join([
            f"{c.get('name', '')}({c.get('type', '')}): {c.get('comment', '')}"
            for c in self.columns
        ])
        return f"表名: {self.table_name}\n描述: {self.description}\n字段: {cols_desc}"
    
    def to_sql_ddl(self) -> str:
        """转换为 SQL DDL 格式"""
        cols = []
        for c in self.columns:
            col_def = f"  {c.get('name', '')} {c.get('type', '')}"
            if c.get('comment'):
                col_def += f" COMMENT '{c.get('comment')}'"
            cols.append(col_def)
        return f"CREATE TABLE {self.table_name} (\n" + ",\n".join(cols) + "\n);"


class SchemaSkill(SecureSkill):
    """
    元数据检索技能
    
    实现语义防火墙：
    - 检索时强制带上 workspace_id 过滤
    - 二次过滤确保只返回用户有权限的表
    """
    
    COLLECTION_NAME = "table_schemas"
    
    def __init__(self, chroma_client: Optional[chromadb.Client] = None):
        """
        初始化 SchemaSkill
        
        Args:
            chroma_client: ChromaDB 客户端，如果不传则自动创建
        """
        super().__init__(name="SchemaSkill")
        
        if chroma_client is None:
            settings = get_settings()
            chroma_client = chromadb.Client(ChromaSettings(
                persist_directory=settings.chroma.persist_dir,
                anonymized_telemetry=False
            ))
        
        self.client = chroma_client
        self._collection = None
    
    @property
    def collection(self):
        """懒加载 Collection"""
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"description": "Database table schemas for semantic search"}
            )
        return self._collection
    
    async def execute(
        self, 
        query: str, 
        user_context: UserContext,
        top_k: int = 10
    ) -> List[TableSchema]:
        """
        执行语义检索
        
        Args:
            query: 用户的自然语言查询
            user_context: 用户上下文
            top_k: 返回结果数量
            
        Returns:
            匹配的表结构列表
        """
        return await self.search_related_tables(query, user_context, top_k)
    
    async def search_related_tables(
        self,
        query: str,
        user_context: UserContext,
        top_k: int = 10
    ) -> List[TableSchema]:
        """
        语义防火墙检索
        
        实现双重过滤：
        1. ChromaDB 检索时的 where 过滤
        2. 返回结果的权限二次校验
        
        Args:
            query: 自然语言查询
            user_context: 用户上下文
            top_k: 返回结果数量
            
        Returns:
            用户有权访问的相关表结构列表
        """
        if not self.validate_user_context(user_context):
            return []
        
        self.log_execution("搜索相关表", f"query='{query}', workspace={user_context.workspace_id}")
        
        # 构建 ChromaDB 查询条件
        where_conditions = self._build_where_conditions(user_context)
        
        try:
            # 执行向量检索
            results = self.collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where_conditions if where_conditions else None,
                include=["documents", "metadatas", "distances"]
            )
            
            # 解析结果
            schemas = self._parse_results(results)
            
            # 二次权限过滤
            filtered_schemas = self._filter_by_permission(schemas, user_context)
            
            self.log_execution(
                "检索完成", 
                f"找到 {len(filtered_schemas)} 个有权访问的表"
            )
            
            return filtered_schemas
            
        except Exception as e:
            self.log_error(e, "search_related_tables")
            return []
    
    def _build_where_conditions(self, user_context: UserContext) -> Optional[Dict]:
        """
        构建 ChromaDB 查询的 where 条件
        
        Args:
            user_context: 用户上下文
            
        Returns:
            where 条件字典
        """
        conditions = []
        
        # 工作空间过滤 (多租户预留)
        conditions.append({
            "workspace_id": {"$eq": user_context.workspace_id}
        })
        
        # 表权限过滤 (如果有白名单)
        if user_context.allowed_tables:
            conditions.append({
                "table_name": {"$in": user_context.allowed_tables}
            })
        
        if len(conditions) == 1:
            return conditions[0]
        elif len(conditions) > 1:
            return {"$and": conditions}
        return None
    
    def _parse_results(self, results: Dict) -> List[TableSchema]:
        """
        解析 ChromaDB 查询结果
        
        Args:
            results: ChromaDB 查询返回的结果
            
        Returns:
            TableSchema 列表
        """
        schemas = []
        
        if not results or not results.get("metadatas"):
            return schemas
        
        metadatas = results["metadatas"][0] if results["metadatas"] else []
        documents = results["documents"][0] if results.get("documents") else []
        
        for i, metadata in enumerate(metadatas):
            schema = TableSchema(
                table_name=metadata.get("table_name", ""),
                description=metadata.get("description", ""),
                database=metadata.get("database", ""),
                workspace_id=metadata.get("workspace_id", "default"),
                columns=self._parse_columns(metadata.get("columns_json", "[]"))
            )
            schemas.append(schema)
        
        return schemas
    
    def _parse_columns(self, columns_json: str) -> List[Dict[str, str]]:
        """解析列信息 JSON"""
        import json
        try:
            return json.loads(columns_json) if columns_json else []
        except:
            return []
    
    def _filter_by_permission(
        self, 
        schemas: List[TableSchema], 
        user_context: UserContext
    ) -> List[TableSchema]:
        """
        二次权限过滤
        
        确保返回的表都在用户权限范围内
        """
        if not user_context.allowed_tables:
            # 空白名单表示允许所有（单租户模式）
            return schemas
        
        return [
            s for s in schemas 
            if s.table_name in user_context.allowed_tables
        ]
    
    async def add_table_schema(
        self, 
        schema: TableSchema,
        workspace_id: str = "default"
    ) -> bool:
        """
        添加表结构到向量库
        
        Args:
            schema: 表结构定义
            workspace_id: 工作空间ID
            
        Returns:
            是否添加成功
        """
        import json
        
        try:
            doc_id = f"{workspace_id}_{schema.database}_{schema.table_name}"
            
            self.collection.upsert(
                ids=[doc_id],
                documents=[schema.to_text()],
                metadatas=[{
                    "table_name": schema.table_name,
                    "description": schema.description,
                    "database": schema.database,
                    "workspace_id": workspace_id,
                    "columns_json": json.dumps(schema.columns, ensure_ascii=False)
                }]
            )
            
            self.log_execution("添加表结构", f"table={schema.table_name}")
            return True
            
        except Exception as e:
            self.log_error(e, "add_table_schema")
            return False
    
    async def sync_from_database(
        self,
        db_connection,
        workspace_id: str = "default"
    ) -> int:
        """
        从数据库同步表结构到向量库
        
        Args:
            db_connection: 数据库连接
            workspace_id: 工作空间ID
            
        Returns:
            同步的表数量
        """
        from sqlalchemy import inspect, text
        
        inspector = inspect(db_connection)
        synced_count = 0
        
        for table_name in inspector.get_table_names():
            columns = []
            for col in inspector.get_columns(table_name):
                columns.append({
                    "name": col["name"],
                    "type": str(col["type"]),
                    "comment": col.get("comment", "")
                })
            
            # 获取表注释
            table_comment = ""
            try:
                result = db_connection.execute(text(
                    f"SELECT table_comment FROM information_schema.tables "
                    f"WHERE table_name = '{table_name}'"
                ))
                row = result.fetchone()
                if row:
                    table_comment = row[0] or ""
            except:
                pass
            
            schema = TableSchema(
                table_name=table_name,
                description=table_comment,
                columns=columns,
                database=str(db_connection.engine.url.database),
                workspace_id=workspace_id
            )
            
            if await self.add_table_schema(schema, workspace_id):
                synced_count += 1
        
        self.log_execution("数据库同步完成", f"同步了 {synced_count} 张表")
        return synced_count
