"""
DeluData 智能问数系统 - RBAC 角色管理 API

角色 CRUD、权限配置、用户角色分配
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.db.database import get_async_db
from app.models.auth.rbac import RoleModel, PermissionModel, UserModel, user_roles
from app.core.security.rbac_init import get_permissions_by_module, normalize_system_role_permissions
from app.core.security.rbac_deps import CheckPerm
from app.core.security.auth import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rbac", tags=["RBAC权限管理"])


# ========== 请求/响应模型 ==========

def _legacy_rbac_api_gone():
    raise HTTPException(
        status_code=410,
        detail={"code": "legacy_api_gone", "replacement": "/api/authorization"},
    )


router.dependencies.append(Depends(_legacy_rbac_api_gone))


class PermissionInfo(BaseModel):
    """权限信息"""
    id: int
    code: str
    module: str
    description: Optional[str]

class RoleInfo(BaseModel):
    """角色信息"""
    id: int
    name: str
    description: Optional[str]
    workspace_id: Optional[str]
    is_system: bool
    permissions: List[str]  # 权限码列表
    data_scope: int = 4  # 数据范围 (1=全部, 2=本部门及以下, 3=本部门, 4=仅本人)

class RoleListResponse(BaseModel):
    """角色列表响应"""
    roles: List[RoleInfo]

class CreateRoleRequest(BaseModel):
    """创建角色请求"""
    name: str
    description: Optional[str] = None
    permissions: List[str] = []  # 权限码列表
    data_scope: int = 4  # 数据范围 (1=全部, 2=本部门及以下, 3=本部门, 4=仅本人)

class UpdateRoleRequest(BaseModel):
    """更新角色请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None
    data_scope: Optional[int] = None  # 数据范围

class AssignRolesRequest(BaseModel):
    """分配角色请求"""
    role_ids: List[int]

class UserWithRolesInfo(BaseModel):
    """用户角色信息 (纯 RBAC)"""
    id: str
    username: str
    email: Optional[str]
    workspace_id: str
    is_admin: bool  # 通过角色判断
    roles: List[RoleInfo]
    permissions: List[str]


# ========== 权限查询 API ==========

@router.get("/permissions", response_model=List[PermissionInfo])
async def list_permissions(
    _: User = Depends(CheckPerm("sys:user:view")),  # 需要查看成员权限
    db: AsyncSession = Depends(get_async_db)
):
    """获取所有权限列表"""
    result = await db.execute(select(PermissionModel).order_by(PermissionModel.module))
    permissions = result.scalars().all()
    
    return [
        PermissionInfo(
            id=p.id,
            code=p.code,
            module=p.module,
            description=p.description
        )
        for p in permissions
    ]


@router.get("/permissions/grouped")
async def list_permissions_grouped():
    """获取按模块分组的权限列表（用于前端展示）"""
    return get_permissions_by_module()


# ========== 角色管理 API ==========

@router.get("/roles", response_model=RoleListResponse)
async def list_roles(
    workspace_id: Optional[str] = None,
    current_user: User = Depends(CheckPerm("sys:user:view")),  # 需要查看成员权限
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取角色列表
    
    返回系统预设角色 + 租户自定义角色
    """
    # 构建查询：系统角色 OR 当前租户角色
    query = select(RoleModel).options(selectinload(RoleModel.permissions))
    
    effective_workspace_id = current_user.workspace_id
    if workspace_id and workspace_id != effective_workspace_id:
        raise HTTPException(status_code=403, detail="跨租户访问禁止")
    if effective_workspace_id:
        query = query.where(
            (RoleModel.workspace_id.is_(None)) |  # 系统全局角色
            (RoleModel.workspace_id == effective_workspace_id)  # 租户自定义角色
        )
    else:
        query = query.where(RoleModel.workspace_id.is_(None))  # 仅系统角色
    
    result = await db.execute(query.order_by(RoleModel.is_system.desc(), RoleModel.name))
    roles = result.scalars().all()
    
    return RoleListResponse(
        roles=[
            RoleInfo(
                id=r.id,
                name=r.name,
                description=r.description,
                workspace_id=r.workspace_id,
                is_system=r.is_system,
                permissions=[p.code for p in r.permissions],
                data_scope=r.data_scope or 4
            )
            for r in roles
        ]
    )


@router.post("/roles", response_model=RoleInfo)
async def create_role(
    request: CreateRoleRequest,
    current_user: User = Depends(CheckPerm("sys:role:edit")),  # 需要角色管理权限
    db: AsyncSession = Depends(get_async_db)
):
    """
    创建自定义角色
    
    自定义角色绑定到特定 workspace
    """
    workspace_id = current_user.workspace_id
    # 检查名称是否重复
    existing = await db.execute(
        select(RoleModel).where(
            RoleModel.name == request.name,
            RoleModel.workspace_id == workspace_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该角色名称已存在")
    
    # 获取权限对象
    perm_result = await db.execute(
        select(PermissionModel).where(PermissionModel.code.in_(request.permissions))
    )
    permissions = perm_result.scalars().all()
    
    # 创建角色
    role = RoleModel(
        name=request.name,
        description=request.description,
        workspace_id=workspace_id,
        is_system=False,
        data_scope=request.data_scope
    )
    role.permissions = permissions
    db.add(role)
    await db.commit()
    await db.refresh(role)
    
    logger.info(f"创建自定义角色: {role.name} (workspace={workspace_id}, data_scope={role.data_scope})")
    
    return RoleInfo(
        id=role.id,
        name=role.name,
        description=role.description,
        workspace_id=role.workspace_id,
        is_system=role.is_system,
        permissions=[p.code for p in role.permissions],
        data_scope=role.data_scope
    )


@router.put("/roles/{role_id}", response_model=RoleInfo)
async def update_role(
    role_id: int,
    request: UpdateRoleRequest,
    current_user: User = Depends(CheckPerm("sys:role:edit")),  # 需要角色管理权限
    db: AsyncSession = Depends(get_async_db)
):
    """
    更新角色信息和权限
    
    系统预设角色不可修改名称
    """
    result = await db.execute(
        select(RoleModel).where(RoleModel.id == role_id).options(selectinload(RoleModel.permissions))
    )
    role = result.scalar_one_or_none()
    
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    if role.workspace_id is not None and role.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    # 系统角色限制
    if role.is_system and request.name and request.name != role.name:
        raise HTTPException(status_code=400, detail="系统预设角色不可修改名称")
    
    # 更新字段
    if request.name:
        role.name = request.name
    if request.description is not None:
        role.description = request.description
    
    # 更新权限
    if request.permissions is not None:
        requested_permissions = request.permissions
        if role.is_system:
            requested_permissions = normalize_system_role_permissions(role.name, requested_permissions)
        logger.info(f"Updating permissions for role {role.name} (id={role.id}). Payload: {requested_permissions}")
        perm_result = await db.execute(
            select(PermissionModel).where(PermissionModel.code.in_(requested_permissions))
        )
        perms = perm_result.scalars().all()
        logger.info(f"Found {len(perms)} matching permissions in DB: {[p.code for p in perms]}")
        role.permissions = perms
    
    # 更新 data_scope
    if request.data_scope is not None:
        role.data_scope = request.data_scope
    
    await db.commit()
    await db.refresh(role)
    
    logger.info(f"更新角色: {role.name}")
    
    return RoleInfo(
        id=role.id,
        name=role.name,
        description=role.description,
        workspace_id=role.workspace_id,
        is_system=role.is_system,
        permissions=[p.code for p in role.permissions],
        data_scope=role.data_scope or 4
    )


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: int,
    current_user: User = Depends(CheckPerm("sys:role:edit")),  # 需要角色管理权限
    db: AsyncSession = Depends(get_async_db)
):
    """
    删除角色
    
    系统预设角色不可删除
    """
    result = await db.execute(select(RoleModel).where(RoleModel.id == role_id))
    role = result.unique().scalar_one_or_none()
    
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    if role.workspace_id is not None and role.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    if role.is_system:
        raise HTTPException(status_code=400, detail="系统预设角色不可删除")
    
    await db.delete(role)
    await db.commit()
    
    logger.info(f"删除角色: {role.name}")
    
    return {"success": True, "message": "角色已删除"}


# ========== 用户角色分配 API ==========

@router.get("/users/{user_id}/roles", response_model=UserWithRolesInfo)
async def get_user_roles(
    user_id: str,
    current_user: User = Depends(CheckPerm("sys:user:view")),  # 需要查看成员权限
    db: AsyncSession = Depends(get_async_db)
):
    """获取用户的角色和权限"""
    result = await db.execute(
        select(UserModel)
        .where(UserModel.id == user_id)
        .options(selectinload(UserModel.roles).selectinload(RoleModel.permissions))
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    return UserWithRolesInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        workspace_id=user.workspace_id,
        is_admin=user.is_admin,  # 纯 RBAC：通过属性判断
        roles=[
            RoleInfo(
                id=r.id,
                name=r.name,
                description=r.description,
                workspace_id=r.workspace_id,
                is_system=r.is_system,
                permissions=[p.code for p in r.permissions]
            )
            for r in user.roles
        ],
        permissions=list(user.all_permissions)
    )


@router.put("/users/{user_id}/roles")
async def assign_user_roles(
    user_id: str,
    request: AssignRolesRequest,
    current_user: User = Depends(CheckPerm("sys:user:edit")),  # 需要管理成员权限
    db: AsyncSession = Depends(get_async_db)
):
    """
    分配用户角色
    
    替换用户的所有角色为指定角色列表
    """
    # 获取用户
    user_result = await db.execute(
        select(UserModel).where(UserModel.id == user_id).options(selectinload(UserModel.roles))
    )
    user = user_result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.workspace_id != current_user.workspace_id:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 获取角色
    roles_result = await db.execute(
        select(RoleModel).where(
            RoleModel.id.in_(request.role_ids),
            (RoleModel.workspace_id.is_(None)) | (RoleModel.workspace_id == current_user.workspace_id)
        )
    )
    roles = roles_result.scalars().all()
    
    # 分配角色
    user.roles = roles
    await db.commit()
    
    logger.info(f"用户 {user.username} 角色更新为: {[r.name for r in roles]}")
    
    return {"success": True, "message": "角色分配成功", "roles": [r.name for r in roles]}
