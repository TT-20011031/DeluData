"""
DeluData 智能问数系统 - RBAC 鉴权依赖注入

提供 FastAPI 依赖注入的权限校验类
基于 SQL 后端，从 current_user.permissions 读取真实权限
"""
from typing import List
from fastapi import Depends, HTTPException, status

from app.api.auth import get_current_user
from .auth import User
from .capabilities import canonical_capability, is_registered_capability


def require_capability(
    current_user: User,
    capability: str,
    target_org_unit_id: int | None = None,
) -> User:
    """Validate a capability and its optional organization scope."""
    code = canonical_capability(capability)
    allowed = current_user.capability_scopes.get(code)
    denied = current_user.denied_capability_scopes.get(code)
    if "*" in current_user.permissions and allowed is None:
        allowed = "*"
    permitted = allowed is not None
    if target_org_unit_id is not None:
        permitted = permitted and (allowed == "*" or target_org_unit_id in (allowed or []))
        if denied == "*" or target_org_unit_id in (denied or []):
            permitted = False
    elif denied == "*":
        permitted = False
    if not permitted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "capability_denied", "capability": code, "org_unit_id": target_org_unit_id},
        )
    return current_user


class CheckPerm:
    """
    权限校验依赖类
    
    基于用户真实权限列表校验：
    - permissions 包含 "*" 表示拥有所有权限（超级管理员）
    - 否则检查具体权限码
    
    使用方式:
        @router.post("/upload")
        async def upload_file(
            user: User = Depends(CheckPerm("kb:manage"))
        ):
            ...
    """
    
    def __init__(self, required_perm: str):
        """
        初始化权限校验
        
        Args:
            required_perm: 必需的权限码，如 "sql:query"
        """
        if not is_registered_capability(required_perm):
            raise RuntimeError(f"unregistered capability referenced by API: {required_perm}")
        self.required_perm = required_perm
    
    async def __call__(
        self,
        current_user: User = Depends(get_current_user)
    ) -> User:
        """
        执行权限校验
        
        从 current_user.permissions 读取用户真实权限
        
        Returns:
            校验通过的用户对象
            
        Raises:
            HTTPException: 权限不足时抛出403
        """
        return require_capability(current_user, self.required_perm)


class CheckWorkspacePerm(CheckPerm):
    """Require a capability that is valid across the whole workspace."""

    async def __call__(
        self,
        current_user: User = Depends(get_current_user),
    ) -> User:
        require_capability(current_user, self.required_perm)
        code = canonical_capability(self.required_perm)
        allowed = current_user.capability_scopes.get(code)
        denied = current_user.denied_capability_scopes.get(code)
        if allowed != "*" or denied:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "workspace_scope_required", "capability": code},
            )
        return current_user


class CheckAnyPerm:
    """
    多权限任一校验（满足任意一个即可）
    
    使用方式:
        @router.get("/data")
        async def get_data(
            user: User = Depends(CheckAnyPerm(["sql:query", "kb:read"]))
        ):
            ...
    """
    
    def __init__(self, required_perms: List[str]):
        unknown = [item for item in required_perms if not is_registered_capability(item)]
        if unknown:
            raise RuntimeError(f"unregistered capabilities referenced by API: {unknown}")
        self.required_perms = required_perms
    
    async def __call__(
        self,
        current_user: User = Depends(get_current_user)
    ) -> User:
        for capability in self.required_perms:
            try:
                return require_capability(current_user, capability)
            except HTTPException:
                continue
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足，需要其中之一: {self.required_perms}",
        )


class CheckAllPerms:
    """
    多权限全部校验（必须满足全部）
    """
    
    def __init__(self, required_perms: List[str]):
        unknown = [item for item in required_perms if not is_registered_capability(item)]
        if unknown:
            raise RuntimeError(f"unregistered capabilities referenced by API: {unknown}")
        self.required_perms = required_perms
    
    async def __call__(
        self,
        current_user: User = Depends(get_current_user)
    ) -> User:
        for capability in self.required_perms:
            require_capability(current_user, capability)
        return current_user
