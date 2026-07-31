"""
文件系统服务层 (Filesystem Service)

职责：文件和文件夹的 CRUD 操作（与 MySQL 和本地存储）
不处理图谱关系，遵循单一职责原则

注意：Session 通过 __init__ 注入，确保事务一致性
"""
import os
import uuid
import logging
from types import SimpleNamespace
from typing import List, Optional, Dict, Any
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_, or_

from app.core.security.data_scope import (
    build_visibility_where_clause,
    parse_legacy_scope,
    DataScopeLevel,
    resolve_scope_dept_ids,
)
from app.core.utils.storage_path import normalize_storage_path
from app.models.knowledge.graph import File, Folder
from app.models.common.enums import DocumentStatus
from app.core.db.tenant_mixin import get_current_workspace
from app.services.file_delete_service import FileDeleteService
from app.services.workspace_knowledge_governance_service import (
    WorkspaceKnowledgeGovernanceService,
)

logger = logging.getLogger(__name__)


class FilesystemService:
    """
    文件系统服务
    
    Layer 1 (基础层) - 仅管 CRUD，不依赖 GraphService
    """
    
    def __init__(self, db: AsyncSession, workspace_id: Optional[str] = None):
        """
        初始化服务
        
        Args:
            db: 外部注入的数据库会话，确保事务一致性
        """
        self.db = db
        self._workspace_id = workspace_id
        self._doc_skill = None
        self._knowledge_governance_service = None
    
    def _filter_by_workspace(self, stmt, model, include_deleted: bool = False):
        ws_id = get_current_workspace() or self._workspace_id
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

    @property
    def knowledge_governance_service(self) -> WorkspaceKnowledgeGovernanceService:
        if self._knowledge_governance_service is None:
            self._knowledge_governance_service = WorkspaceKnowledgeGovernanceService(self.db)
        return self._knowledge_governance_service

    async def _ensure_knowledge_action_allowed(self, action: str) -> None:
        workspace_id = get_current_workspace() or self._workspace_id
        if not workspace_id:
            return
        await self.knowledge_governance_service.ensure_action_allowed(workspace_id, action)  # type: ignore[arg-type]

    # ================= 静态资源隔离 (多租户) =================
    
    @staticmethod
    def get_upload_path(workspace_id: str, filename: str, subdir: str = "documents") -> str:
        """
        获取带租户隔离的上传路径
        
        路径结构: uploads/{workspace_id}/{subdir}/{filename}
        
        Args:
            workspace_id: 租户 ID
            filename: 文件名
            subdir: 子目录 (documents, images, temp 等)
        
        Returns:
            完整的物理存储路径
        """
        from app.config import get_settings
        
        settings = get_settings()
        base_path = settings.upload_dir  # 从配置读取，避免硬编码
        
        # 构建租户隔离路径
        tenant_path = os.path.join(base_path, workspace_id, subdir)
        
        # 确保目录存在
        os.makedirs(tenant_path, exist_ok=True)
        
        return os.path.join(tenant_path, filename)
    
    @staticmethod
    def get_workspace_upload_dir(workspace_id: str) -> str:
        """获取租户的上传根目录"""
        from app.config import get_settings
        
        settings = get_settings()
        return os.path.join(settings.upload_dir, workspace_id)

    # ================= 文件夹操作 =================

    async def create_folder(
        self, 
        name: str, 
        parent_id: Optional[str] = None, 
        user_id: Optional[str] = None,
        visibility: str = 'public',
        dept_id: Optional[int] = None,
        owner_id: Optional[str] = None
    ) -> Folder:
        """创建文件夹"""
        await self._ensure_knowledge_action_allowed("create_folder")
        folder = Folder(
            id=str(uuid.uuid4()),
            name=name,
            parent_id=parent_id,
            user_id=user_id,
            visibility=visibility,
            dept_id=dept_id,
            owner_id=owner_id or user_id
        )
        self.db.add(folder)
        await self.db.commit()
        await self.db.refresh(folder)
        return folder

    async def get_folder_structure(
        self, 
        user_id: Optional[str] = None, 
        scope: Optional[str] = None,
        is_workspace_admin: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        获取完整的文件夹树结构（递归）
        
        Args:
            user_id: 可选的用户ID过滤
            scope: 可见范围 ('public', 'private', 'dept_<id>')
        """
        folder_query = self._filter_by_workspace(select(Folder), Folder)
        file_query = self._filter_by_workspace(select(File), File)

        scope_spec = parse_legacy_scope(scope)
        if not scope_spec.valid:
            logger.warning(f"[Security] Invalid scope parameter: {scope}")
            return []

        try:
            if scope_spec.apply_filter:
                legacy_context = SimpleNamespace(
                    user_id=user_id or "",
                    data_scope=DataScopeLevel.ALL,
                    dept_id=None,
                    is_workspace_admin=is_workspace_admin,
                )
                folder_clause = build_visibility_where_clause(
                    Folder,
                    user_context=legacy_context,
                    visibilities=scope_spec.visibilities,
                    dept_ids=scope_spec.dept_ids,
                    allow_private_without_owner=not bool(user_id),
                )
                file_clause = build_visibility_where_clause(
                    File,
                    user_context=legacy_context,
                    visibilities=scope_spec.visibilities,
                    dept_ids=scope_spec.dept_ids,
                    allow_private_without_owner=not bool(user_id),
                )
                if folder_clause is None or file_clause is None:
                    return []
                folder_query = folder_query.where(folder_clause)
                file_query = file_query.where(file_clause)
            elif not scope and user_id and not is_workspace_admin:
                file_query = file_query.where(File.user_id == user_id)

            folder_result = await self.db.execute(folder_query)
            folders = folder_result.scalars().all()
            file_result = await self.db.execute(file_query)
            files = file_result.scalars().all()
        except Exception as e:
            logger.warning(f"[Structure] 文件夹查询失败，返回所有文件夹: {e}")
            folder_result = await self.db.execute(self._filter_by_workspace(select(Folder), Folder))
            folders = folder_result.scalars().all()
            logger.warning(f"[Structure] 文件查询失败，返回所有文件: {e}")
            file_result = await self.db.execute(self._filter_by_workspace(select(File), File))
            files = file_result.scalars().all()

        # 构建映射
        folder_map = {
            f.id: {"id": f.id, "name": f.name, "type": "folder", "children": []} 
            for f in folders
        }
        root_nodes = []

        # 挂载文件夹
        for f in folders:
            node = folder_map[f.id]
            if f.parent_id and f.parent_id in folder_map:
                folder_map[f.parent_id]["children"].append(node)
            else:
                root_nodes.append(node)

        # 挂载文件
        for file in files:
            file_node = {
                "id": file.id,
                "name": file.name,
                "type": "file",
                "file_type": file.file_type,
                "status": file.status,
                "is_deleted": bool(getattr(file, "is_deleted", False)),
                "delete_status": getattr(file, "delete_status", "active") or "active",
                "delete_error": getattr(file, "delete_error", None),
                "delete_op_id": getattr(file, "delete_op_id", None),
                "description": file.description,
                "pageindex_status": file.pageindex_status,
            }
            if file.folder_id and file.folder_id in folder_map:
                folder_map[file.folder_id]["children"].append(file_node)
            else:
                root_nodes.append(file_node)
        
        return root_nodes

    async def get_structure_nodes(
        self,
        user_id: Optional[str] = None,
        scope: Optional[str] = None,
        parent_id: Optional[str] = None,
        limit: int = 200,
        cursor: Optional[str] = None,
        is_workspace_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        分页获取某一层级的节点（文件夹 + 文件），用于前端懒加载。

        Args:
            user_id: 用户 ID（用于 private scope 过滤）
            scope: 可见范围 ('public', 'private', 'dept_<id>')
            parent_id: 父文件夹 ID，None 表示根节点
            limit: 单次返回节点上限
            cursor: 偏移量游标（字符串整数）
        """
        safe_limit = max(1, min(int(limit or 200), 500))
        try:
            offset = max(int(cursor or "0"), 0)
        except (TypeError, ValueError):
            offset = 0

        folder_query = self._filter_by_workspace(select(Folder), Folder)
        file_query = self._filter_by_workspace(select(File), File)

        scope_spec = parse_legacy_scope(scope)
        if not scope_spec.valid:
            logger.warning(f"[Security] Invalid scope parameter: {scope}")
            return {"items": [], "next_cursor": None, "has_more": False}

        if scope_spec.apply_filter:
            legacy_context = SimpleNamespace(
                user_id=user_id or "",
                data_scope=DataScopeLevel.ALL,
                dept_id=None,
                is_workspace_admin=is_workspace_admin,
            )
            folder_clause = build_visibility_where_clause(
                Folder,
                user_context=legacy_context,
                visibilities=scope_spec.visibilities,
                dept_ids=scope_spec.dept_ids,
                allow_private_without_owner=not bool(user_id),
            )
            file_clause = build_visibility_where_clause(
                File,
                user_context=legacy_context,
                visibilities=scope_spec.visibilities,
                dept_ids=scope_spec.dept_ids,
                allow_private_without_owner=not bool(user_id),
            )
            if folder_clause is None or file_clause is None:
                return {"items": [], "next_cursor": None, "has_more": False}
            folder_query = folder_query.where(folder_clause)
            file_query = file_query.where(file_clause)
        elif not scope and user_id and not is_workspace_admin:
            file_query = file_query.where(File.user_id == user_id)

        # 只拉取当前层级，不做递归
        if parent_id:
            folder_query = folder_query.where(Folder.parent_id == parent_id)
            file_query = file_query.where(File.folder_id == parent_id)
        else:
            folder_query = folder_query.where(Folder.parent_id.is_(None))
            file_query = file_query.where(File.folder_id.is_(None))

        folder_result = await self.db.execute(folder_query)
        folders = folder_result.scalars().all()

        file_result = await self.db.execute(file_query)
        files = file_result.scalars().all()

        nodes: List[Dict[str, Any]] = []
        for folder in folders:
            nodes.append(
                {
                    "id": folder.id,
                    "name": folder.name,
                    "type": "folder",
                    "children": [],
                }
            )
        for file in files:
            nodes.append(
                {
                    "id": file.id,
                    "name": file.name,
                    "type": "file",
                    "file_type": file.file_type,
                    "status": file.status,
                    "is_deleted": bool(getattr(file, "is_deleted", False)),
                    "delete_status": getattr(file, "delete_status", "active") or "active",
                    "delete_error": getattr(file, "delete_error", None),
                    "delete_op_id": getattr(file, "delete_op_id", None),
                    "description": file.description,
                    "pageindex_status": file.pageindex_status,
                }
            )

        nodes.sort(key=lambda item: (0 if item["type"] == "folder" else 1, item["name"], item["id"]))
        page = nodes[offset : offset + safe_limit + 1]
        has_more = len(page) > safe_limit
        items = page[:safe_limit]
        next_cursor = str(offset + safe_limit) if has_more else None
        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    async def _get_dept_tree_ids(self, dept_id: int, workspace_id: Optional[str]) -> List[int]:
        from app.models.auth.organization import DepartmentModel
        from sqlalchemy import select

        stmt = select(DepartmentModel.id).where(DepartmentModel.id == dept_id)
        if workspace_id:
            stmt = stmt.where(DepartmentModel.workspace_id == workspace_id)
        result = await self.db.execute(stmt)
        ids = [row[0] for row in result.fetchall()]

        stmt = select(DepartmentModel.id).where(
            DepartmentModel.ancestors.like(f"%/{dept_id}/%")
        )
        if workspace_id:
            stmt = stmt.where(DepartmentModel.workspace_id == workspace_id)
        result = await self.db.execute(stmt)
        ids.extend([row[0] for row in result.fetchall()])

        return list({int(i) for i in ids if i is not None})

    async def _build_visibility_clause(
        self,
        model,
        user_context,
        visibilities: Optional[List[str]] = None,
        dept_ids: Optional[List[int]] = None
    ):
        scope_dept_ids = await resolve_scope_dept_ids(
            user_context,
            descendants_loader=lambda dept_id: self._get_dept_tree_ids(
                dept_id,
                user_context.workspace_id,
            ),
        )
        return build_visibility_where_clause(
            model,
            user_context=user_context,
            scope_dept_ids=scope_dept_ids,
            visibilities=visibilities,
            dept_ids=dept_ids,
        )

    async def get_visible_structure(
        self,
        user_context,
        visibilities: Optional[List[str]] = None,
        dept_ids: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        folder_query = self._filter_by_workspace(select(Folder), Folder)
        file_query = self._filter_by_workspace(select(File), File)

        folder_clause = await self._build_visibility_clause(Folder, user_context, visibilities, dept_ids)
        file_clause = await self._build_visibility_clause(File, user_context, visibilities, dept_ids)
        if folder_clause is None or file_clause is None:
            return []

        if folder_clause is not None:
            folder_query = folder_query.where(folder_clause)
        else:
            return []

        if file_clause is not None:
            file_query = file_query.where(file_clause)
        else:
            return []

        folder_result = await self.db.execute(folder_query)
        folders = folder_result.scalars().all()

        file_result = await self.db.execute(file_query)
        files = file_result.scalars().all()

        folder_map = {
            f.id: {"id": f.id, "name": f.name, "type": "folder", "children": []}
            for f in folders
        }
        root_nodes = []

        for f in folders:
            node = folder_map[f.id]
            if f.parent_id and f.parent_id in folder_map:
                folder_map[f.parent_id]["children"].append(node)
            else:
                root_nodes.append(node)

        for file in files:
            file_node = {
                "id": file.id,
                "name": file.name,
                "type": "file",
                "file_type": file.file_type,
                "status": file.status,
                "is_deleted": bool(getattr(file, "is_deleted", False)),
                "delete_status": getattr(file, "delete_status", "active") or "active",
                "delete_error": getattr(file, "delete_error", None),
                "delete_op_id": getattr(file, "delete_op_id", None),
                "description": file.description,
                "pageindex_status": file.pageindex_status,
            }
            if file.folder_id and file.folder_id in folder_map:
                folder_map[file.folder_id]["children"].append(file_node)
            else:
                root_nodes.append(file_node)

        return root_nodes

    async def expand_folder_file_ids(
        self,
        folder_ids: List[str],
        include_subfolders: bool,
        user_context,
        visibilities: Optional[List[str]] = None,
        dept_ids: Optional[List[int]] = None
    ) -> List[str]:
        if not folder_ids:
            return []

        folder_clause = await self._build_visibility_clause(Folder, user_context, visibilities, dept_ids)
        file_clause = await self._build_visibility_clause(File, user_context, visibilities, dept_ids)

        all_folder_ids = {fid for fid in folder_ids if fid}
        if include_subfolders and all_folder_ids:
            pending = list(all_folder_ids)
            while pending:
                stmt = self._filter_by_workspace(select(Folder.id), Folder).where(Folder.parent_id.in_(pending))
                if folder_clause is not None:
                    stmt = stmt.where(folder_clause)
                result = await self.db.execute(stmt)
                child_ids = [row[0] for row in result.fetchall()]
                new_ids = [fid for fid in child_ids if fid and fid not in all_folder_ids]
                if not new_ids:
                    break
                all_folder_ids.update(new_ids)
                pending = new_ids

        if not all_folder_ids:
            return []

        stmt = self._filter_by_workspace(select(File.id), File).where(File.folder_id.in_(list(all_folder_ids)))
        if file_clause is not None:
            stmt = stmt.where(file_clause)
        result = await self.db.execute(stmt)
        return [row[0] for row in result.fetchall()]

    async def delete_folder(self, folder_id: str) -> bool:
        """删除文件夹（级联删除）"""
        await self._ensure_knowledge_action_allowed("delete")
        result = await self.db.execute(
            self._filter_by_workspace(select(Folder), Folder).where(Folder.id == folder_id)
        )
        folder = result.scalar_one_or_none()
        
        if folder:
            await self.db.delete(folder)
            await self.db.commit()
            return True
        return False

    async def delete_folders_batch(
        self, 
        folder_ids: List[str], 
        user_context: Any
    ) -> int:
        """
        批量删除文件夹（含子文件、子文件夹递归删除）
        
        注意：File.folder_id 外键约束为 SET NULL，需显式删除文件
        """
        if not folder_ids:
            return 0
        await self._ensure_knowledge_action_allowed("delete")
        
        # 1. 递归收集所有子文件夹 ID
        all_folder_ids = set(folder_ids)
        to_process = list(folder_ids)
        
        while to_process:
            result = await self.db.execute(
                self._filter_by_workspace(select(Folder.id), Folder).where(Folder.parent_id.in_(to_process))
            )
            child_ids = [row[0] for row in result.fetchall()]
            
            if not child_ids:
                break
            
            new_ids = [fid for fid in child_ids if fid not in all_folder_ids]
            all_folder_ids.update(new_ids)
            to_process = new_ids
        
        logger.info(f"[BatchDeleteFolders] 收集到 {len(all_folder_ids)} 个文件夹")
        
        # 2. 收集这些文件夹下的所有文件 ID
        file_result = await self.db.execute(
            self._filter_by_workspace(select(File.id), File).where(File.folder_id.in_(all_folder_ids))
        )
        file_ids = [row[0] for row in file_result.fetchall()]
        
        # 3. 手动批量删除文件
        if file_ids:
            await self.delete_files_batch(file_ids, user_context)
            logger.info(f"[BatchDeleteFolders] 已删除 {len(file_ids)} 个文件")
        
        # 4. 批量删除文件夹
        delete_stmt = delete(Folder).where(Folder.id.in_(all_folder_ids))
        ws_id = get_current_workspace()
        if ws_id:
            delete_stmt = delete_stmt.where(Folder.workspace_id == ws_id)
        await self.db.execute(delete_stmt)
        await self.db.commit()
        
        logger.info(f"[BatchDeleteFolders] 已删除 {len(all_folder_ids)} 个文件夹")
        return len(all_folder_ids)

    async def rename_folder(self, folder_id: str, new_name: str) -> bool:
        """重命名文件夹"""
        await self._ensure_knowledge_action_allowed("rename")
        result = await self.db.execute(
            self._filter_by_workspace(select(Folder), Folder).where(Folder.id == folder_id)
        )
        folder = result.scalar_one_or_none()
        if folder:
            folder.name = new_name
            await self.db.commit()
            return True
        return False

    async def move_folder(self, folder_id: str, new_parent_id: Optional[str]) -> bool:
        """移动文件夹"""
        await self._ensure_knowledge_action_allowed("move")
        if folder_id == new_parent_id:
            return False
            
        result = await self.db.execute(
            self._filter_by_workspace(select(Folder), Folder).where(Folder.id == folder_id)
        )
        folder = result.scalar_one_or_none()
        if folder:
            folder.parent_id = new_parent_id
            await self.db.commit()
            return True
        return False

    # ================= 文件操作 =================

    async def create_file_record(self, file_data: dict, *, commit: bool = True) -> File:
        """创建文件记录"""
        if "storage_path" in file_data and file_data["storage_path"]:
            file_data["storage_path"] = normalize_storage_path(str(file_data["storage_path"]))
        file_record = File(**file_data)
        self.db.add(file_record)
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()
        return file_record

    async def get_file(self, file_id: str) -> Optional[File]:
        """获取文件记录"""
        result = await self.db.execute(
            self._filter_by_workspace(select(File), File).where(File.id == file_id)
        )
        return result.scalar_one_or_none()

    async def find_by_name(self, name: str) -> Optional[File]:
        """按名称查找文件"""
        result = await self.db.execute(
            self._filter_by_workspace(select(File), File).where(File.name == name)
        )
        return result.scalar_one_or_none()

    async def update_file_status(
        self, 
        file_id: str, 
        status: str, 
        chunk_count: int = 0, 
        error: str = None,
        description: str = None
    ):
        """更新文件状态"""
        result = await self.db.execute(
            self._filter_by_workspace(select(File), File).where(File.id == file_id)
        )
        file_record = result.scalar_one_or_none()
        
        if file_record:
            file_record.status = status
            if chunk_count > 0:
                file_record.chunk_count = chunk_count
            if error is not None:
                file_record.error_message = error
            elif status != DocumentStatus.ERROR.value:
                # 进入 processing/indexed 时清空旧错误，避免残留取消标记或历史错误干扰。
                file_record.error_message = None
            if description:
                file_record.description = description
            
            if status == DocumentStatus.INDEXED.value:
                file_record.processed_at = datetime.now()
            await self.db.commit()

    async def rename_file(self, file_id: str, new_name: str) -> bool:
        """重命名文件"""
        await self._ensure_knowledge_action_allowed("rename")
        result = await self.db.execute(
            self._filter_by_workspace(select(File), File).where(File.id == file_id)
        )
        file_record = result.scalar_one_or_none()
        if file_record:
            file_record.name = new_name
            await self.db.commit()
            
            # 同步 ChromaDB
            try:
                await self._batch_update_chroma_metadata(
                    file_id, 
                    update_field="source_file", 
                    update_value=new_name
                )
            except Exception as e:
                logger.error(f"Chroma rename failed: {e}")
            return True
        return False

    async def move_file(self, file_id: str, new_folder_id: Optional[str]) -> bool:
        """移动文件"""
        await self._ensure_knowledge_action_allowed("move")
        result = await self.db.execute(
            self._filter_by_workspace(select(File), File).where(File.id == file_id)
        )
        file_record = result.scalar_one_or_none()
        if file_record:
            file_record.folder_id = new_folder_id
            await self.db.commit()
            return True
        return False

    async def update_pageindex_status(
        self,
        file_id: str,
        status: Optional[str],
        error: Optional[str] = None
    ) -> bool:
        """
        [DEPRECATED] PageIndex 功能已禁用
        """
        result = await self.db.execute(
            self._filter_by_workspace(select(File), File).where(File.id == file_id)
        )
        file_record = result.scalar_one_or_none()
        if not file_record:
            return False

        file_record.pageindex_status = status
        file_record.pageindex_error = error
        await self.db.commit()
        return True

    async def update_file_description(self, file_id: str, description: str) -> bool:
        """更新文件描述 -> 同步 ChromaDB"""
        result = await self.db.execute(
            self._filter_by_workspace(select(File), File).where(File.id == file_id)
        )
        file_record = result.scalar_one_or_none()
        
        if not file_record:
            return False

        file_record.description = description
        await self.db.commit()

        try:
            await self._batch_update_chroma_metadata(
                file_id,
                update_field="description",
                update_value=description
            )
        except Exception as e:
            logger.error(f"Failed to sync Chroma metadata: {e}")

        return True

    async def delete_file(self, file_id: str, user_context: Any) -> Dict[str, Any]:
        """
        软删除文件（状态机：active -> pending_delete -> vector_deleted）。
        """
        await self._ensure_knowledge_action_allowed("delete")
        result = await self.db.execute(
            self._filter_by_workspace(select(File), File, include_deleted=True).where(File.id == file_id)
        )
        file_record = result.scalar_one_or_none()
        if not file_record:
            return {
                "success": False,
                "delete_status": "not_found",
                "delete_op_id": None,
                "file_id": file_id,
            }

        delete_service = FileDeleteService(self.db, self.doc_skill)
        delete_result = await delete_service.delete(file_record, user_context)
        return {
            "success": delete_result.success,
            "delete_status": delete_result.delete_status,
            "delete_op_id": delete_result.delete_op_id,
            "file_id": delete_result.file_id,
        }

    async def delete_files_batch(self, file_ids: List[str], user_context: Any) -> int:
        """批量软删除，仅统计进入 vector_deleted 的记录数。"""
        if not file_ids:
            return 0
        await self._ensure_knowledge_action_allowed("delete")
        
        result = await self.db.execute(
            self._filter_by_workspace(select(File), File, include_deleted=True).where(File.id.in_(file_ids))
        )
        file_records = result.scalars().all()
        if not file_records:
            return 0

        delete_service = FileDeleteService(self.db, self.doc_skill)
        deleted_count = 0
        for file_record in file_records:
            single_result = await delete_service.delete(file_record, user_context)
            if single_result.success:
                deleted_count += 1

        logger.info(
            "[BatchDelete] soft-delete completed: requested=%s found=%s success=%s",
            len(file_ids),
            len(file_records),
            deleted_count,
        )
        return deleted_count

    # ================= Chroma 同步辅助 =================

    async def _batch_update_chroma_metadata(
        self, 
        file_id: str, 
        update_field: str, 
        update_value: str,
        batch_size: Optional[int] = None
    ):
        """分批更新 ChromaDB 元数据"""
        from app.config import get_settings
        
        if batch_size is None:
            batch_size = get_settings().chroma.batch_size
            
        collection = self.doc_skill.collection
        
        c_result = collection.get(
            where={"file_id": file_id},
            include=["metadatas"]
        )
        
        if not c_result or not c_result["ids"]:
            return
        
        ids = c_result["ids"]
        metadatas = c_result["metadatas"]
        total = len(ids)
        
        logger.info(f"[ChromaBatch] 更新 {total} 个切片的 {update_field} 字段")
        
        for i in range(0, total, batch_size):
            batch_ids = ids[i:i + batch_size]
            batch_metas = metadatas[i:i + batch_size]
            
            new_metas = []
            for meta in batch_metas:
                meta[update_field] = update_value
                new_metas.append(meta)
            
            collection.update(ids=batch_ids, metadatas=new_metas)
            
            if total > batch_size:
                logger.info(f"[ChromaBatch] 已更新 {min(i + batch_size, total)}/{total}")

    # ================= 节点位置 =================

    async def update_node_position(self, node_id: str, x: float, y: float) -> bool:
        """更新节点位置（Folder 或 File）"""
        # 尝试更新 Folder
        result = await self.db.execute(
            self._filter_by_workspace(select(Folder), Folder).where(Folder.id == node_id)
        )
        folder = result.scalar_one_or_none()
        if folder:
            folder.x = x
            folder.y = y
            await self.db.commit()
            return True
            
        # 尝试更新 File
        result = await self.db.execute(
            self._filter_by_workspace(select(File), File).where(File.id == node_id)
        )
        file_record = result.scalar_one_or_none()
        if file_record:
            file_record.x = x
            file_record.y = y
            await self.db.commit()
            return True
            
        return False
