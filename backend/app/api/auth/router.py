"""Authentication APIs."""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.core.security.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    User,
    apply_workspace_admin_access,
    create_access_token,
    create_user_async,
    get_user_by_username,
    user_model_to_user,
    verify_password,
)
from app.models.auth.workspace import WorkspaceModel
from app.services.workspace_knowledge_governance_service import (
    WorkspaceKnowledgeGovernanceService,
)

from .schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse

logger = logging.getLogger(__name__)
router = APIRouter()


async def _get_workspace_features(db: AsyncSession, workspace_id: str) -> dict:
    result = await db.execute(
        select(
            WorkspaceModel.museum_enabled,
            WorkspaceModel.kiosk_enabled,
        ).where(WorkspaceModel.id == workspace_id)
    )
    row = result.first()
    governance_service = WorkspaceKnowledgeGovernanceService(db)
    knowledge_features = await governance_service.get_workspace_feature_flags(workspace_id)
    if not row:
        return {
            "museum_enabled": False,
            "kiosk_enabled": False,
            **knowledge_features,
        }
    return {
        "museum_enabled": bool(row[0]),
        "kiosk_enabled": bool(row[1]),
        **knowledge_features,
    }


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_async_db),
):
    user_db = await get_user_by_username(db, request.username)
    if not user_db or not verify_password(request.password, user_db.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user_db.disabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user_disabled")

    ws_result = await db.execute(
        select(
            WorkspaceModel.is_active,
            WorkspaceModel.museum_enabled,
            WorkspaceModel.kiosk_enabled,
            WorkspaceModel.owner_id,
        ).where(WorkspaceModel.id == user_db.workspace_id)
    )
    ws_row = ws_result.first()
    if ws_row is None or not bool(ws_row[0]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace_disabled")
    governance_service = WorkspaceKnowledgeGovernanceService(db)
    workspace_features = {
        "museum_enabled": bool(ws_row[1]),
        "kiosk_enabled": bool(ws_row[2]),
        **(await governance_service.get_workspace_feature_flags(user_db.workspace_id)),
    }

    user = apply_workspace_admin_access(user_model_to_user(user_db), ws_row[3])
    access_token = create_access_token(
        data={
            "sub": user.id,
            "username": user.username,
            "role": user.role,
            "workspace_id": user.workspace_id,
        },
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    logger.info("用户登录成功: %s", user.username)
    return TokenResponse(
        access_token=access_token,
        user={
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "permissions": user.permissions,
            "is_workspace_admin": user.is_workspace_admin,
            "department_id": user.department_id,
            "workspace_features": workspace_features,
        },
    )


@router.post("/register", response_model=TokenResponse)
async def register(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_async_db),
):
    try:
        user_db = await create_user_async(
            db,
            username=request.username,
            password=request.password,
            email=request.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    user = user_model_to_user(user_db)
    access_token = create_access_token(
        data={
            "sub": user.id,
            "username": user.username,
            "role": user.role,
            "workspace_id": user.workspace_id,
        }
    )
    workspace_features = await _get_workspace_features(db, user.workspace_id)

    logger.info("新用户注册: %s", user.username)
    return TokenResponse(
        access_token=access_token,
        user={
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "permissions": user.permissions,
            "department_id": user.department_id,
            "workspace_features": workspace_features,
        },
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    workspace_features = await _get_workspace_features(db, current_user.workspace_id)
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        role=current_user.role,
        workspace_id=current_user.workspace_id,
        permissions=current_user.permissions,
        is_workspace_admin=current_user.is_workspace_admin,
        workspace_features=workspace_features,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    access_token = create_access_token(
        data={
            "sub": current_user.id,
            "username": current_user.username,
            "role": current_user.role,
            "workspace_id": current_user.workspace_id,
        }
    )
    workspace_features = await _get_workspace_features(db, current_user.workspace_id)

    return TokenResponse(
        access_token=access_token,
        user={
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "permissions": current_user.permissions,
            "is_workspace_admin": current_user.is_workspace_admin,
            "department_id": current_user.department_id,
            "workspace_features": workspace_features,
        },
    )


@router.get("/status")
async def check_status(current_user: User = Depends(get_current_user)):
    return {"status": "active", "workspace_id": current_user.workspace_id}
