"""
DeluData - 认证与权限模块

包含:
- RBAC 权限模型 (User, Role, Permission)
- 组织架构模型 (Department)
"""

# RBAC 模型
from app.models.auth.rbac import (
    UserModel,
    RoleModel, 
    PermissionModel,
    UserTablePermission,
    user_roles,
    role_permissions,
)

# 组织架构模型
from app.models.auth.organization import (
    DepartmentModel,
    DataScope,
)
from app.models.auth.authorization import (
    AssignmentModel,
    AuthorizationAuditEventModel,
    AuthorizationExceptionModel,
    AuthorizationRevisionModel,
    PositionModel,
    RoleBindingModel,
)
from app.models.auth.organization_semantic import (
    OrganizationSemanticProfileModel,
    OrganizationSemanticProfileVersionModel,
    WorkspaceBusinessContextModel,
)

__all__ = [
    # RBAC
    "UserModel",
    "RoleModel",
    "PermissionModel",
    "UserTablePermission",
    "user_roles",
    "role_permissions",
    # 组织架构
    "DepartmentModel",
    "DataScope",
    "PositionModel",
    "AssignmentModel",
    "RoleBindingModel",
    "AuthorizationExceptionModel",
    "AuthorizationAuditEventModel",
    "AuthorizationRevisionModel",
    "WorkspaceBusinessContextModel",
    "OrganizationSemanticProfileModel",
    "OrganizationSemanticProfileVersionModel",
]
