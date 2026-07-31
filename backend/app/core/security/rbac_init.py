"""
DeluData 智能问数系统 - RBAC 权限初始化

系统启动时同步权限和预设角色到数据库
确保代码中的权限定义与数据库保持一致

[重写说明]
使用原始 SQL 操作多对多关系，避免 SQLAlchemy 异步懒加载问题
"""
import logging
import uuid
from typing import List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, insert

from app.models.auth.rbac import (
    PermissionModel, RoleModel, UserModel, 
    role_permissions, user_roles
)
from .auth import get_password_hash
from .capabilities import CAPABILITIES

logger = logging.getLogger(__name__)


FORCED_SYSTEM_ROLE_PERMISSIONS: Dict[str, List[str]] = {
    "Member": ["database:query"],
}


def normalize_system_role_permissions(role_name: str, permissions: List[str]) -> List[str]:
    """Keep required permissions on for built-in roles."""
    normalized = list(dict.fromkeys(permissions or []))
    for code in FORCED_SYSTEM_ROLE_PERMISSIONS.get(role_name, []):
        if code not in normalized:
            normalized.append(code)
    return normalized


# ========== 系统权限定义 (与菜单对应) ==========

SYSTEM_PERMISSIONS: List[Dict[str, str]] = [
    # [纯 RBAC] 超级管理员根权限
    {"code": "*", "module": "系统", "description": "超级管理员权限 - 拥有所有权限"},
    
    # 对话模块
    {"code": "chat:use", "module": "对话", "description": "使用AI对话"},
    
    # 知识库模块
    {"code": "knowledge:view", "module": "知识库", "description": "查看知识库"},
    {"code": "knowledge:manage", "module": "知识库", "description": "管理知识库文档"},
    
    # 数据库模块
    {"code": "database:view", "module": "数据库", "description": "查看数据库"},
    {"code": "database:query", "module": "数据库", "description": "执行SQL查询"},
    
    # 用户管理
    {"code": "user:view", "module": "用户管理", "description": "查看用户列表"},
    {"code": "user:manage", "module": "用户管理", "description": "管理用户"},
    
    # 角色权限
    {"code": "role:view", "module": "角色权限", "description": "查看角色列表"},
    {"code": "role:manage", "module": "角色权限", "description": "管理角色权限"},
    
    # 部门管理
    {"code": "dept:view", "module": "部门管理", "description": "查看部门"},
    {"code": "dept:manage", "module": "部门管理", "description": "管理部门"},
    
    # 扩展配置
    {"code": "config:view", "module": "扩展配置", "description": "查看配置"},
    {"code": "config:manage", "module": "扩展配置", "description": "修改配置"},
    
    # SQL数据权限
    {"code": "sql:manage", "module": "SQL数据权限", "description": "管理用户表级权限"},
    
    # 博物馆模块
    {"code": "museum:guide", "module": "博物馆", "description": "使用博物馆智能导览"},
    {"code": "museum:shop", "module": "博物馆", "description": "访问博物馆商城"},
    {"code": "museum:manage", "module": "博物馆", "description": "管理博物馆内容"},
]

# Keep the database catalogue synchronized with the canonical authorization model.
for _code, (_module, _description) in CAPABILITIES.items():
    if not any(item["code"] == _code for item in SYSTEM_PERMISSIONS):
        SYSTEM_PERMISSIONS.append(
            {"code": _code, "module": _module, "description": _description}
        )


# ========== 预设角色定义 ==========

SYSTEM_ROLES: List[Dict] = [
    {
        "name": "Admin",
        "description": "系统管理员 - 拥有全部权限",
        "permissions": ["*"],  # 特殊标记：全部权限
        "is_system": True
    },
    {
        "name": "Member", 
        "description": "普通成员 - 基础使用权限",
        "permissions": ["chat:use", "knowledge:view", "database:view", "database:query", "museum:guide", "museum:shop"],
        "is_system": True
    },
    {
        "name": "Viewer",
        "description": "访客 - 只能查看",
        "permissions": ["knowledge:view", "database:view"],
        "is_system": True
    }
]


# ========== 初始化函数 ==========

# Role names and wildcard permissions do not grant implicit authority.  The
# owner template is synchronized to the explicit, registered capability set.
SYSTEM_ROLES[0]["permissions"] = list(CAPABILITIES)


async def sync_permissions(db: AsyncSession) -> None:
    """
    同步权限表（幂等操作）
    
    启动时运行，确保数据库里的 Permission 表和代码定义一致
    """
    logger.info("开始同步系统权限...")
    
    for perm_data in SYSTEM_PERMISSIONS:
        # 检查权限是否存在
        result = await db.execute(
            select(PermissionModel).where(PermissionModel.code == perm_data["code"])
        )
        perm = result.scalar_one_or_none()
        
        if perm is None:
            # 新增权限
            perm = PermissionModel(
                code=perm_data["code"],
                module=perm_data["module"],
                description=perm_data["description"]
            )
            db.add(perm)
            logger.info(f"新增权限: {perm_data['code']}")
        else:
            # 更新描述（幂等）
            perm.description = perm_data["description"]
            perm.module = perm_data["module"]
    
    await db.commit()
    logger.info(f"权限同步完成，共 {len(SYSTEM_PERMISSIONS)} 个权限")


async def sync_system_roles(db: AsyncSession) -> None:
    """
    同步预设角色（幂等操作）
    
    使用原始 SQL 操作多对多关系，避免 ORM 懒加载问题
    """
    logger.info("开始同步系统预设角色...")
    
    # 获取所有权限 (code -> id 映射)
    result = await db.execute(select(PermissionModel))
    all_perms = {perm.code: perm.id for perm in result.scalars().all()}
    
    for role_data in SYSTEM_ROLES:
        # 检查角色是否存在（不预加载任何关系）
        result = await db.execute(
            select(RoleModel).where(
                RoleModel.name == role_data["name"],
                RoleModel.workspace_id.is_(None),
                RoleModel.is_system == True
            )
        )
        role = result.scalar_one_or_none()
        
        if role is None:
            # 新增角色
            role = RoleModel(
                name=role_data["name"],
                description=role_data["description"],
                workspace_id=None,
                is_system=True
            )
            db.add(role)
            await db.flush()  # 获取 ID
            logger.info(f"新增预设角色: {role_data['name']}")
        else:
            # 更新描述
            role.description = role_data["description"]
        
        # 使用原始 SQL 清除并重新分配权限
        # 1. 清除现有权限关系
        await db.execute(
            delete(role_permissions).where(role_permissions.c.role_id == role.id)
        )
        
        # 2. 确定要分配的权限 ID 列表
        role_permission_codes = normalize_system_role_permissions(role_data["name"], role_data["permissions"])
        if "*" in role_permission_codes:
            perm_ids = list(all_perms.values())
        else:
            perm_ids = [all_perms[code] for code in role_permission_codes if code in all_perms]
        
        # 3. 插入新的权限关系
        if perm_ids:
            await db.execute(
                insert(role_permissions),
                [{"role_id": role.id, "permission_id": pid} for pid in perm_ids]
            )
    
    await db.commit()
    logger.info(f"预设角色同步完成，共 {len(SYSTEM_ROLES)} 个角色")


async def sync_default_admin(db: AsyncSession) -> None:
    """
    同步默认管理员用户（幂等操作）
    
    使用原始 SQL 操作用户-角色关系，避免 ORM 懒加载问题
    """
    logger.info("检查默认管理员用户...")
    
    # 检查 admin 用户是否存在（不预加载任何关系）
    result = await db.execute(
        select(UserModel).where(UserModel.username == "admin")
    )
    admin_user = result.scalar_one_or_none()
    
    # 查找 Admin 角色
    admin_role_result = await db.execute(
        select(RoleModel).where(
            RoleModel.name == "Admin",
            RoleModel.is_system == True
        )
    )
    admin_role = admin_role_result.scalar_one_or_none()
    
    if admin_user is None:
        # 创建默认管理员
        admin_user = UserModel(
            id=str(uuid.uuid4()),
            username="admin",
            email="admin@deludata.com",
            hashed_password=get_password_hash("admin123"),
            workspace_id="default",
            disabled=False
        )
        db.add(admin_user)
        await db.flush()  # 获取 ID
        
        # 使用原始 SQL 绑定 Admin 角色
        if admin_role:
            await db.execute(
                insert(user_roles),
                [{"user_id": admin_user.id, "role_id": admin_role.id}]
            )
        
        await db.commit()
        logger.info("创建默认管理员用户: admin / admin123 (绑定 Admin 角色)")
    else:
        # 检查是否已有 Admin 角色（使用原始 SQL 查询）
        from sqlalchemy import and_
        has_role_result = await db.execute(
            select(user_roles).where(
                and_(
                    user_roles.c.user_id == admin_user.id,
                    user_roles.c.role_id == admin_role.id
                )
            )
        )
        has_admin_role = has_role_result.first() is not None
        
        if not has_admin_role and admin_role:
            await db.execute(
                insert(user_roles),
                [{"user_id": admin_user.id, "role_id": admin_role.id}]
            )
            await db.commit()
            logger.info("为 admin 用户绑定 Admin 角色")
        else:
            logger.info("默认管理员用户已存在，跳过创建")


async def init_rbac(db: AsyncSession) -> None:
    """
    完整的 RBAC 初始化流程
    
    应在应用启动时调用
    """
    try:
        await sync_permissions(db)
        await sync_system_roles(db)
        await sync_default_admin(db)
        logger.info("RBAC 权限系统初始化完成")
    except Exception as e:
        logger.error(f"RBAC 初始化失败: {e}")
        raise


# ========== 工具函数 ==========

def get_permission_codes() -> List[str]:
    """获取所有权限码列表（用于前端）"""
    return [p["code"] for p in SYSTEM_PERMISSIONS]


def get_permissions_by_module() -> Dict[str, List[Dict]]:
    """按模块分组获取权限（用于前端展示）"""
    modules = {}
    for perm in SYSTEM_PERMISSIONS:
        module = perm["module"]
        if module not in modules:
            modules[module] = []
        modules[module].append(perm)
    return modules
