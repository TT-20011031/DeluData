"""
知识库 API - 关系与图谱路由

处理文件关系管理和图谱数据
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.security.auth import User
from app.core.security.rbac_deps import CheckPerm
from app.services.graph_service import GraphService
get_current_admin = CheckPerm("knowledge:manage")
from app.services.filesystem_service import FilesystemService

from .deps import get_user_context, get_graph_service, get_filesystem_service
from .schemas import RelationCreate, UpdatePositionRequest
from app.models.common.context import UserContext

router = APIRouter(tags=["relationships"])
logger = logging.getLogger(__name__)


@router.post("/relationships")
async def create_relationship(
    relation: RelationCreate,
    current_user: User = Depends(get_current_admin),
    graph: GraphService = Depends(get_graph_service)
):
    """创建文档关联"""
    try:
        rel = await graph.create_relationship(
            source_id=relation.source_id,
            target_id=relation.target_id,
            relation_type=relation.relation_type,
            workspace_id=current_user.workspace_id
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"id": rel.id, "type": rel.relation_type}


@router.delete("/relationships")
async def delete_relationship(
    source_id: str, 
    target_id: str,
    current_user: User = Depends(get_current_admin),
    graph: GraphService = Depends(get_graph_service)
):
    """删除关系"""
    success = await graph.delete_relationship(
        source_id,
        target_id,
        workspace_id=current_user.workspace_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="关系不存在")
    return {"success": True}


@router.get("/graph")
async def get_graph_data(
    user_context: UserContext = Depends(get_user_context),
    graph: GraphService = Depends(get_graph_service)
):
    """获取图谱数据"""
    return await graph.get_graph_data(
        user_id=user_context.user_id,
        workspace_id=user_context.workspace_id
    )


@router.put("/nodes/{node_id}/position")
async def update_node_position(
    node_id: str,
    request: UpdatePositionRequest,
    user_context: UserContext = Depends(get_user_context),
    filesystem: FilesystemService = Depends(get_filesystem_service)
):
    """更新节点位置"""
    success = await filesystem.update_node_position(node_id, request.x, request.y)
    if not success:
        raise HTTPException(status_code=404, detail="节点不存在")
    return {"success": True}
