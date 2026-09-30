"""Common API dependencies."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.database import get_async_db_manager
from app.core.db.tenant_mixin import set_current_workspace
from app.core.security.auth import User, decode_token, get_user_by_id, user_model_to_user
from app.models.auth.workspace import WorkspaceModel
from app.models.common.context import UserContext
from app.services.authorization_service import build_effective_access_context

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


async def get_async_db():
    """Yield async DB session."""
    db = get_async_db_manager()
    async with db.session_scope() as session:
        yield session


async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_async_db),
) -> Optional[User]:
    if not token:
        return None

    token_data = decode_token(token)
    if not token_data:
        return None

    user_db = await get_user_by_id(db, token_data.user_id)
    if not user_db or user_db.disabled:
        return None

    ws_result = await db.execute(
        select(WorkspaceModel.is_active).where(WorkspaceModel.id == user_db.workspace_id)
    )
    ws_is_active = ws_result.scalar_one_or_none()
    if ws_is_active is False or ws_is_active is None:
        return None

    set_current_workspace(user_db.workspace_id)
    user = user_model_to_user(user_db)
    context = await build_effective_access_context(db, user.workspace_id, user.id)
    user.permissions = context.capabilities
    user.capability_scopes = context.capability_scopes
    user.denied_capability_scopes = context.denied_capability_scopes
    user.authorization_revision = context.revision
    user.is_workspace_admin = context.is_workspace_admin
    if context.is_workspace_admin:
        user.role = "admin"
    user.data_scope = 1 if any(value == "*" for value in context.capability_scopes.values()) else 4
    primary = next((item for item in context.assignments if item.is_primary), None)
    if primary:
        user.department_id = primary.org_unit_id
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_async_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid_credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exception

    token_data = decode_token(token)
    if not token_data:
        raise credentials_exception

    user_db = await get_user_by_id(db, token_data.user_id)
    if not user_db:
        raise credentials_exception
    if user_db.disabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user_disabled")

    ws_result = await db.execute(
        select(WorkspaceModel.is_active).where(WorkspaceModel.id == user_db.workspace_id)
    )
    ws_is_active = ws_result.scalar_one_or_none()
    if ws_is_active is False or ws_is_active is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace_disabled")

    set_current_workspace(user_db.workspace_id)
    user = user_model_to_user(user_db)
    context = await build_effective_access_context(db, user.workspace_id, user.id)
    user.permissions = context.capabilities
    user.capability_scopes = context.capability_scopes
    user.denied_capability_scopes = context.denied_capability_scopes
    user.authorization_revision = context.revision
    user.is_workspace_admin = context.is_workspace_admin
    if context.is_workspace_admin:
        user.role = "admin"
    user.data_scope = 1 if any(value == "*" for value in context.capability_scopes.values()) else 4
    primary = next((item for item in context.assignments if item.is_primary), None)
    if primary:
        user.department_id = primary.org_unit_id
    return user


async def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    allowed = current_user.capability_scopes.get("config:manage")
    denied = current_user.denied_capability_scopes.get("config:manage")
    if allowed != "*" or denied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace_config_management_required",
        )
    return current_user


async def get_current_query_user(current_user: User = Depends(get_current_user)) -> User:
    """Require the ordinary read-only data-query capability."""

    allowed = current_user.capability_scopes.get("database:query")
    denied = current_user.denied_capability_scopes.get("database:query")
    if (
        "database:query" not in current_user.permissions
        or allowed is None
        or allowed == []
        or denied == "*"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="database_query_permission_required",
        )
    return current_user


def get_user_context(current_user: User = Depends(get_current_user)) -> UserContext:
    return UserContext(
        user_id=current_user.id,
        workspace_id=current_user.workspace_id,
        allowed_tables=["*"] if "*" in current_user.permissions else [],
        role=current_user.role,
        capabilities=list(current_user.permissions),
        is_workspace_admin=current_user.is_workspace_admin,
        dept_id=current_user.department_id,
        data_scope=current_user.data_scope,
    )


def get_user_context_optional(
    user: Optional[User] = Depends(get_current_user_optional),
) -> UserContext:
    if user:
        set_current_workspace(user.workspace_id)
        return UserContext(
            user_id=user.id,
            workspace_id=user.workspace_id,
            allowed_tables=["*"] if "*" in user.permissions else [],
            role=user.role,
            capabilities=list(user.permissions),
            is_workspace_admin=user.is_workspace_admin,
            dept_id=user.department_id,
            data_scope=user.data_scope,
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication_required",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _load_workspace_feature_flags(
    db: AsyncSession,
    workspace_id: str,
) -> tuple[bool, bool]:
    result = await db.execute(
        select(
            WorkspaceModel.museum_enabled,
            WorkspaceModel.kiosk_enabled,
        ).where(WorkspaceModel.id == workspace_id)
    )
    row = result.first()
    if not row:
        return False, False
    return bool(row[0]), bool(row[1])


async def require_workspace_feature_enabled(
    feature: Literal["museum", "kiosk"],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    museum_enabled, kiosk_enabled = await _load_workspace_feature_flags(
        db, current_user.workspace_id
    )
    enabled = museum_enabled if feature == "museum" else kiosk_enabled
    if enabled:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"{feature}_disabled")


async def require_workspace_museum_enabled(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    await require_workspace_feature_enabled("museum", current_user=current_user, db=db)


async def require_workspace_kiosk_enabled(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    await require_workspace_feature_enabled("kiosk", current_user=current_user, db=db)
