"""
管理员 API (Router 层)

用户管理、权限配置等（仅 Admin 可用）
"""
import uuid
import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, or_
from sqlalchemy.orm import selectinload

from app.api.deps import get_async_db
from app.core.security.auth import get_password_hash, User
from app.core.security.rbac_deps import CheckWorkspacePerm
from app.models.auth.rbac import UserModel, RoleModel, UserTablePermission
from app.models.auth.organization import DepartmentModel
from app.services.db_whitelist_service import DBWhitelistService

from .schemas import (
    CreateUserRequest,
    UpdateUserRequest,
    UserInfo,
    UserListResponse,
    RoleInfo,
    DepartmentInfo,
    TablePermissionsRequest,
    TablePermissionsResponse,
    ResetPasswordRequest,  # [NEW] 重置密码
)

logger = logging.getLogger(__name__)

router = APIRouter()
get_current_admin = CheckWorkspacePerm("database:manage")


# ========== 用户管理 API ==========

@router.api_route("/users", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@router.api_route("/users/{legacy_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def legacy_user_api_gone(legacy_path: str = ""):
    raise HTTPException(
        status_code=410,
        detail={
            "code": "legacy_api_gone",
            "replacement": "/api/authorization/users and /api/authorization/assignments",
        },
    )


@router.post("/users", response_model=UserInfo)
async def admin_create_user(
    request: CreateUserRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    创建新用户（仅管理员）- 原子性事务
    
    Unit of Work 模式：
    1. 创建用户记录 -> flush() 获取 ID
    2. 绑定部门
    3. 批量绑定角色
    4. 任一步骤失败则全量回滚，不留"孤儿账号"
    """
    # 检查用户名是否存在
    result = await db.execute(
        select(UserModel).where(UserModel.username == request.username)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    
    try:
        # Step 1: 先查询要绑定的角色
        roles_to_bind = []
        
        if request.role_ids:
            roles_result = await db.execute(
                select(RoleModel)
                .where(
                    RoleModel.id.in_(request.role_ids),
                    or_(
                        RoleModel.workspace_id.is_(None),
                        RoleModel.workspace_id == admin.workspace_id
                    )
                )
                .options(selectinload(RoleModel.permissions))
            )
            roles_to_bind = list(roles_result.scalars().all())
            
            if len(roles_to_bind) != len(request.role_ids):
                found_ids = {r.id for r in roles_to_bind}
                missing_ids = set(request.role_ids) - found_ids
                raise HTTPException(
                    status_code=400,
                    detail=f"以下角色 ID 不存在: {list(missing_ids)}"
                )
        else:
            # 兼容旧版本
            if request.role not in ["admin", "user"]:
                raise HTTPException(status_code=400, detail="无效的角色，可选: admin, user")
            
            role_name = "Admin" if request.role == "admin" else "Member"
            role_result = await db.execute(
                select(RoleModel)
                .where(RoleModel.name == role_name, RoleModel.is_system == True)
                .options(selectinload(RoleModel.permissions))
            )
            role_obj = role_result.scalar_one_or_none()
            if role_obj:
                roles_to_bind.append(role_obj)
        
        # Step 2: 创建用户时直接设置 roles（在 add 之前，避免触发懒加载）
        user = UserModel(
            id=str(uuid.uuid4()),
            username=request.username,
            email=request.email,
            hashed_password=get_password_hash(request.password),
            workspace_id=admin.workspace_id,
            disabled=False,
            department_id=request.department_id
        )
        user.roles = roles_to_bind  # 在 add 之前设置，不会触发懒加载
        
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"创建用户事务失败，已回滚: {e}")
        raise HTTPException(status_code=500, detail=f"创建用户失败: {str(e)}")
    
    logger.info(f"管理员 {admin.username} 创建用户: {user.username}")
    
    # 基于权限判断显示角色（roles_to_bind 已预加载 permissions）
    all_perms = set()
    for r in roles_to_bind:
        for p in r.permissions:
            all_perms.add(p.code)
    display_role = "admin" if "*" in all_perms else "user"
    
    return UserInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        role=display_role,
        workspace_id=user.workspace_id,
        disabled=user.disabled,
        department_id=user.department_id
    )


@router.get("/users", response_model=UserListResponse)
async def admin_list_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """获取所有用户（仅管理员）"""
    result = await db.execute(
        select(UserModel)
        .where(UserModel.workspace_id == admin.workspace_id)
        .options(selectinload(UserModel.roles))
        .order_by(UserModel.username)
    )
    users_db = result.scalars().all()
    
    # 批量获取部门信息
    dept_ids = [u.department_id for u in users_db if u.department_id]
    dept_map = {}
    if dept_ids:
        dept_result = await db.execute(
            select(DepartmentModel).where(
                DepartmentModel.id.in_(dept_ids),
                DepartmentModel.workspace_id == admin.workspace_id
            )
        )
        for d in dept_result.scalars().all():
            dept_map[d.id] = DepartmentInfo(id=d.id, name=d.name)
    
    users = []
    for user in users_db:
        all_perms = set()
        for r in user.roles:
            for p in r.permissions:
                all_perms.add(p.code)
        if "*" in all_perms:
            display_role = "admin"
        elif user.roles:
            display_role = user.roles[0].name.lower()
        else:
            display_role = "user"
        
        role_infos = [
            RoleInfo(id=r.id, name=r.name, description=r.description)
            for r in user.roles
        ]
        
        users.append(UserInfo(
            id=user.id,
            username=user.username,
            email=user.email,
            role=display_role,
            roles=role_infos,
            workspace_id=user.workspace_id,
            disabled=user.disabled,
            department_id=user.department_id,
            department=dept_map.get(user.department_id) if user.department_id else None
        ))
    
    return UserListResponse(users=users)


@router.put("/users/{user_id}", response_model=UserInfo)
async def admin_update_user(
    user_id: str,
    request: UpdateUserRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """更新用户信息（仅管理员）- 原子性事务"""
    # 预加载 roles + roles.permissions，避免后续懒加载
    result = await db.execute(
        select(UserModel)
        .where(
            UserModel.id == user_id,
            UserModel.workspace_id == admin.workspace_id
        )
        .options(selectinload(UserModel.roles).selectinload(RoleModel.permissions))
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 保存用于返回的角色列表（可能会被覆盖）
    final_roles = list(user.roles)
    
    try:
        if request.email is not None:
            user.email = request.email
        if request.disabled is not None:
            user.disabled = request.disabled
        if request.department_id is not None:
            user.department_id = request.department_id
        
        # 更新角色
        if request.role_ids is not None:
            # 预加载 permissions
            roles_result = await db.execute(
                select(RoleModel)
                .where(
                    RoleModel.id.in_(request.role_ids),
                    or_(
                        RoleModel.workspace_id.is_(None),
                        RoleModel.workspace_id == admin.workspace_id
                    )
                )
                .options(selectinload(RoleModel.permissions))
            )
            final_roles = list(roles_result.scalars().all())
            user.roles = final_roles  # roles 已预加载，可以安全赋值
            
        elif request.role is not None:
            if request.role not in ["admin", "user"]:
                raise HTTPException(status_code=400, detail="无效的角色")
            
            SYSTEM_ROLE_NAMES = {"Admin", "Member", "Viewer"}
            keep_roles = [r for r in user.roles if r.name not in SYSTEM_ROLE_NAMES]
            
            role_name = "Admin" if request.role == "admin" else "Member"
            role_result = await db.execute(
                select(RoleModel)
                .where(RoleModel.name == role_name, RoleModel.is_system == True)
                .options(selectinload(RoleModel.permissions))
            )
            new_system_role = role_result.scalar_one_or_none()
            
            final_roles = keep_roles
            if new_system_role:
                final_roles.append(new_system_role)
            user.roles = final_roles  # 直接赋值
        
        await db.commit()
        await db.refresh(user)
        
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"更新用户事务失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")
    
    logger.info(f"管理员 {admin.username} 更新用户: {user.username}")
    
    # 使用 final_roles（已预加载 permissions）计算权限
    all_perms = set()
    for r in final_roles:
        for p in r.permissions:
            all_perms.add(p.code)
    display_role = "admin" if "*" in all_perms else "user"
    
    role_infos = [RoleInfo(id=r.id, name=r.name, description=r.description) for r in final_roles]
    
    dept_info = None
    if user.department_id:
        dept_result = await db.execute(
            select(DepartmentModel).where(DepartmentModel.id == user.department_id)
        )
        dept = dept_result.scalar_one_or_none()
        if dept:
            dept_info = DepartmentInfo(id=dept.id, name=dept.name)
    
    return UserInfo(
        id=user.id,
        username=user.username,
        email=user.email,
        role=display_role,
        roles=role_infos,
        workspace_id=user.workspace_id,
        disabled=user.disabled,
        department_id=user.department_id,
        department=dept_info
    )


@router.delete("/users/{user_id}")
async def admin_delete_user(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """删除用户（仅管理员）"""
    result = await db.execute(
        select(UserModel).where(
            UserModel.id == user_id,
            UserModel.workspace_id == admin.workspace_id
        )
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    
    await db.delete(user)
    await db.commit()
    
    logger.info(f"管理员 {admin.username} 删除用户: {user.username}")
    
    return {"success": True, "message": "用户已删除"}


# ========== 表级权限管理 ==========

@router.get("/users/{user_id}/table-permissions", response_model=TablePermissionsResponse)
async def get_user_table_permissions(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """获取用户的表级权限"""
    result = await db.execute(
        select(UserModel).where(
            UserModel.id == user_id,
            UserModel.workspace_id == admin.workspace_id
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    result = await db.execute(
        select(UserTablePermission).where(UserTablePermission.user_id == user_id)
    )
    permissions = result.scalars().all()
    
    return TablePermissionsResponse(
        user_id=user_id,
        allowed_tables=[p.table_name for p in permissions]
    )


@router.put("/users/{user_id}/table-permissions")
async def save_user_table_permissions(
    user_id: str,
    request: TablePermissionsRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """保存用户的表级权限"""
    result = await db.execute(
        select(UserModel).where(
            UserModel.id == user_id,
            UserModel.workspace_id == admin.workspace_id
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    await db.execute(
        delete(UserTablePermission).where(UserTablePermission.user_id == user_id)
    )
    
    allowed_tables = [
        table for table, allowed in request.table_permissions.items() if allowed
    ]
    
    for table_name in allowed_tables:
        db.add(UserTablePermission(user_id=user_id, table_name=table_name))
    
    await db.commit()
    
    logger.info(f"管理员 {admin.username} 更新用户 {user.username} 的表权限: {len(allowed_tables)} 个表")
    
    return {
        "success": True,
        "user_id": user_id,
        "allowed_tables": allowed_tables,
        "message": f"已保存 {len(allowed_tables)} 个表的访问权限"
    }


# ========== 密码管理 API ==========

@router.put("/users/{user_id}/password")
async def admin_reset_password(
    user_id: str,
    request: ResetPasswordRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db)
):
    """
    管理员重置用户密码
    
    [NEW] 允许管理员强制重置用户密码，无需旧密码验证
    
    Args:
        user_id: 目标用户 ID
        request: 包含新密码的请求
        
    Returns:
        成功响应
    """
    # 验证密码长度
    if len(request.new_password) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于6位")
    
    # 查找用户
    result = await db.execute(
        select(UserModel).where(
            UserModel.id == user_id,
            UserModel.workspace_id == admin.workspace_id
        )
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 更新密码
    user.hashed_password = get_password_hash(request.new_password)
    await db.commit()
    
    logger.info(f"[审计] 管理员 {admin.username} 重置了用户 {user.username} 的密码")
    
    return {
        "success": True,
        "user_id": user_id,
        "message": f"用户 {user.username} 的密码已重置"
    }


class DBWhitelistEndpointRequest(BaseModel):
    host: str
    port: int = 3306


class DBWhitelistRequest(BaseModel):
    is_enabled: bool = False
    allowed_endpoints: list[DBWhitelistEndpointRequest] = Field(default_factory=list)
    note: str | None = None


@router.get("/db-whitelist")
async def get_db_whitelist(
    admin: User = Depends(get_current_admin),
):
    service = DBWhitelistService()
    config = await service.get_workspace_config(admin.workspace_id)
    if not config:
        return {
            "is_enabled": False,
            "allowed_endpoints": [],
            "note": None,
        }

    return {
        "is_enabled": bool(config.is_enabled),
        "allowed_endpoints": [
            {"host": item.host, "port": item.port}
            for item in (config.allowed_endpoints or [])
        ],
        "note": config.note,
    }


@router.put("/db-whitelist")
async def update_db_whitelist(
    request: DBWhitelistRequest,
    admin: User = Depends(get_current_admin),
):
    service = DBWhitelistService()
    saved = await service.update_workspace_config(
        admin.workspace_id,
        is_enabled=request.is_enabled,
        allowed_endpoints=[
            {"host": item.host, "port": item.port}
            for item in request.allowed_endpoints
        ],
        note=request.note,
        updated_by=str(admin.id),
    )

    logger.info(
        "[Audit] admin=%s workspace=%s updated DB whitelist enabled=%s endpoints=%s",
        admin.username,
        admin.workspace_id,
        saved.is_enabled,
        len(saved.allowed_endpoints),
    )

    return {
        "is_enabled": bool(saved.is_enabled),
        "allowed_endpoints": [
            {"host": item.host, "port": item.port}
            for item in (saved.allowed_endpoints or [])
        ],
        "note": saved.note,
    }
