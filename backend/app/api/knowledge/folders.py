"""
知识库 API - 文件夹路由

处理文件夹的 CRUD 操作
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security.auth import User
from app.core.security.rbac_deps import CheckPerm
from app.services.filesystem_service import FilesystemService
get_current_admin = CheckPerm("knowledge:manage")

from .deps import get_user_context, get_filesystem_service
from .schemas import FolderCreate, BatchDeleteRequest, RenameRequest, MoveRequest
from app.models.common.context import UserContext

router = APIRouter(tags=["folders"])
logger = logging.getLogger(__name__)


class VisibleStructureRequest(BaseModel):
    visibilities: Optional[List[str]] = None
    dept_ids: Optional[List[int]] = None


class ExpandFolderRequest(BaseModel):
    folder_ids: List[str]
    include_subfolders: bool = False
    visibilities: Optional[List[str]] = None
    dept_ids: Optional[List[int]] = None


class ExpandFolderResponse(BaseModel):
    file_ids: List[str]


@router.post("/folders")
async def create_folder(
    folder: FolderCreate,
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """创建文件夹"""
    return await filesystem.create_folder(
        name=folder.name,
        parent_id=folder.parent_id,
        user_id=user_context.user_id,
        visibility=folder.visibility,
        dept_id=folder.dept_id or user_context.dept_id,
        owner_id=user_context.user_id
    )


@router.get("/structure")
async def get_structure(
    scope: Optional[str] = None,
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """获取文件夹树结构"""
    return await filesystem.get_folder_structure(
        user_id=user_context.user_id,
        scope=scope,
        is_workspace_admin=user_context.is_workspace_admin,
    )


@router.post("/structure/visible")
async def get_visible_structure(
    request: VisibleStructureRequest,
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """获取用户可见范围的文件树结构"""
    return await filesystem.get_visible_structure(
        user_context=user_context,
        visibilities=request.visibilities,
        dept_ids=request.dept_ids
    )


@router.get("/structure/nodes")
async def get_structure_nodes(
    scope: Optional[str] = None,
    parent_id: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=500),
    cursor: Optional[str] = None,
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service),
):
    """按层分页获取节点，供前端树形懒加载使用。"""
    return await filesystem.get_structure_nodes(
        user_id=user_context.user_id,
        scope=scope,
        parent_id=parent_id or None,
        limit=limit,
        cursor=cursor,
        is_workspace_admin=user_context.is_workspace_admin,
    )


@router.post("/folders/expand", response_model=ExpandFolderResponse)
async def expand_folders(
    request: ExpandFolderRequest,
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """展开文件夹为文件 ID 列表"""
    file_ids = await filesystem.expand_folder_file_ids(
        folder_ids=request.folder_ids,
        include_subfolders=request.include_subfolders,
        user_context=user_context,
        visibilities=request.visibilities,
        dept_ids=request.dept_ids
    )
    return ExpandFolderResponse(file_ids=file_ids)


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """删除文件夹"""
    success = await filesystem.delete_folder(folder_id)
    if not success:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    return {"success": True}


@router.post("/folders/batch-delete")
async def batch_delete_folders(
    request: BatchDeleteRequest,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """批量删除文件夹"""
    user_context = UserContext(
        user_id=current_user.id,
        workspace_id=current_user.workspace_id,
        allowed_tables=["*"],
        role=current_user.role,
        is_workspace_admin=current_user.is_workspace_admin,
    )
    
    deleted_count = await filesystem.delete_folders_batch(request.ids, user_context)
    
    return {
        "success": True, 
        "deleted_count": deleted_count,
        "requested_count": len(request.ids)
    }


@router.put("/folders/{folder_id}/name")
async def rename_folder(
    folder_id: str,
    request: RenameRequest,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """重命名文件夹"""
    success = await filesystem.rename_folder(folder_id, request.name)
    if not success:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    return {"success": True}


@router.put("/folders/{folder_id}/move")
async def move_folder(
    folder_id: str,
    request: MoveRequest,
    current_user: User = Depends(get_current_admin),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """移动文件夹"""
    success = await filesystem.move_folder(folder_id, request.parent_id)
    if not success:
        raise HTTPException(status_code=400, detail="移动失败")
    return {"success": True}
