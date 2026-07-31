"""
SQL 示例配置 API (Router 层)

SQL 示例 CRUD、批量操作、分组管理
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends

from app.api.deps import get_current_admin
from app.core.security.auth import User
from app.models.config.sql_example import (
    SqlExample, SqlExampleCreate, SqlExampleUpdate,
    create_sql_example_async, get_sql_example_async, list_sql_examples_async,
    update_sql_example_async, delete_sql_example_async,
    batch_delete_sql_examples_async, batch_move_to_group_async,
)
from app.models.config.sql_example_group import (
    SqlExampleGroup, SqlExampleGroupCreate, SqlExampleGroupUpdate,
    create_sql_example_group_async, list_sql_example_groups_async,
    get_sql_example_group_async, update_sql_example_group_async, delete_sql_example_group_async,
)
from .schemas import (
    SqlExampleResponse,
    SqlExampleListResponse,
    SqlGroupResponse,
    SqlGroupListResponse,
    BatchDeleteRequest,
    BatchMoveRequest,
    BatchOperationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ========== SQL 示例 API ==========

@router.post("/sql-examples", response_model=SqlExampleResponse, summary="创建SQL示例")
async def create_sql_example(
    request: SqlExampleCreate,
    admin: User = Depends(get_current_admin)
):
    """创建 SQL 示例配置（仅管理员）"""
    example = await create_sql_example_async(
        example=request,
        workspace_id=admin.workspace_id,
        user_id=admin.id
    )
    
    return SqlExampleResponse(
        id=example.id,
        question=example.question,
        sql=example.sql,
        description=example.description,
        tables=example.tables,
        is_active=example.is_active,
        created_at=example.created_at.isoformat(),
        updated_at=example.updated_at.isoformat()
    )


@router.get("/sql-examples", response_model=SqlExampleListResponse, summary="获取SQL示例列表")
async def list_sql_examples(
    limit: int = 50,
    offset: int = 0,
    group_id: Optional[int] = None,
    admin: User = Depends(get_current_admin)
):
    """获取 SQL 示例配置（仅管理员，支持分页）"""
    limit = min(limit, 100)
    
    result = await list_sql_examples_async(
        workspace_id=admin.workspace_id,
        active_only=False,
        group_id=group_id,
        limit=limit,
        offset=offset
    )
    
    return SqlExampleListResponse(
        examples=[
            SqlExampleResponse(
                id=e.id,
                question=e.question,
                sql=e.sql,
                description=e.description,
                tables=e.tables,
                is_active=e.is_active,
                group_id=e.group_id,
                created_at=e.created_at.isoformat(),
                updated_at=e.updated_at.isoformat()
            )
            for e in result["items"]
        ],
        total=result["total"]
    )


@router.get("/sql-examples/{example_id}", response_model=SqlExampleResponse, summary="获取单个SQL示例")
async def get_sql_example(
    example_id: int,
    admin: User = Depends(get_current_admin)
):
    """获取单个 SQL 示例详情（仅管理员）"""
    example = await get_sql_example_async(example_id)
    
    if not example:
        raise HTTPException(status_code=404, detail="SQL 示例不存在")
    
    if example.workspace_id != admin.workspace_id:
        raise HTTPException(status_code=403, detail="无权访问此示例")
    
    return SqlExampleResponse(
        id=example.id,
        question=example.question,
        sql=example.sql,
        description=example.description,
        tables=example.tables,
        is_active=example.is_active,
        created_at=example.created_at.isoformat(),
        updated_at=example.updated_at.isoformat()
    )


@router.put("/sql-examples/{example_id}", response_model=SqlExampleResponse, summary="更新SQL示例")
async def update_sql_example(
    example_id: int,
    request: SqlExampleUpdate,
    admin: User = Depends(get_current_admin)
):
    """更新 SQL 示例配置（仅管理员）"""
    existing = await get_sql_example_async(example_id)
    if not existing:
        raise HTTPException(status_code=404, detail="SQL 示例不存在")
    if existing.workspace_id != admin.workspace_id:
        raise HTTPException(status_code=403, detail="无权修改此示例")
    
    example = await update_sql_example_async(example_id, request)
    
    return SqlExampleResponse(
        id=example.id,
        question=example.question,
        sql=example.sql,
        description=example.description,
        tables=example.tables,
        is_active=example.is_active,
        created_at=example.created_at.isoformat(),
        updated_at=example.updated_at.isoformat()
    )


@router.delete("/sql-examples/{example_id}", summary="删除SQL示例")
async def delete_sql_example(
    example_id: int,
    admin: User = Depends(get_current_admin)
):
    """删除 SQL 示例配置（仅管理员）"""
    existing = await get_sql_example_async(example_id)
    if not existing:
        raise HTTPException(status_code=404, detail="SQL 示例不存在")
    if existing.workspace_id != admin.workspace_id:
        raise HTTPException(status_code=403, detail="无权删除此示例")
    
    success = await delete_sql_example_async(example_id)
    
    if not success:
        raise HTTPException(status_code=500, detail="删除失败")
    
    return {"success": True, "message": "SQL 示例已删除"}


# ========== 批量操作 API ==========

@router.post("/sql-examples/batch-delete", response_model=BatchOperationResponse, summary="批量删除SQL示例")
async def batch_delete_sql_examples(
    request: BatchDeleteRequest,
    admin: User = Depends(get_current_admin)
):
    """批量删除 SQL 示例（仅管理员）"""
    if not request.ids:
        raise HTTPException(status_code=400, detail="请选择要删除的示例")
    
    if len(request.ids) > 100:
        raise HTTPException(status_code=400, detail="单次最多删除100条")
    
    deleted_count = await batch_delete_sql_examples_async(
        example_ids=request.ids,
        workspace_id=admin.workspace_id
    )
    
    return BatchOperationResponse(
        success=True,
        affected_count=deleted_count,
        message=f"成功删除 {deleted_count} 条 SQL 示例"
    )


@router.post("/sql-examples/batch-move", response_model=BatchOperationResponse, summary="批量移动SQL示例到分组")
async def batch_move_sql_examples(
    request: BatchMoveRequest,
    admin: User = Depends(get_current_admin)
):
    """批量移动 SQL 示例到指定分组（仅管理员）"""
    if not request.ids:
        raise HTTPException(status_code=400, detail="请选择要移动的示例")
    
    updated_count = await batch_move_to_group_async(
        example_ids=request.ids,
        group_id=request.group_id,
        workspace_id=admin.workspace_id
    )
    
    group_name = "未分组" if request.group_id is None else f"分组 {request.group_id}"
    return BatchOperationResponse(
        success=True,
        affected_count=updated_count,
        message=f"成功移动 {updated_count} 条 SQL 示例到 {group_name}"
    )


# ========== 分组管理 API ==========

@router.post("/sql-groups", response_model=SqlGroupResponse, summary="创建SQL示例分组")
async def create_sql_group(
    request: SqlExampleGroupCreate,
    admin: User = Depends(get_current_admin)
):
    """创建 SQL 示例分组（仅管理员）"""
    group = await create_sql_example_group_async(
        group=request,
        workspace_id=admin.workspace_id,
        user_id=admin.id
    )
    
    return SqlGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        color=group.color,
        example_count=group.example_count,
        created_at=group.created_at.isoformat(),
        updated_at=group.updated_at.isoformat()
    )


@router.get("/sql-groups", response_model=SqlGroupListResponse, summary="获取SQL示例分组列表")
async def list_sql_groups(
    admin: User = Depends(get_current_admin)
):
    """获取所有 SQL 示例分组（仅管理员）"""
    groups = await list_sql_example_groups_async(admin.workspace_id)
    
    return SqlGroupListResponse(
        groups=[
            SqlGroupResponse(
                id=g.id,
                name=g.name,
                description=g.description,
                color=g.color,
                example_count=g.example_count,
                created_at=g.created_at.isoformat(),
                updated_at=g.updated_at.isoformat()
            )
            for g in groups
        ],
        total=len(groups)
    )


@router.put("/sql-groups/{group_id}", response_model=SqlGroupResponse, summary="更新SQL示例分组")
async def update_sql_group(
    group_id: int,
    request: SqlExampleGroupUpdate,
    admin: User = Depends(get_current_admin)
):
    """更新 SQL 示例分组（仅管理员）"""
    existing = await get_sql_example_group_async(group_id)
    if not existing:
        raise HTTPException(status_code=404, detail="分组不存在")
    if existing.workspace_id != admin.workspace_id:
        raise HTTPException(status_code=403, detail="无权修改此分组")
    
    group = await update_sql_example_group_async(group_id, request)
    
    return SqlGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        color=group.color,
        example_count=group.example_count,
        created_at=group.created_at.isoformat(),
        updated_at=group.updated_at.isoformat()
    )


@router.delete("/sql-groups/{group_id}", summary="删除SQL示例分组")
async def delete_sql_group(
    group_id: int,
    admin: User = Depends(get_current_admin)
):
    """删除 SQL 示例分组（仅管理员）"""
    existing = await get_sql_example_group_async(group_id)
    if not existing:
        raise HTTPException(status_code=404, detail="分组不存在")
    if existing.workspace_id != admin.workspace_id:
        raise HTTPException(status_code=403, detail="无权删除此分组")
    
    success = await delete_sql_example_group_async(group_id)
    
    if not success:
        raise HTTPException(status_code=500, detail="删除失败")
    
    return {"success": True, "message": "分组已删除，组内示例已移至未分组"}
