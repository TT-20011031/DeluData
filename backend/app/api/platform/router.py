"""
平台超管 API

用于平台运营方管理所有租户
[独立鉴权] 使用 sys_platform_admins 表
"""
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.core.security.auth import get_password_hash
from app.core.security.capabilities import CAPABILITIES
from app.models.auth.workspace import (
    WorkspaceModel, 
    WorkspaceCreate, 
    WorkspaceUpdate, 
    WorkspaceAdminResponse,
    WorkspaceAdminUpdate,
    WorkspaceResponse,
    WorkspaceWithAdmin
)
from app.models.auth.rbac import PermissionModel, UserModel, RoleModel
from app.models.auth.organization import DepartmentModel
from app.models.auth.authorization import (
    AssignmentModel,
    AuthorizationAuditEventModel,
    AuthorizationRevisionModel,
    PositionModel,
    RoleBindingModel,
)
# [独立鉴权] 导入平台管理员鉴权
from app.api.platform.deps import get_current_platform_admin
from app.models.platform.admin import PlatformAdminModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform", tags=["平台管理"])


# ========== [已废弃] 旧的权限检查 ==========
# 现在使用 deps.get_current_platform_admin 替代


async def _get_workspace_or_404(db: AsyncSession, workspace_id: str) -> WorkspaceModel:
    result = await db.execute(
        select(WorkspaceModel).where(WorkspaceModel.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    return workspace


async def _get_workspace_admin_or_404(
    db: AsyncSession, workspace: WorkspaceModel
) -> UserModel:
    admin_user: UserModel | None = None
    if workspace.owner_id:
        result = await db.execute(
            select(UserModel).where(
                UserModel.id == workspace.owner_id,
                UserModel.workspace_id == workspace.id,
            )
        )
        admin_user = result.scalar_one_or_none()

    if admin_user is None:
        result = await db.execute(
            select(UserModel)
            .where(UserModel.workspace_id == workspace.id)
            .order_by(UserModel.disabled.asc(), UserModel.username.asc())
        )
        admin_user = result.scalars().first()

    if admin_user is None:
        raise HTTPException(status_code=404, detail="租户管理员不存在")

    return admin_user


# ========== 工作空间管理 API ==========

@router.get("/workspaces", response_model=List[WorkspaceResponse])
async def list_workspaces(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    is_active: Optional[bool] = Query(None),
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取所有工作空间列表
    
    [平台超管] 查看所有租户
    """
    stmt = select(WorkspaceModel)
    
    if is_active is not None:
        stmt = stmt.where(WorkspaceModel.is_active == is_active)
    
    stmt = stmt.offset(skip).limit(limit).order_by(WorkspaceModel.created_at.desc())
    
    result = await db.execute(stmt)
    workspaces = result.scalars().all()
    
    return workspaces


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取单个工作空间详情
    """
    workspace = await _get_workspace_or_404(db, workspace_id)
    return workspace


@router.get("/workspaces/{workspace_id}/admin", response_model=WorkspaceAdminResponse)
async def get_workspace_admin(
    workspace_id: str,
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """获取租户管理员信息"""
    workspace = await _get_workspace_or_404(db, workspace_id)
    admin_user = await _get_workspace_admin_or_404(db, workspace)
    return admin_user


@router.put("/workspaces/{workspace_id}/admin", response_model=WorkspaceAdminResponse)
async def update_workspace_admin(
    workspace_id: str,
    request: WorkspaceAdminUpdate,
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """更新租户管理员账号信息"""
    workspace = await _get_workspace_or_404(db, workspace_id)
    admin_user = await _get_workspace_admin_or_404(db, workspace)

    if request.username is not None:
        username = request.username.strip()
        if not username:
            raise HTTPException(status_code=400, detail="管理员账号不能为空")
        if username != admin_user.username:
            existing_user = await db.execute(
                select(UserModel).where(
                    UserModel.username == username,
                    UserModel.id != admin_user.id,
                )
            )
            if existing_user.scalar_one_or_none():
                raise HTTPException(status_code=400, detail=f"用户名 '{username}' 已存在")
            admin_user.username = username

    if request.email is not None:
        email = request.email.strip()
        admin_user.email = email or None

    if request.password is not None:
        password = request.password.strip()
        if password:
            admin_user.hashed_password = get_password_hash(password)

    await db.commit()
    await db.refresh(admin_user)

    logger.info(
        "[平台] 管理员 %s 更新了工作空间 %s 的管理员账号",
        admin.username,
        workspace.code,
    )

    return admin_user


@router.post("/workspaces", response_model=WorkspaceWithAdmin)
async def create_workspace(
    request: WorkspaceCreate,
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    创建新工作空间（原子化事务）
    
    同时创建:
    1. Workspace 记录
    2. 初始管理员账号
    3. 绑定管理员角色
    """
    # 检查 code 是否已存在
    existing = await db.execute(
        select(WorkspaceModel).where(WorkspaceModel.code == request.code)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"工作空间代码 '{request.code}' 已存在")
    
    # 检查管理员用户名是否已存在
    existing_user = await db.execute(
        select(UserModel).where(UserModel.username == request.admin_username)
    )
    if existing_user.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"用户名 '{request.admin_username}' 已存在")
    
    # Step 1: 创建工作空间
    workspace_id = str(uuid.uuid4())
    workspace = WorkspaceModel(
        id=workspace_id,
        name=request.name,
        code=request.code,
        plan=request.plan,
        max_users=request.max_users,
        museum_enabled=request.museum_enabled,
        kiosk_enabled=request.kiosk_enabled,
        description=request.description,
    )
    db.add(workspace)
    
    # Step 2: 创建管理员用户
    admin_id = str(uuid.uuid4())
    admin_user = UserModel(
        id=admin_id,
        username=request.admin_username,
        email=request.admin_email,
        hashed_password=get_password_hash(request.admin_password),
        workspace_id=workspace_id,
        disabled=False,
    )
    db.add(admin_user)
    
    # Step 3: 初始化租户组织、所有者岗位与显式能力角色。平台管理员
    # 自身仍使用独立鉴权，但其创建的租户不能再落回用户直绑角色。
    root_org = DepartmentModel(
        workspace_id=workspace_id,
        parent_id=None,
        name="总部",
        code="ROOT",
        ancestors="/",
        order_num=0,
        status=True,
    )
    db.add(root_org)
    await db.flush()
    owner_position = PositionModel(
        workspace_id=workspace_id,
        org_unit_id=root_org.id,
        name="工作区所有者",
        code="WORKSPACE_OWNER",
        status=True,
    )
    db.add(owner_position)
    permissions = list((await db.execute(
        select(PermissionModel).where(PermissionModel.code.in_(sorted(CAPABILITIES)))
    )).scalars().all())
    if len(permissions) != len(CAPABILITIES):
        raise HTTPException(status_code=409, detail="capability_catalog_not_initialized")
    owner_role = RoleModel(
        name="工作区所有者",
        description="系统维护的工作区所有者显式能力集合",
        workspace_id=workspace_id,
        is_system=True,
        data_scope=4,
    )
    owner_role.permissions = permissions
    db.add(owner_role)
    await db.flush()
    now = datetime.utcnow()
    owner_assignment = AssignmentModel(
        workspace_id=workspace_id,
        user_id=admin_id,
        position_id=owner_position.id,
        is_primary=True,
        starts_at=now,
        status=True,
    )
    owner_binding = RoleBindingModel(
        workspace_id=workspace_id,
        position_id=owner_position.id,
        role_id=owner_role.id,
        scope_type="workspace",
        custom_org_unit_ids=[],
        starts_at=now,
        status=True,
        created_by=admin_id,
    )
    db.add_all([owner_assignment, owner_binding])
    db.add(AuthorizationRevisionModel(workspace_id=workspace_id, revision=1))
    db.add(AuthorizationAuditEventModel(
        workspace_id=workspace_id,
        actor_id=f"platform:{admin.id}",
        action="workspace.authorization.initialize",
        target_type="workspace",
        target_id=workspace_id,
        before_json={},
        after_json={"owner_id": admin_id, "root_org_id": root_org.id},
        reason="workspace creation",
    ))

    # Step 4: 更新工作空间的 owner_id
    workspace.owner_id = admin_id
    
    await db.commit()
    await db.refresh(workspace)
    
    logger.info(f"[平台] 管理员 {admin.username} 创建了工作空间 {request.code}")
    
    return WorkspaceWithAdmin(
        workspace=WorkspaceResponse.model_validate(workspace),
        admin_username=request.admin_username,
        admin_id=admin_id
    )


@router.put("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    request: WorkspaceUpdate,
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    更新工作空间信息
    """
    result = await db.execute(
        select(WorkspaceModel).where(WorkspaceModel.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    
    # 不允许修改默认工作空间的状态
    if workspace.code == 'default' and request.is_active == False:
        raise HTTPException(status_code=400, detail="不能禁用默认工作空间")
    
    # 更新字段
    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(workspace, key, value)
    
    await db.commit()
    await db.refresh(workspace)
    
    logger.info(f"[平台] 管理员 {admin.username} 更新了工作空间 {workspace.code}")
    
    return workspace


@router.post("/workspaces/{workspace_id}/disable")
async def disable_workspace(
    workspace_id: str,
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    禁用工作空间
    """
    result = await db.execute(
        select(WorkspaceModel).where(WorkspaceModel.id == workspace_id)
    )
    workspace = result.scalar_one_or_none()
    
    if not workspace:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    
    if workspace.code == 'default':
        raise HTTPException(status_code=400, detail="不能禁用默认工作空间")
    
    workspace.is_active = False
    await db.commit()
    
    logger.info(f"[平台] 管理员 {admin.username} 禁用了工作空间 {workspace.code}")
    
    return {"success": True, "message": f"工作空间 {workspace.code} 已禁用"}


@router.get("/workspaces/{workspace_id}/users")
async def list_workspace_users(
    workspace_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取工作空间下的所有用户
    """
    # 验证工作空间存在
    ws_result = await db.execute(
        select(WorkspaceModel).where(WorkspaceModel.id == workspace_id)
    )
    if not ws_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="工作空间不存在")
    
    # 查询用户
    stmt = select(UserModel).where(
        UserModel.workspace_id == workspace_id
    ).offset(skip).limit(limit)
    
    result = await db.execute(stmt)
    users = result.scalars().all()
    
    # 统计总数
    count_stmt = select(func.count()).where(UserModel.workspace_id == workspace_id)
    count_result = await db.execute(count_stmt)
    total = count_result.scalar()
    
    return {
        "total": total,
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "disabled": u.disabled,
            }
            for u in users
        ]
    }


# ========== 统计 API ==========

@router.get("/stats")
async def get_platform_stats(
    admin: PlatformAdminModel = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取平台统计数据
    """
    # 工作空间数量
    ws_count = await db.execute(select(func.count()).select_from(WorkspaceModel))
    
    # 用户总数
    user_count = await db.execute(select(func.count()).select_from(UserModel))
    
    # 活跃工作空间数量
    active_ws = await db.execute(
        select(func.count()).where(WorkspaceModel.is_active == True)
    )
    
    return {
        "total_workspaces": ws_count.scalar(),
        "total_users": user_count.scalar(),
        "active_workspaces": active_ws.scalar(),
    }
