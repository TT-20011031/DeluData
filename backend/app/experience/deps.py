"""Dependency helpers for experience-backend device auth."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_async_db
from app.config import get_settings
from app.core.db.tenant_mixin import set_current_workspace
from app.core.security.auth import user_model_to_user
from app.experience.models import ScienceDevice
from app.models.auth.workspace import WorkspaceModel
from app.models.auth.rbac import RoleModel, UserModel
from app.models.common.context import UserContext

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExperienceContext:
    device_id: str
    workspace_id: str
    service_user_id: str
    dept_id: Optional[int]
    device_token: str
    session_id: Optional[str] = None


def _build_dev_device_id(device_token: str) -> str:
    digest = hashlib.sha1(device_token.encode("utf-8")).hexdigest()[:12]
    return f"dev-{digest}"


async def _try_auto_register_dev_device(
    db: AsyncSession,
    device_token: str,
) -> Optional[ScienceDevice]:
    settings = get_settings()
    if not settings.app.debug or not settings.experience.dev_auto_register_device:
        return None

    token_prefix = (settings.experience.dev_auto_register_token_prefix or "").strip()
    if token_prefix and not device_token.startswith(token_prefix):
        return None

    service_username = (
        settings.experience.dev_auto_register_service_username.strip() or "admin"
    )
    user_result = await db.execute(
        select(UserModel).where(
            UserModel.username == service_username,
            UserModel.disabled.is_(False),
        )
    )
    service_user = user_result.scalar_one_or_none()
    if not service_user:
        logger.warning(
            "Device auto-register skipped: service user '%s' not found",
            service_username,
        )
        return None

    device_id = _build_dev_device_id(device_token)
    existing_result = await db.execute(
        select(ScienceDevice).where(
            ScienceDevice.workspace_id == service_user.workspace_id,
            ScienceDevice.device_id == device_id,
        )
    )
    device = existing_result.scalar_one_or_none()
    if device:
        device.device_token = device_token
        device.service_user_id = service_user.id
        device.dept_id = service_user.department_id
        device.is_active = True
        await db.flush()
        logger.warning(
            "Auto-activated dev device '%s' for token prefix '%s'",
            device.device_id,
            token_prefix or "*",
        )
        return device

    name_prefix = (
        settings.experience.dev_auto_register_device_name_prefix.strip() or "Dev Kiosk"
    )
    device = ScienceDevice(
        workspace_id=service_user.workspace_id,
        device_id=device_id,
        name=f"{name_prefix} {device_id[-6:]}",
        device_token=device_token,
        service_user_id=service_user.id,
        dept_id=service_user.department_id,
        is_active=True,
    )
    db.add(device)
    await db.flush()
    logger.warning(
        "Auto-registered dev device '%s' in workspace '%s'",
        device.device_id,
        device.workspace_id,
    )
    return device


async def get_experience_context(
    x_device_token: str = Header(..., alias="X-Device-Token"),
    db: AsyncSession = Depends(get_async_db),
) -> ExperienceContext:
    result = await db.execute(
        select(ScienceDevice).where(
            ScienceDevice.device_token == x_device_token,
            ScienceDevice.is_active.is_(True),
        )
    )
    device = result.scalar_one_or_none()
    if not device:
        device = await _try_auto_register_dev_device(db, x_device_token)

    if not device:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_device_token",
        )

    ws_result = await db.execute(
        select(
            WorkspaceModel.is_active,
            WorkspaceModel.kiosk_enabled,
        ).where(WorkspaceModel.id == device.workspace_id)
    )
    ws_row = ws_result.first()
    if ws_row is None or not bool(ws_row[0]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace_disabled",
        )
    if not bool(ws_row[1]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="kiosk_disabled",
        )

    set_current_workspace(device.workspace_id)
    return ExperienceContext(
        device_id=device.device_id,
        workspace_id=device.workspace_id,
        service_user_id=device.service_user_id,
        dept_id=device.dept_id,
        device_token=x_device_token,
    )


async def get_experience_user_context(
    experience_ctx: ExperienceContext = Depends(get_experience_context),
    db: AsyncSession = Depends(get_async_db),
) -> UserContext:
    result = await db.execute(
        select(UserModel)
        .where(UserModel.id == experience_ctx.service_user_id)
        .options(selectinload(UserModel.roles).selectinload(RoleModel.permissions))
    )
    user_db = result.scalar_one_or_none()

    if user_db:
        user = user_model_to_user(user_db)
        set_current_workspace(user.workspace_id)
        return UserContext(
            user_id=user.id,
            workspace_id=user.workspace_id,
            role=user.role,
            dept_id=user.department_id,
            data_scope=user.data_scope,
            allowed_tables=["*"] if "*" in user.permissions else [],
        )

    # Fallback for temporarily deleted service user.
    set_current_workspace(experience_ctx.workspace_id)
    return UserContext(
        user_id=experience_ctx.service_user_id,
        workspace_id=experience_ctx.workspace_id,
        role="service",
        dept_id=experience_ctx.dept_id,
        data_scope=1,
        allowed_tables=[],
    )
