"""
知识图谱服务层 (Graph Service)

职责：管理文件关系（Knowledge Graph）
- 关系创建/删除
- 图谱数据查询（UI 绘图）
- ChromaDB 关系同步
- 统计服务

注意：文件/文件夹 CRUD 已迁移至 FilesystemService
"""
import logging
from typing import List, Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_, or_, func

from app.models.knowledge.graph import Folder, File, FileRelationship
from app.config import get_settings
from app.models.common.enums import DocumentStatus
from app.core.db.tenant_mixin import get_current_workspace

logger = logging.getLogger(__name__)


class GraphService:
    """
    知识图谱服务
    
    Layer 2 (图谱层) - 仅管理文件关系，不处理 CRUD
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self._doc_skill = None
    
    def _resolve_workspace_id(self, workspace_id: Optional[str]) -> Optional[str]:
        return workspace_id or get_current_workspace()
    
    def _filter_by_workspace(
        self,
        stmt,
        model,
        workspace_id: Optional[str],
        include_deleted: bool = False,
    ):
        ws_id = self._resolve_workspace_id(workspace_id)
        if ws_id and hasattr(model, "workspace_id"):
            stmt = stmt.where(model.workspace_id == ws_id)
        if model is File and not include_deleted and hasattr(model, "is_deleted"):
            stmt = stmt.where(File.is_deleted.is_(False))
        return stmt

    @property
    def doc_skill(self):
        """懒加载 DocSkill（延迟导入以避免循环依赖）"""
        if self._doc_skill is None:
            from app.skills.doc_skill import DocSkill
            self._doc_skill = DocSkill()
        return self._doc_skill

    # ================= 关系管理 =================

    async def create_relationship(
        self, 
        source_id: str, 
        target_id: str, 
        relation_type: str,
        weight: float = 1.0,
        workspace_id: Optional[str] = None
    ) -> FileRelationship:
        """
        创建文档关联
        
        Args:
            source_id: 源文件 ID
            target_id: 目标文件 ID
            relation_type: 关系类型（引用/补充/冲突）
            weight: 关系权重 (1.0=手动, 0.8=引用, 0.5=同目录)
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        if ws_id:
            src_check = await self.db.execute(
                select(File.id).where(File.id == source_id, File.workspace_id == ws_id)
            )
            if not src_check.scalar_one_or_none():
                raise PermissionError("source_file 无权限或不存在")
            
            tgt_check = await self.db.execute(
                select(File.id).where(File.id == target_id, File.workspace_id == ws_id)
            )
            if not tgt_check.scalar_one_or_none():
                raise PermissionError("target_file 无权限或不存在")
        
        result = await self.db.execute(
            select(FileRelationship).where(
                and_(
                    FileRelationship.source_file_id == source_id,
                    FileRelationship.target_file_id == target_id
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.relation_type = relation_type
            await self.db.commit()
            rel = existing
        else:
            rel = FileRelationship(
                source_file_id=source_id,
                target_file_id=target_id,
                relation_type=relation_type
            )
            self.db.add(rel)
            await self.db.commit()
            await self.db.refresh(rel)

        # 同步 Chroma
        await self._sync_doc_relations(source_id)

        return rel

    async def delete_relationship(
        self, 
        source_id: str, 
        target_id: str,
        workspace_id: Optional[str] = None
    ) -> bool:
        """删除关系"""
        ws_id = self._resolve_workspace_id(workspace_id)
        if ws_id:
            src_check = await self.db.execute(
                select(File.id).where(File.id == source_id, File.workspace_id == ws_id)
            )
            if not src_check.scalar_one_or_none():
                return False
            
            tgt_check = await self.db.execute(
                select(File.id).where(File.id == target_id, File.workspace_id == ws_id)
            )
            if not tgt_check.scalar_one_or_none():
                return False
        
        result = await self.db.execute(
            select(FileRelationship).where(
                and_(
                    FileRelationship.source_file_id == source_id,
                    FileRelationship.target_file_id == target_id
                )
            )
        )
        rel = result.scalar_one_or_none()

        if rel:
            await self.db.delete(rel)
            await self.db.commit()
            await self._sync_doc_relations(source_id)
            return True
        return False

    async def get_related_context(
        self, 
        file_ids: List[str],
        min_weight: float = 0.5,
        workspace_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        [Graph RAG] 获取关联文档上下文
        
        Args:
            file_ids: 源文件 ID 列表
            min_weight: 最小权重阈值
            
        Returns:
            关联文档列表，包含摘要/开头 500 字
        """
        if not file_ids:
            return []
        
        # 查询关联关系
        rel_query = select(FileRelationship).where(
            FileRelationship.source_file_id.in_(file_ids)
        )
        rel_result = await self.db.execute(rel_query)
        rels = rel_result.scalars().all()
        
        # 收集目标文件 ID
        target_ids = [r.target_file_id for r in rels]
        
        if not target_ids:
            return []
        
        # 获取目标文件信息
        file_result = await self.db.execute(
            self._filter_by_workspace(select(File), File, workspace_id).where(File.id.in_(target_ids))
        )
        files = file_result.scalars().all()
        
        context = []
        for f in files:
            context.append({
                "id": f.id,
                "name": f.name,
                "description": f.description or "",
                "summary": f.summary or ""
            })
        
        return context

    # ================= 图谱数据（UI 绘图） =================

    async def get_graph_data(
        self, 
        user_id: Optional[str] = None,
        workspace_id: Optional[str] = None
    ) -> Dict[str, List]:
        """获取图数据（用于前端拓扑图）"""
        # 1. Nodes (Folders)
        folder_query = self._filter_by_workspace(select(Folder), Folder, workspace_id)
        if user_id:
            folder_query = folder_query.where(Folder.user_id == user_id)
        folder_result = await self.db.execute(folder_query)
        folders = folder_result.scalars().all()

        nodes = []
        for f in folders:
            nodes.append({
                "id": f.id,
                "data": {
                    "label": f.name,
                    "type": "folder",
                    "status": "active"
                },
                "position": {"x": f.x or 0, "y": f.y or 0},
                "type": "fileNode", 
                "style": {"backgroundColor": "#374151"}
            })

        # 2. Nodes (Files)
        file_query = self._filter_by_workspace(select(File), File, workspace_id)
        if user_id:
            file_query = file_query.where(File.user_id == user_id)
        file_query = file_query.where(File.status == DocumentStatus.INDEXED.value)
        
        file_result = await self.db.execute(file_query)
        files = file_result.scalars().all()
        
        file_ids = set()
        for f in files:
            file_ids.add(f.id)
            nodes.append({
                "id": f.id,
                "data": {
                    "label": f.name,
                    "type": f.file_type,
                    "status": f.status
                },
                "position": {"x": f.x or 0, "y": f.y or 0},
                "type": "fileNode"
            })

        edges = []
        
        # 3. Edges: File -> Folder
        for f in files:
            if f.folder_id:
                edges.append({
                    "id": f"e_folder_file_{f.folder_id}_{f.id}",
                    "source": f.folder_id,
                    "target": f.id,
                    "label": "contains",
                    "type": "default",
                    "animated": False
                })

        # 4. Edges: Folder -> Parent Folder
        for f in folders:
            if f.parent_id:
                edges.append({
                    "id": f"e_folder_folder_{f.parent_id}_{f.id}",
                    "source": f.parent_id,
                    "target": f.id,
                    "label": "parent",
                    "type": "default",
                    "animated": False
                })

        # 5. Edges: Explicit Relationships
        if file_ids:
            rel_query = select(FileRelationship).where(
                or_(
                    FileRelationship.source_file_id.in_(file_ids),
                    FileRelationship.target_file_id.in_(file_ids)
                )
            )
            rel_result = await self.db.execute(rel_query)
            rels = rel_result.scalars().all()
            
            for r in rels:
                if r.source_file_id in file_ids and r.target_file_id in file_ids:
                    edges.append({
                        "id": f"e{r.source_file_id}-{r.target_file_id}",
                        "source": r.source_file_id,
                        "target": r.target_file_id,
                        "label": r.relation_type,
                        "type": "floating",
                        "style": {"stroke": "#3b82f6", "strokeWidth": 2}
                    })

        return {"nodes": nodes, "edges": edges}

    # ================= ChromaDB 同步 =================

    async def _sync_doc_relations(self, doc_id: str):
        """同步文档关联关系到 ChromaDB"""
        try:
            result = await self.db.execute(
                select(FileRelationship).where(FileRelationship.source_file_id == doc_id)
            )
            rels = result.scalars().all()
            related_ids = ",".join([r.target_file_id for r in rels])
            
            await self._batch_update_chroma_metadata(
                doc_id,
                update_field="related_ids",
                update_value=related_ids
            )
                
        except Exception as e:
            logger.error(f"Sync relations failed: {e}")

    async def _batch_update_chroma_metadata(
        self, 
        file_id: str, 
        update_field: str, 
        update_value: str,
        batch_size: Optional[int] = None
    ):
        """分批更新 ChromaDB 元数据"""
        if batch_size is None:
            batch_size = get_settings().chroma.batch_size
            
        collection = self.doc_skill.collection
        
        c_result = collection.get(
            where={"doc_id": file_id},
            include=["metadatas"]
        )
        
        if not c_result or not c_result["ids"]:
            return
        
        ids = c_result["ids"]
        metadatas = c_result["metadatas"]
        total = len(ids)
        
        for i in range(0, total, batch_size):
            batch_ids = ids[i:i + batch_size]
            batch_metas = metadatas[i:i + batch_size]
            
            new_metas = []
            for meta in batch_metas:
                meta[update_field] = update_value
                new_metas.append(meta)
            
            collection.update(ids=batch_ids, metadatas=new_metas)

    # ================= 统计服务 =================

    async def get_knowledge_stats(
        self, 
        user_id: str, 
        dept_id: Optional[int] = None, 
        is_admin: bool = False,
        workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取知识库统计信息"""
        from app.models.auth.organization import DepartmentModel
        workspace_id = self._resolve_workspace_id(workspace_id)
        
        try:
            # [性能优化] 使用条件聚合，单次查询获取全局和私有文档数量
            from sqlalchemy import case
            stats_query = select(
                func.sum(case((File.visibility == 'public', 1), else_=0)).label('global_count'),
                func.sum(case(
                    (and_(File.visibility == 'private', File.owner_id == user_id), 1), 
                    else_=0
                )).label('private_count')
            )
            stats_query = stats_query.where(File.is_deleted.is_(False))
            if workspace_id:
                stats_query = stats_query.where(File.workspace_id == workspace_id)
            
            stats_result = await self.db.execute(stats_query)
            row = stats_result.fetchone()
            if row:
                global_count = int(row.global_count or 0)
                private_count = int(row.private_count or 0)
            else:
                global_count = 0
                private_count = 0
        except Exception as e:
            logger.warning(f"[Stats] 条件聚合查询失败，回退到传统查询: {e}")
            total_query = select(func.count(File.id))
            total_query = total_query.where(File.is_deleted.is_(False))
            if workspace_id:
                total_query = total_query.where(File.workspace_id == workspace_id)
            total_result = await self.db.execute(total_query)
            global_count = total_result.scalar() or 0
            private_count = 0
        
        # 统计各部门文档
        try:
            dept_query = (
                select(
                    DepartmentModel.id,
                    DepartmentModel.name,
                    DepartmentModel.parent_id,
                    func.count(File.id).label('doc_count')
                )
                .outerjoin(
                    File,
                    and_(
                        File.dept_id == DepartmentModel.id, 
                        File.visibility == 'dept',
                        File.is_deleted.is_(False),
                        *([File.workspace_id == workspace_id] if workspace_id else [])
                    )
                )
                .group_by(DepartmentModel.id, DepartmentModel.name, DepartmentModel.parent_id)
                .order_by(DepartmentModel.parent_id, DepartmentModel.id)
            )
            
            if workspace_id:
                dept_query = dept_query.where(DepartmentModel.workspace_id == workspace_id)
            if not is_admin:
                dept_query = dept_query.where(DepartmentModel.id == dept_id)
            
            dept_result = await self.db.execute(dept_query)
            dept_rows = dept_result.fetchall()
        except Exception as e:
            logger.warning(f"[Stats] 部门统计查询失败: {e}")
            dept_rows = []
        
        # 构建部门树
        if is_admin:
            def build_tree(parent_id):
                children = []
                for row in dept_rows:
                    row_parent = row.parent_id if row.parent_id else None
                    if row_parent == parent_id:
                        children.append({
                            "id": row.id,
                            "name": row.name,
                            "count": row.doc_count,
                            "children": build_tree(row.id)
                        })
                return children
            
            departments = build_tree(None)
        else:
            departments = [
                {"id": row.id, "name": row.name, "count": row.doc_count, "children": []}
                for row in dept_rows
            ]

        return {
            "global": {"count": global_count},
            "private": {"count": private_count},
            "departments": departments
        }
