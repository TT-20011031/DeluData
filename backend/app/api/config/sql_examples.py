"""
SQL 示例配置 API (Router 层)

SQL 示例 CRUD、批量操作、分组管理
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends

from app.api.deps import get_current_admin, get_current_query_user
from app.core.security.auth import User
from app.models.config.sql_example import (
    SqlExample, SqlExampleCreate, SqlExampleUpdate,
    SqlExampleValidationData,
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
    SqlExampleValidationRequest,
    SqlExampleValidationResponse,
    SqlExampleOwnerClaimRequest,
)
from app.services.sql_example_service import claim_orphan_sql_example, validate_sql_example

logger = logging.getLogger(__name__)

router = APIRouter()


def _example_response(example: SqlExample, *, include_owner: bool = False) -> SqlExampleResponse:
    return SqlExampleResponse(
        id=int(example.id or 0),
        question=example.question,
        sql=example.sql,
        description=example.description,
        tables=example.tables,
        is_active=example.is_active,
        group_id=example.group_id,
        owner_id=example.created_by if include_owner else None,
        validation_status=example.validation_status,
        validation_errors=example.validation_errors,
        parameters=example.parameters,
        normalized_question=example.normalized_question,
        validated_at=example.validated_at.isoformat() if example.validated_at else None,
        last_matched_at=example.last_matched_at.isoformat() if example.last_matched_at else None,
        match_count=example.match_count,
        created_at=example.created_at.isoformat(),
        updated_at=example.updated_at.isoformat(),
    )


# ========== SQL 示例 API ==========

@router.post("/sql-examples", response_model=SqlExampleResponse, summary="创建SQL示例")
async def create_sql_example(
    request: SqlExampleCreate,
    user: User = Depends(get_current_query_user)
):
    """创建当前账号私有 SQL 示例。无效内容会保存为不可启用的草稿。"""
    if request.group_id is not None:
        group = await get_sql_example_group_async(request.group_id)
        if not group or group.workspace_id != user.workspace_id or group.created_by != user.id:
            raise HTTPException(status_code=403, detail="所属分组不属于当前账号")
    validation = await validate_sql_example(
        question=request.question,
        sql=request.sql,
        workspace_id=user.workspace_id,
        user_id=user.id,
        parameters=request.parameters,
    )
    if not request.is_active and validation.status == "valid":
        validation.status = "draft"
    example = await create_sql_example_async(
        example=request,
        workspace_id=user.workspace_id,
        user_id=user.id,
        validation=validation,
    )
    return _example_response(example)


@router.get("/sql-examples", response_model=SqlExampleListResponse, summary="获取SQL示例列表")
async def list_sql_examples(
    limit: int = 50,
    offset: int = 0,
    group_id: Optional[int] = None,
    user: User = Depends(get_current_query_user)
):
    """仅返回当前账号自己的 SQL 示例。"""
    limit = min(limit, 100)
    
    result = await list_sql_examples_async(
        workspace_id=user.workspace_id,
        owner_id=user.id,
        active_only=False,
        group_id=group_id,
        limit=limit,
        offset=offset
    )
    
    return SqlExampleListResponse(
        examples=[_example_response(e) for e in result["items"]],
        total=result["total"]
    )


@router.post(
    "/sql-examples/validate",
    response_model=SqlExampleValidationResponse,
    summary="校验SQL示例并识别动态参数",
)
async def validate_sql_example_endpoint(
    request: SqlExampleValidationRequest,
    user: User = Depends(get_current_query_user),
):
    result = await validate_sql_example(
        question=request.question,
        sql=request.sql,
        workspace_id=user.workspace_id,
        user_id=user.id,
        parameters=request.parameters,
    )
    return SqlExampleValidationResponse(
        status=result.status,
        errors=result.errors,
        parameters=result.parameters,
        normalized_question=result.normalized_question,
        preview_sql=result.preview_sql,
    )


@router.get("/sql-examples/audit", response_model=SqlExampleListResponse, summary="审计SQL示例")
async def audit_sql_examples(
    owner_id: Optional[str] = None,
    validation_status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    admin: User = Depends(get_current_admin),
):
    result = await list_sql_examples_async(
        workspace_id=admin.workspace_id,
        owner_id=owner_id,
        validation_status=validation_status,
        limit=min(limit, 100),
        offset=offset,
    )
    return SqlExampleListResponse(
        examples=[_example_response(item, include_owner=True) for item in result["items"]],
        total=result["total"],
    )


@router.post(
    "/sql-examples/{example_id}/claim",
    response_model=SqlExampleResponse,
    summary="为孤立SQL示例分配归属账号",
)
async def claim_sql_example_owner(
    example_id: int,
    request: SqlExampleOwnerClaimRequest,
    admin: User = Depends(get_current_admin),
):
    try:
        example = await claim_orphan_sql_example(
            example_id=example_id,
            workspace_id=admin.workspace_id,
            owner_id=request.owner_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not example:
        raise HTTPException(status_code=404, detail="SQL 示例不存在")
    return _example_response(example, include_owner=True)


@router.get("/sql-examples/{example_id}", response_model=SqlExampleResponse, summary="获取单个SQL示例")
async def get_sql_example(
    example_id: int,
    user: User = Depends(get_current_query_user)
):
    """获取当前账号的单个 SQL 示例。"""
    example = await get_sql_example_async(example_id)
    
    if not example:
        raise HTTPException(status_code=404, detail="SQL 示例不存在")
    
    if example.workspace_id != user.workspace_id or example.created_by != user.id:
        raise HTTPException(status_code=403, detail="无权访问此示例")
    return _example_response(example)


@router.put("/sql-examples/{example_id}", response_model=SqlExampleResponse, summary="更新SQL示例")
async def update_sql_example(
    example_id: int,
    request: SqlExampleUpdate,
    user: User = Depends(get_current_query_user)
):
    """更新当前账号自己的 SQL 示例。"""
    existing = await get_sql_example_async(example_id)
    if not existing:
        raise HTTPException(status_code=404, detail="SQL 示例不存在")
    if existing.workspace_id != user.workspace_id or existing.created_by != user.id:
        raise HTTPException(status_code=403, detail="无权修改此示例")
    if request.group_id is not None:
        group = await get_sql_example_group_async(request.group_id)
        if not group or group.workspace_id != user.workspace_id or group.created_by != user.id:
            raise HTTPException(status_code=403, detail="所属分组不属于当前账号")

    validation: Optional[SqlExampleValidationData] = None
    should_validate = any(
        value is not None
        for value in (request.question, request.sql, request.parameters)
    ) or request.is_active is True
    if should_validate:
        validation = await validate_sql_example(
            question=request.question if request.question is not None else existing.question,
            sql=request.sql if request.sql is not None else existing.sql,
            workspace_id=user.workspace_id,
            user_id=user.id,
            parameters=request.parameters if request.parameters is not None else existing.parameters,
        )
        if request.is_active is not True and validation.status == "valid":
            validation.status = "draft"
    example = await update_sql_example_async(example_id, request, validation=validation)
    return _example_response(example)


@router.delete("/sql-examples/{example_id}", summary="删除SQL示例")
async def delete_sql_example(
    example_id: int,
    user: User = Depends(get_current_query_user)
):
    """删除当前账号自己的 SQL 示例。"""
    existing = await get_sql_example_async(example_id)
    if not existing:
        raise HTTPException(status_code=404, detail="SQL 示例不存在")
    if existing.workspace_id != user.workspace_id or existing.created_by != user.id:
        raise HTTPException(status_code=403, detail="无权删除此示例")
    
    success = await delete_sql_example_async(example_id)
    
    if not success:
        raise HTTPException(status_code=500, detail="删除失败")
    
    return {"success": True, "message": "SQL 示例已删除"}


# ========== 批量操作 API ==========

@router.post("/sql-examples/batch-delete", response_model=BatchOperationResponse, summary="批量删除SQL示例")
async def batch_delete_sql_examples(
    request: BatchDeleteRequest,
    user: User = Depends(get_current_query_user)
):
    """批量删除当前账号自己的 SQL 示例。"""
    if not request.ids:
        raise HTTPException(status_code=400, detail="请选择要删除的示例")
    
    if len(request.ids) > 100:
        raise HTTPException(status_code=400, detail="单次最多删除100条")
    
    deleted_count = await batch_delete_sql_examples_async(
        example_ids=request.ids,
        workspace_id=user.workspace_id,
        owner_id=user.id,
    )
    
    return BatchOperationResponse(
        success=True,
        affected_count=deleted_count,
        message=f"成功删除 {deleted_count} 条 SQL 示例"
    )


@router.post("/sql-examples/batch-move", response_model=BatchOperationResponse, summary="批量移动SQL示例到分组")
async def batch_move_sql_examples(
    request: BatchMoveRequest,
    user: User = Depends(get_current_query_user)
):
    """批量移动当前账号自己的 SQL 示例。"""
    if not request.ids:
        raise HTTPException(status_code=400, detail="请选择要移动的示例")
    if request.group_id is not None:
        group = await get_sql_example_group_async(request.group_id)
        if not group or group.workspace_id != user.workspace_id or group.created_by != user.id:
            raise HTTPException(status_code=403, detail="目标分组不属于当前账号")
    
    updated_count = await batch_move_to_group_async(
        example_ids=request.ids,
        group_id=request.group_id,
        workspace_id=user.workspace_id,
        owner_id=user.id,
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
    user: User = Depends(get_current_query_user)
):
    """创建当前账号私有分组。"""
    group = await create_sql_example_group_async(
        group=request,
        workspace_id=user.workspace_id,
        user_id=user.id
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
    user: User = Depends(get_current_query_user)
):
    """获取当前账号的 SQL 示例分组。"""
    groups = await list_sql_example_groups_async(user.workspace_id, user.id)
    
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
    user: User = Depends(get_current_query_user)
):
    """更新当前账号自己的 SQL 示例分组。"""
    existing = await get_sql_example_group_async(group_id)
    if not existing:
        raise HTTPException(status_code=404, detail="分组不存在")
    if existing.workspace_id != user.workspace_id or existing.created_by != user.id:
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
    user: User = Depends(get_current_query_user)
):
    """删除当前账号自己的 SQL 示例分组。"""
    existing = await get_sql_example_group_async(group_id)
    if not existing:
        raise HTTPException(status_code=404, detail="分组不存在")
    if existing.workspace_id != user.workspace_id or existing.created_by != user.id:
        raise HTTPException(status_code=403, detail="无权删除此分组")
    
    success = await delete_sql_example_group_async(
        group_id,
        workspace_id=user.workspace_id,
        owner_id=user.id,
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="删除失败")
    
    return {"success": True, "message": "分组已删除，组内示例已移至未分组"}
