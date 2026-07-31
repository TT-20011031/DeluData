"""Unified organization and authorization center API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import distinct, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.api.deps import get_async_db
from app.core.security.auth import User
from app.core.security.auth import get_password_hash
from app.core.security.capabilities import CAPABILITIES, canonicalize_capabilities
from app.core.security.rbac_deps import CheckAnyPerm, CheckPerm, require_capability
from app.models.auth.authorization import (
    AssignmentModel,
    AuthorizationAuditEventModel,
    AuthorizationExceptionModel,
    PositionModel,
    RoleBindingModel,
)
from app.models.auth.organization import DepartmentModel
from app.models.auth.rbac import PermissionModel, RoleModel, UserModel
from app.models.auth.workspace import WorkspaceModel
from app.services.authorization_service import (
    build_effective_access_context,
    bump_authorization_revision,
    has_capability,
    record_authorization_audit,
    resolve_scope_org_ids,
    validate_exception_window,
)
from app.services.organization_semantic_service import get_organization_semantic_service

router = APIRouter(prefix="/authorization", tags=["组织权限中心"])


class OrgUnitWrite(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    code: str | None = Field(default=None, max_length=32)
    parent_id: int | None = None
    leader_id: str | None = None
    order_num: int = 0
    status: bool = True


class PositionWrite(BaseModel):
    org_unit_id: int
    name: str = Field(min_length=1, max_length=96)
    code: str | None = Field(default=None, max_length=64)
    status: bool = True


class AssignmentWrite(BaseModel):
    user_id: str
    position_id: int
    is_primary: bool = False
    starts_at: datetime
    ends_at: datetime | None = None
    status: bool = True

    @model_validator(mode="after")
    def validate_dates(self):
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class UserCreateWrite(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=128)
    position_id: int
    starts_at: datetime
    ends_at: datetime | None = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class UserPatchWrite(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=64)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=128)
    disabled: bool | None = None


class RoleWrite(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None
    permission_codes: list[str] = Field(default_factory=list)


class RoleBindingWrite(BaseModel):
    position_id: int
    role_id: int
    scope_type: Literal["workspace", "org_unit", "org_tree", "custom", "self"] = "self"
    scope_org_unit_id: int | None = None
    custom_org_unit_ids: list[int] = Field(default_factory=list)
    starts_at: datetime
    ends_at: datetime | None = None
    status: bool = True

    @model_validator(mode="after")
    def validate_dates(self):
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.scope_type == "custom" and not self.custom_org_unit_ids:
            raise ValueError("custom scope requires organization units")
        return self


class ExceptionWrite(BaseModel):
    user_id: str
    effect_type: Literal["allow", "deny"]
    capability_codes: list[str] = Field(default_factory=list)
    scope_type: Literal["workspace", "org_unit", "org_tree", "custom", "self"] = "self"
    scope_org_unit_ids: list[int] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1000)
    owner_id: str
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def validate_exception(self):
        validate_exception_window(self.starts_at, self.ends_at)
        unknown = canonicalize_capabilities(self.capability_codes) - set(CAPABILITIES)
        if unknown:
            raise ValueError(f"unknown capabilities: {sorted(unknown)}")
        return self


def _org_payload(row: DepartmentModel) -> dict[str, Any]:
    return {
        "id": row.id, "workspace_id": row.workspace_id, "parent_id": row.parent_id,
        "name": row.name, "code": row.code, "leader_id": row.leader_id,
        "ancestors": row.ancestors, "level": row.level, "order_num": row.order_num,
        "status": bool(row.status),
    }


async def _get_org(db: AsyncSession, workspace_id: str, org_id: int) -> DepartmentModel:
    result = await db.execute(select(DepartmentModel).where(DepartmentModel.id == org_id, DepartmentModel.workspace_id == workspace_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="organization_not_found")
    return row


async def _audit(
    db: AsyncSession, user: User, action: str, target_type: str,
    target_id: int | str | None, before: dict[str, Any] | None,
    after: dict[str, Any] | None, reason: str | None = None,
) -> dict[str, int]:
    event = await record_authorization_audit(
        db, workspace_id=user.workspace_id, actor_id=user.id, action=action,
        target_type=target_type, target_id=target_id, before=before, after=after, reason=reason,
    )
    revision = await bump_authorization_revision(db, user.workspace_id)
    return {"audit_id": event.id, "revision": revision}


async def _visible_org_ids(
    db: AsyncSession, user: User, capability: str,
) -> list[int] | None:
    scope = user.capability_scopes.get(capability)
    if scope is None and "*" in user.permissions:
        scope = "*"
    denied = user.denied_capability_scopes.get(capability)
    if denied == "*":
        return []
    if scope == "*":
        if not denied:
            return None
        all_ids = (await db.execute(select(DepartmentModel.id).where(
            DepartmentModel.workspace_id == user.workspace_id,
        ))).scalars().all()
        return sorted(set(int(item) for item in all_ids) - set(denied or []))
    visible = set(scope or [])
    visible.difference_update(denied or [])
    return sorted(visible)


def _require_workspace_capability(user: User, capability: str) -> None:
    allowed = user.capability_scopes.get(capability)
    denied = user.denied_capability_scopes.get(capability)
    if allowed != "*" or denied:
        raise HTTPException(status_code=403, detail="workspace_scope_required")


@router.get("/capabilities")
async def list_capabilities(_: User = Depends(CheckAnyPerm(["authorization:view", "role:view"]))):
    return [{"code": code, "module": value[0], "description": value[1]} for code, value in sorted(CAPABILITIES.items())]


@router.get("/org-units")
async def list_org_units(user: User = Depends(CheckPerm("org:view")), db: AsyncSession = Depends(get_async_db)):
    query = select(DepartmentModel).where(DepartmentModel.workspace_id == user.workspace_id)
    visible = await _visible_org_ids(db, user, "org:view")
    if visible is not None:
        query = query.where(DepartmentModel.id.in_(visible or [-1]))
    result = await db.execute(query.order_by(DepartmentModel.ancestors, DepartmentModel.order_num, DepartmentModel.id))
    return [_org_payload(row) for row in result.scalars().all()]


@router.post("/org-units", status_code=201)
async def create_org_unit(request: OrgUnitWrite, user: User = Depends(CheckPerm("org:manage")), db: AsyncSession = Depends(get_async_db)):
    parent = await _get_org(db, user.workspace_id, request.parent_id) if request.parent_id else None
    if parent:
        require_capability(user, "org:manage", parent.id)
    elif await _visible_org_ids(db, user, "org:manage") is not None:
        raise HTTPException(status_code=403, detail="workspace_root_creation_requires_workspace_scope")
    row = DepartmentModel(
        workspace_id=user.workspace_id, parent_id=parent.id if parent else None,
        name=request.name.strip(), code=request.code, leader_id=request.leader_id,
        ancestors=f"{parent.ancestors}{parent.id}/" if parent else "/",
        order_num=request.order_num, status=request.status,
    )
    db.add(row)
    await db.flush()
    await get_organization_semantic_service().invalidate_org(
        db, user.workspace_id, int(row.id), str(user.id),
    )
    meta = await _audit(db, user, "org.create", "org_unit", row.id, None, _org_payload(row))
    await db.commit()
    return {**_org_payload(row), **meta}


@router.patch("/org-units/{org_id}")
async def update_org_unit(org_id: int, request: OrgUnitWrite, user: User = Depends(CheckPerm("org:manage")), db: AsyncSession = Depends(get_async_db)):
    row = await _get_org(db, user.workspace_id, org_id)
    require_capability(user, "org:manage", org_id)
    before = _org_payload(row)
    parent = await _get_org(db, user.workspace_id, request.parent_id) if request.parent_id else None
    if parent:
        require_capability(user, "org:manage", parent.id)
    old_prefix = f"{row.ancestors}{row.id}/"
    if parent and (parent.id == row.id or parent.ancestors.startswith(old_prefix)):
        raise HTTPException(status_code=409, detail="organization_cycle")
    new_ancestors = f"{parent.ancestors}{parent.id}/" if parent else "/"
    if new_ancestors != row.ancestors:
        descendants = await db.execute(select(DepartmentModel).where(DepartmentModel.workspace_id == user.workspace_id, DepartmentModel.ancestors.like(f"{old_prefix}%")))
        new_prefix = f"{new_ancestors}{row.id}/"
        for child in descendants.scalars().all():
            child.ancestors = child.ancestors.replace(old_prefix, new_prefix, 1)
    row.parent_id = parent.id if parent else None
    row.ancestors = new_ancestors
    row.name, row.code, row.leader_id = request.name.strip(), request.code, request.leader_id
    row.order_num, row.status = request.order_num, request.status
    await get_organization_semantic_service().invalidate_org(
        db, user.workspace_id, int(row.id), str(user.id),
    )
    meta = await _audit(db, user, "org.update", "org_unit", row.id, before, _org_payload(row))
    await db.commit()
    return {**_org_payload(row), **meta}


@router.delete("/org-units/{org_id}", status_code=204)
async def delete_org_unit(org_id: int, user: User = Depends(CheckPerm("org:manage")), db: AsyncSession = Depends(get_async_db)):
    row = await _get_org(db, user.workspace_id, org_id)
    require_capability(user, "org:manage", org_id)
    child_count = int((await db.execute(
        select(func.count(DepartmentModel.id)).where(
            DepartmentModel.workspace_id == user.workspace_id,
            DepartmentModel.parent_id == org_id,
        )
    )).scalar_one() or 0)
    position_count = int((await db.execute(
        select(func.count(PositionModel.id)).where(
            PositionModel.workspace_id == user.workspace_id,
            PositionModel.org_unit_id == org_id,
        )
    )).scalar_one() or 0)
    if child_count or position_count:
        raise HTTPException(status_code=409, detail={
            "code": "organization_in_use",
            "blockers": {
                "child_org_units": child_count,
                "positions": position_count,
            },
        })
    before = _org_payload(row)
    await db.delete(row)
    await db.flush()
    await get_organization_semantic_service().invalidate_workspace_org_set(
        db, user.workspace_id, str(user.id),
    )
    await _audit(db, user, "org.delete", "org_unit", org_id, before, None)
    await db.commit()


@router.get("/positions")
async def list_positions(org_unit_id: int | None = None, user: User = Depends(CheckPerm("org:view")), db: AsyncSession = Depends(get_async_db)):
    now = datetime.utcnow()
    headcount = (
        select(func.count(distinct(AssignmentModel.user_id)))
        .where(
            AssignmentModel.position_id == PositionModel.id,
            AssignmentModel.workspace_id == user.workspace_id,
            AssignmentModel.status == True,  # noqa: E712
            AssignmentModel.starts_at <= now,
            or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
        )
        .correlate(PositionModel)
        .scalar_subquery()
    )
    query = select(PositionModel, headcount.label("headcount")).where(PositionModel.workspace_id == user.workspace_id)
    visible = await _visible_org_ids(db, user, "org:view")
    if visible is not None:
        query = query.where(PositionModel.org_unit_id.in_(visible or [-1]))
    if org_unit_id is not None:
        require_capability(user, "org:view", org_unit_id)
        query = query.where(PositionModel.org_unit_id == org_unit_id)
    result = await db.execute(query.order_by(PositionModel.name))
    return [{"id": row.id, "org_unit_id": row.org_unit_id, "name": row.name, "code": row.code, "status": bool(row.status), "legacy_generated": bool(row.legacy_generated), "headcount": int(count or 0)} for row, count in result.all()]


@router.post("/positions", status_code=201)
async def create_position(request: PositionWrite, user: User = Depends(CheckPerm("org:manage")), db: AsyncSession = Depends(get_async_db)):
    org = await _get_org(db, user.workspace_id, request.org_unit_id)
    if not org.status:
        raise HTTPException(status_code=409, detail="organization_disabled")
    require_capability(user, "org:manage", request.org_unit_id)
    row = PositionModel(workspace_id=user.workspace_id, **request.model_dump())
    db.add(row)
    await db.flush()
    await get_organization_semantic_service().invalidate_org(
        db, user.workspace_id, int(row.org_unit_id), str(user.id),
    )
    payload = {"id": row.id, **request.model_dump(mode="json")}
    meta = await _audit(db, user, "position.create", "position", row.id, None, payload)
    await db.commit()
    return {**payload, **meta}


@router.patch("/positions/{position_id}")
async def update_position(position_id: int, request: PositionWrite, user: User = Depends(CheckPerm("org:manage")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(PositionModel).where(PositionModel.id == position_id, PositionModel.workspace_id == user.workspace_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="position_not_found")
    require_capability(user, "org:manage", row.org_unit_id)
    target_org = await _get_org(db, user.workspace_id, request.org_unit_id)
    if not target_org.status:
        raise HTTPException(status_code=409, detail="organization_disabled")
    require_capability(user, "org:manage", request.org_unit_id)
    before = {"org_unit_id": row.org_unit_id, "name": row.name, "code": row.code, "status": row.status}
    previous_org_unit_id = int(row.org_unit_id)
    for key, value in request.model_dump().items(): setattr(row, key, value)
    await get_organization_semantic_service().invalidate_org(
        db, user.workspace_id, int(row.org_unit_id), str(user.id),
    )
    if previous_org_unit_id != int(row.org_unit_id):
        await get_organization_semantic_service().invalidate_org(
            db, user.workspace_id, previous_org_unit_id, str(user.id),
        )
    after = request.model_dump(mode="json")
    meta = await _audit(db, user, "position.update", "position", row.id, before, after)
    await db.commit()
    return {"id": row.id, **after, **meta}


@router.delete("/positions/{position_id}", status_code=204)
async def delete_position(position_id: int, user: User = Depends(CheckPerm("org:manage")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(PositionModel).where(PositionModel.id == position_id, PositionModel.workspace_id == user.workspace_id))
    row = result.scalar_one_or_none()
    if row is None: raise HTTPException(status_code=404, detail="position_not_found")
    require_capability(user, "org:manage", row.org_unit_id)
    assignment_count = int((await db.execute(
        select(func.count(AssignmentModel.id)).where(
            AssignmentModel.workspace_id == user.workspace_id,
            AssignmentModel.position_id == position_id,
        )
    )).scalar_one() or 0)
    role_binding_count = int((await db.execute(
        select(func.count(RoleBindingModel.id)).where(
            RoleBindingModel.workspace_id == user.workspace_id,
            RoleBindingModel.position_id == position_id,
        )
    )).scalar_one() or 0)
    if assignment_count or role_binding_count:
        raise HTTPException(status_code=409, detail={
            "code": "position_in_use",
            "blockers": {
                "assignments": assignment_count,
                "role_bindings": role_binding_count,
            },
        })
    org_unit_id = int(row.org_unit_id)
    await db.delete(row)
    await get_organization_semantic_service().invalidate_org(
        db, user.workspace_id, org_unit_id, str(user.id),
    )
    await _audit(db, user, "position.delete", "position", row.id, {"name": row.name}, None)
    await db.commit()


@router.get("/users")
async def list_users(
    assignment_state: Literal["unassigned"] | None = None,
    user: User = Depends(CheckAnyPerm(["user:view", "semantic_access:view"])),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(UserModel).where(UserModel.workspace_id == user.workspace_id)
    scope_capability = "user:view" if "user:view" in user.capability_scopes else "semantic_access:view"
    if assignment_state == "unassigned":
        _require_workspace_capability(user, scope_capability)
        now = datetime.utcnow()
        effective_assignment = (
            select(AssignmentModel.id)
            .join(PositionModel, PositionModel.id == AssignmentModel.position_id)
            .where(
                AssignmentModel.workspace_id == user.workspace_id,
                AssignmentModel.user_id == UserModel.id,
                AssignmentModel.status == True,  # noqa: E712
                AssignmentModel.starts_at <= now,
                or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
                PositionModel.status == True,  # noqa: E712
            )
        )
        result = await db.execute(query.where(~exists(effective_assignment)).order_by(UserModel.username))
        return [{"id": row.id, "username": row.username, "email": row.email, "disabled": bool(row.disabled)} for row in result.scalars().all()]
    visible = await _visible_org_ids(db, user, scope_capability)
    if visible is not None:
        now = datetime.utcnow()
        query = query.join(AssignmentModel, AssignmentModel.user_id == UserModel.id).join(
            PositionModel, PositionModel.id == AssignmentModel.position_id
        ).where(
            AssignmentModel.status == True,  # noqa: E712
            AssignmentModel.starts_at <= now,
            or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
            PositionModel.status == True,  # noqa: E712
            PositionModel.org_unit_id.in_(visible or [-1]),
        )
        outside_assignment = aliased(AssignmentModel)
        outside_position = aliased(PositionModel)
        outside_scope = (
            select(outside_assignment.id)
            .join(outside_position, outside_position.id == outside_assignment.position_id)
            .where(
                outside_assignment.user_id == UserModel.id,
                outside_assignment.workspace_id == user.workspace_id,
                outside_assignment.status == True,  # noqa: E712
                outside_assignment.starts_at <= now,
                or_(outside_assignment.ends_at.is_(None), outside_assignment.ends_at > now),
                outside_position.status == True,  # noqa: E712
                outside_position.org_unit_id.not_in(visible or [-1]),
            )
        )
        query = query.where(~exists(outside_scope)).distinct()
    result = await db.execute(query.order_by(UserModel.username))
    return [{"id": row.id, "username": row.username, "email": row.email, "disabled": bool(row.disabled)} for row in result.scalars().all()]


async def _require_target_user_scope(
    db: AsyncSession, user: User, target_user_id: str, capability: str,
) -> list[int]:
    result = await db.execute(
        select(PositionModel.org_unit_id)
        .join(AssignmentModel, AssignmentModel.position_id == PositionModel.id)
        .where(
            AssignmentModel.workspace_id == user.workspace_id,
            AssignmentModel.user_id == target_user_id,
            AssignmentModel.status == True,  # noqa: E712
        )
    )
    org_ids = sorted({int(item) for item in result.scalars().all()})
    if not org_ids:
        _require_workspace_capability(user, capability)
    for org_id in org_ids:
        require_capability(user, capability, org_id)
    return org_ids


@router.post("/users", status_code=201)
async def create_user_with_primary_assignment(
    request: UserCreateWrite,
    user: User = Depends(CheckPerm("user:manage")),
    db: AsyncSession = Depends(get_async_db),
):
    position = (await db.execute(select(PositionModel).where(
        PositionModel.id == request.position_id,
        PositionModel.workspace_id == user.workspace_id,
        PositionModel.status == True,  # noqa: E712
    ))).scalar_one_or_none()
    if position is None:
        raise HTTPException(status_code=404, detail="position_not_found")
    require_capability(user, "user:manage", int(position.org_unit_id))
    max_users = (await db.execute(select(WorkspaceModel.max_users).where(
        WorkspaceModel.id == user.workspace_id,
    ))).scalar_one_or_none()
    current_users = int((await db.execute(select(func.count(UserModel.id)).where(
        UserModel.workspace_id == user.workspace_id,
        UserModel.disabled == False,  # noqa: E712
    ))).scalar_one() or 0)
    if max_users is not None and current_users >= int(max_users):
        raise HTTPException(status_code=409, detail="workspace_user_limit_reached")
    if (await db.execute(select(UserModel.id).where(UserModel.username == request.username.strip()))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="username_exists")
    row = UserModel(
        id=str(uuid.uuid4()), username=request.username.strip(), email=request.email,
        hashed_password=get_password_hash(request.password), workspace_id=user.workspace_id,
        disabled=False, department_id=None,
    )
    db.add(row)
    await db.flush()
    assignment = AssignmentModel(
        workspace_id=user.workspace_id, user_id=row.id, position_id=position.id,
        is_primary=True, starts_at=request.starts_at, ends_at=request.ends_at, status=True,
    )
    db.add(assignment)
    await db.flush()
    payload = {
        "id": row.id, "username": row.username, "email": row.email,
        "disabled": False, "primary_assignment_id": assignment.id,
        "position_id": position.id,
    }
    meta = await _audit(db, user, "user.create", "user", row.id, None, payload)
    await db.commit()
    return {**payload, **meta}


@router.patch("/users/{target_user_id}")
async def update_user_account(
    target_user_id: str,
    request: UserPatchWrite,
    user: User = Depends(CheckPerm("user:manage")),
    db: AsyncSession = Depends(get_async_db),
):
    row = (await db.execute(select(UserModel).where(
        UserModel.id == target_user_id,
        UserModel.workspace_id == user.workspace_id,
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    await _require_target_user_scope(db, user, target_user_id, "user:manage")
    before = {"username": row.username, "email": row.email, "disabled": bool(row.disabled)}
    if request.username is not None and request.username.strip() != row.username:
        if (await db.execute(select(UserModel.id).where(UserModel.username == request.username.strip()))).scalar_one_or_none():
            raise HTTPException(status_code=409, detail="username_exists")
        row.username = request.username.strip()
    if "email" in request.model_fields_set:
        row.email = request.email
    if request.password:
        row.hashed_password = get_password_hash(request.password)
    if request.disabled is not None:
        owner_id = (await db.execute(select(WorkspaceModel.owner_id).where(
            WorkspaceModel.id == user.workspace_id,
        ))).scalar_one_or_none()
        if request.disabled and owner_id == target_user_id:
            raise HTTPException(status_code=409, detail="workspace_owner_cannot_be_disabled")
        row.disabled = request.disabled
        if request.disabled:
            assignments = await db.execute(select(AssignmentModel).where(
                AssignmentModel.workspace_id == user.workspace_id,
                AssignmentModel.user_id == target_user_id,
                AssignmentModel.status == True,  # noqa: E712
            ))
            for assignment in assignments.scalars().all():
                assignment.status = False
    after = {"username": row.username, "email": row.email, "disabled": bool(row.disabled)}
    meta = await _audit(db, user, "user.update", "user", row.id, before, after)
    await db.commit()
    return {"id": row.id, **after, **meta}


@router.delete("/users/{target_user_id}", status_code=204)
async def disable_user_account(
    target_user_id: str,
    reason: str = Query(min_length=1),
    user: User = Depends(CheckPerm("user:manage")),
    db: AsyncSession = Depends(get_async_db),
):
    row = (await db.execute(select(UserModel).where(
        UserModel.id == target_user_id,
        UserModel.workspace_id == user.workspace_id,
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    await _require_target_user_scope(db, user, target_user_id, "user:manage")
    owner_id = (await db.execute(select(WorkspaceModel.owner_id).where(
        WorkspaceModel.id == user.workspace_id,
    ))).scalar_one_or_none()
    if owner_id == target_user_id:
        raise HTTPException(status_code=409, detail="workspace_owner_cannot_be_disabled")
    row.disabled = True
    assignments = await db.execute(select(AssignmentModel).where(
        AssignmentModel.workspace_id == user.workspace_id,
        AssignmentModel.user_id == target_user_id,
        AssignmentModel.status == True,  # noqa: E712
    ))
    for assignment in assignments.scalars().all():
        assignment.status = False
    await _audit(
        db, user, "user.disable", "user", row.id,
        {"disabled": False}, {"disabled": True}, reason,
    )
    await db.commit()


async def _validate_assignment(db: AsyncSession, workspace_id: str, payload: AssignmentWrite, exclude_id: int | None = None):
    # Lock the user row so concurrent requests cannot create overlapping
    # primary assignments after both pass the uniqueness check.
    user_exists = (await db.execute(select(UserModel.id).where(
        UserModel.id == payload.user_id,
        UserModel.workspace_id == workspace_id,
    ).with_for_update())).scalar_one_or_none()
    position_exists = (await db.execute(select(PositionModel.id).where(
        PositionModel.id == payload.position_id,
        PositionModel.workspace_id == workspace_id,
        PositionModel.status == True,  # noqa: E712
    ))).scalar_one_or_none()
    if user_exists is None or position_exists is None: raise HTTPException(status_code=404, detail="user_or_position_not_found")
    if payload.status:
        same_position_overlap = select(AssignmentModel.id).where(
            AssignmentModel.workspace_id == workspace_id,
            AssignmentModel.user_id == payload.user_id,
            AssignmentModel.position_id == payload.position_id,
            AssignmentModel.status == True,  # noqa: E712
            or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > payload.starts_at),
            or_(payload.ends_at is None, AssignmentModel.starts_at < payload.ends_at),
        )
        if exclude_id is not None:
            same_position_overlap = same_position_overlap.where(AssignmentModel.id != exclude_id)
        if (await db.execute(same_position_overlap.limit(1))).scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="assignment_overlap")
    if payload.is_primary and payload.status:
        overlap = select(AssignmentModel.id).where(
            AssignmentModel.workspace_id == workspace_id, AssignmentModel.user_id == payload.user_id,
            AssignmentModel.is_primary == True, AssignmentModel.status == True,  # noqa: E712
            or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > payload.starts_at),
            or_(payload.ends_at is None, AssignmentModel.starts_at < payload.ends_at),
        )
        if exclude_id is not None: overlap = overlap.where(AssignmentModel.id != exclude_id)
        if (await db.execute(overlap.limit(1))).scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="primary_assignment_overlap")


@router.get("/assignments")
async def list_assignments(
    user_id: str | None = None,
    position_id: int | None = None,
    state: Literal["effective", "upcoming", "expired", "disabled", "all"] = "all",
    user: User = Depends(CheckPerm("user:view")),
    db: AsyncSession = Depends(get_async_db),
):
    now = datetime.utcnow()
    query = (
        select(AssignmentModel, PositionModel, UserModel)
        .join(PositionModel, PositionModel.id == AssignmentModel.position_id)
        .join(UserModel, UserModel.id == AssignmentModel.user_id)
        .where(AssignmentModel.workspace_id == user.workspace_id)
    )
    visible = await _visible_org_ids(db, user, "user:view")
    if visible is not None:
        query = query.where(PositionModel.org_unit_id.in_(visible or [-1]))
    if user_id: query = query.where(AssignmentModel.user_id == user_id)
    if position_id: query = query.where(AssignmentModel.position_id == position_id)
    if state == "effective":
        query = query.where(
            AssignmentModel.status == True,  # noqa: E712
            AssignmentModel.starts_at <= now,
            or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
            PositionModel.status == True,  # noqa: E712
            UserModel.disabled == False,  # noqa: E712
        )
    elif state == "upcoming":
        query = query.where(
            AssignmentModel.status == True,  # noqa: E712
            AssignmentModel.starts_at > now,
            UserModel.disabled == False,  # noqa: E712
        )
    elif state == "expired":
        query = query.where(AssignmentModel.ends_at.is_not(None), AssignmentModel.ends_at <= now)
    elif state == "disabled":
        query = query.where(or_(
            AssignmentModel.status == False,  # noqa: E712
            PositionModel.status == False,  # noqa: E712
            UserModel.disabled == True,  # noqa: E712
        ))
    result = await db.execute(query.order_by(AssignmentModel.is_primary.desc(), AssignmentModel.starts_at.desc()))
    return [{
        "id": a.id,
        "user_id": a.user_id,
        "username": account.username,
        "email": account.email,
        "disabled": bool(account.disabled),
        "position_id": p.id,
        "position_name": p.name,
        "org_unit_id": p.org_unit_id,
        "is_primary": bool(a.is_primary),
        "starts_at": a.starts_at,
        "ends_at": a.ends_at,
        "status": bool(a.status),
    } for a, p, account in result.all()]


@router.post("/assignments", status_code=201)
async def create_assignment(request: AssignmentWrite, user: User = Depends(CheckPerm("user:manage")), db: AsyncSession = Depends(get_async_db)):
    await _validate_assignment(db, user.workspace_id, request)
    position_org_id = (await db.execute(select(PositionModel.org_unit_id).where(
        PositionModel.id == request.position_id,
        PositionModel.workspace_id == user.workspace_id,
    ))).scalar_one()
    require_capability(user, "user:manage", int(position_org_id))
    row = AssignmentModel(workspace_id=user.workspace_id, **request.model_dump())
    db.add(row); await db.flush()
    payload = {"id": row.id, **request.model_dump(mode="json")}
    meta = await _audit(db, user, "assignment.create", "assignment", row.id, None, payload)
    await db.commit(); return {**payload, **meta}


@router.patch("/assignments/{assignment_id}")
async def update_assignment(assignment_id: int, request: AssignmentWrite, user: User = Depends(CheckPerm("user:manage")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(AssignmentModel).where(AssignmentModel.id == assignment_id, AssignmentModel.workspace_id == user.workspace_id))
    row = result.scalar_one_or_none()
    if row is None: raise HTTPException(status_code=404, detail="assignment_not_found")
    old_org_id = (await db.execute(select(PositionModel.org_unit_id).where(PositionModel.id == row.position_id))).scalar_one()
    require_capability(user, "user:manage", int(old_org_id))
    await _validate_assignment(db, user.workspace_id, request, assignment_id)
    new_org_id = (await db.execute(select(PositionModel.org_unit_id).where(PositionModel.id == request.position_id))).scalar_one()
    require_capability(user, "user:manage", int(new_org_id))
    before = {"user_id": row.user_id, "position_id": row.position_id, "is_primary": row.is_primary, "starts_at": str(row.starts_at), "ends_at": str(row.ends_at)}
    for key, value in request.model_dump().items(): setattr(row, key, value)
    after = request.model_dump(mode="json")
    meta = await _audit(db, user, "assignment.update", "assignment", row.id, before, after)
    await db.commit(); return {"id": row.id, **after, **meta}


@router.delete("/assignments/{assignment_id}", status_code=204)
async def delete_assignment(assignment_id: int, user: User = Depends(CheckPerm("user:manage")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(AssignmentModel).where(AssignmentModel.id == assignment_id, AssignmentModel.workspace_id == user.workspace_id))
    row = result.scalar_one_or_none()
    if row is None: raise HTTPException(status_code=404, detail="assignment_not_found")
    org_id = (await db.execute(select(PositionModel.org_unit_id).where(PositionModel.id == row.position_id))).scalar_one()
    require_capability(user, "user:manage", int(org_id))
    await db.delete(row); await _audit(db, user, "assignment.delete", "assignment", row.id, {"user_id": row.user_id, "position_id": row.position_id}, None); await db.commit()


@router.get("/roles")
async def list_roles(user: User = Depends(CheckPerm("role:view")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(RoleModel).options(selectinload(RoleModel.permissions)).where(RoleModel.workspace_id == user.workspace_id).order_by(RoleModel.name))
    return [{"id": role.id, "name": role.name, "description": role.description, "is_system": bool(role.is_system), "permission_codes": sorted(canonicalize_capabilities(p.code for p in role.permissions))} for role in result.unique().scalars().all()]


async def _permissions(db: AsyncSession, codes: list[str]) -> list[PermissionModel]:
    canonical = canonicalize_capabilities(codes)
    unknown = canonical - set(CAPABILITIES)
    if unknown: raise HTTPException(status_code=422, detail={"unknown_capabilities": sorted(unknown)})
    result = await db.execute(select(PermissionModel).where(PermissionModel.code.in_(canonical)))
    permissions = list(result.scalars().all())
    if len(permissions) != len(canonical): raise HTTPException(status_code=409, detail="capability_catalog_not_initialized")
    return permissions


async def _require_role_management_scope(
    db: AsyncSession, user: User, role_id: int,
) -> None:
    rows = (await db.execute(
        select(RoleBindingModel, PositionModel)
        .join(PositionModel, PositionModel.id == RoleBindingModel.position_id)
        .where(
            RoleBindingModel.workspace_id == user.workspace_id,
            RoleBindingModel.role_id == role_id,
            RoleBindingModel.status == True,  # noqa: E712
        )
    )).all()
    if not rows:
        return
    for binding, position in rows:
        require_capability(user, "role:manage", int(position.org_unit_id))
        scope_ids = await resolve_scope_org_ids(
            db, user.workspace_id, binding.scope_type,
            binding.scope_org_unit_id or position.org_unit_id,
            binding.custom_org_unit_ids,
        )
        for org_id in scope_ids:
            require_capability(user, "role:manage", org_id)


@router.post("/roles", status_code=201)
async def create_role(request: RoleWrite, user: User = Depends(CheckPerm("role:manage")), db: AsyncSession = Depends(get_async_db)):
    role = RoleModel(name=request.name.strip(), description=request.description, workspace_id=user.workspace_id, is_system=False, data_scope=4)
    role.permissions = await _permissions(db, request.permission_codes)
    db.add(role); await db.flush()
    payload = {"id": role.id, **request.model_dump(mode="json")}
    meta = await _audit(db, user, "role.create", "role", role.id, None, payload)
    await db.commit(); return {**payload, **meta}


@router.patch("/roles/{role_id}")
async def update_role(role_id: int, request: RoleWrite, user: User = Depends(CheckPerm("role:manage")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(RoleModel).options(selectinload(RoleModel.permissions)).where(RoleModel.id == role_id, RoleModel.workspace_id == user.workspace_id))
    role = result.unique().scalar_one_or_none()
    if role is None: raise HTTPException(status_code=404, detail="role_not_found")
    if role.is_system: raise HTTPException(status_code=409, detail="system_role_immutable")
    await _require_role_management_scope(db, user, role_id)
    before = {"name": role.name, "description": role.description, "permission_codes": [p.code for p in role.permissions]}
    role.name, role.description = request.name.strip(), request.description
    role.permissions = await _permissions(db, request.permission_codes)
    affected_org_ids = set((await db.execute(
        select(PositionModel.org_unit_id)
        .join(RoleBindingModel, RoleBindingModel.position_id == PositionModel.id)
        .where(
            RoleBindingModel.workspace_id == user.workspace_id,
            RoleBindingModel.role_id == role_id,
            RoleBindingModel.status == True,  # noqa: E712
        )
    )).scalars())
    for org_unit_id in affected_org_ids:
        await get_organization_semantic_service().invalidate_org(
            db, user.workspace_id, int(org_unit_id), str(user.id),
        )
    after = request.model_dump(mode="json")
    meta = await _audit(db, user, "role.update", "role", role.id, before, after)
    await db.commit(); return {"id": role.id, **after, **meta}


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(role_id: int, user: User = Depends(CheckPerm("role:manage")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(RoleModel).where(RoleModel.id == role_id, RoleModel.workspace_id == user.workspace_id))
    row = result.scalar_one_or_none()
    if row is None: raise HTTPException(status_code=404, detail="role_not_found")
    if row.is_system: raise HTTPException(status_code=409, detail="system_role_immutable")
    await _require_role_management_scope(db, user, role_id)
    if (await db.execute(select(RoleBindingModel.id).where(RoleBindingModel.role_id == role_id).limit(1))).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="role_in_use")
    await db.delete(row); await _audit(db, user, "role.delete", "role", row.id, {"name": row.name}, None); await db.commit()


@router.get("/role-bindings")
async def list_role_bindings(user: User = Depends(CheckPerm("authorization:view")), db: AsyncSession = Depends(get_async_db)):
    now = datetime.utcnow()
    affected_users = (
        select(func.count(distinct(AssignmentModel.user_id)))
        .join(UserModel, UserModel.id == AssignmentModel.user_id)
        .where(
            AssignmentModel.position_id == PositionModel.id,
            AssignmentModel.workspace_id == user.workspace_id,
            AssignmentModel.status == True,  # noqa: E712
            AssignmentModel.starts_at <= now,
            or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
            UserModel.disabled == False,  # noqa: E712
        )
        .correlate(PositionModel)
        .scalar_subquery()
    )
    query = select(RoleBindingModel, PositionModel, RoleModel, affected_users.label("affected_user_count")).join(PositionModel, PositionModel.id == RoleBindingModel.position_id).join(RoleModel, RoleModel.id == RoleBindingModel.role_id).where(RoleBindingModel.workspace_id == user.workspace_id)
    visible = await _visible_org_ids(db, user, "authorization:view")
    if visible is not None:
        query = query.where(PositionModel.org_unit_id.in_(visible or [-1]))
    result = await db.execute(query.order_by(PositionModel.name, RoleModel.name))
    rows = result.all()
    if visible is not None:
        visible_set = set(visible)
        scoped_rows = []
        for binding, position, role, affected_user_count in rows:
            scope_ids = await resolve_scope_org_ids(
                db, user.workspace_id, binding.scope_type,
                binding.scope_org_unit_id or position.org_unit_id,
                binding.custom_org_unit_ids,
            )
            if set(scope_ids).issubset(visible_set):
                scoped_rows.append((binding, position, role, affected_user_count))
        rows = scoped_rows
    return [{"id": b.id, "position_id": p.id, "position_name": p.name, "org_unit_id": p.org_unit_id, "role_id": r.id, "role_name": r.name, "scope_type": b.scope_type, "scope_org_unit_id": b.scope_org_unit_id, "custom_org_unit_ids": b.custom_org_unit_ids or [], "starts_at": b.starts_at, "ends_at": b.ends_at, "status": bool(b.status), "affected_user_count": int(affected or 0) if b.status and b.starts_at <= now and (b.ends_at is None or b.ends_at > now) else 0} for b, p, r, affected in rows]


async def _validate_binding(db: AsyncSession, workspace_id: str, request: RoleBindingWrite):
    position = (await db.execute(select(PositionModel).where(
        PositionModel.id == request.position_id,
        PositionModel.workspace_id == workspace_id,
        PositionModel.status == True,  # noqa: E712
    ))).scalar_one_or_none()
    role = (await db.execute(select(RoleModel).where(RoleModel.id == request.role_id, RoleModel.workspace_id == workspace_id))).scalar_one_or_none()
    if position is None or role is None: raise HTTPException(status_code=404, detail="position_or_role_not_found")
    org_status = (await db.execute(select(DepartmentModel.status).where(
        DepartmentModel.id == position.org_unit_id,
        DepartmentModel.workspace_id == workspace_id,
    ))).scalar_one_or_none()
    if not org_status:
        raise HTTPException(status_code=409, detail="organization_disabled")
    await resolve_scope_org_ids(db, workspace_id, request.scope_type, request.scope_org_unit_id or position.org_unit_id, request.custom_org_unit_ids)


@router.post("/role-bindings", status_code=201)
async def create_role_binding(request: RoleBindingWrite, user: User = Depends(CheckPerm("authorization:manage")), db: AsyncSession = Depends(get_async_db)):
    await _validate_binding(db, user.workspace_id, request)
    position_org_id = (await db.execute(select(PositionModel.org_unit_id).where(PositionModel.id == request.position_id))).scalar_one()
    require_capability(user, "authorization:manage", int(position_org_id))
    scope_ids = await resolve_scope_org_ids(db, user.workspace_id, request.scope_type, request.scope_org_unit_id or int(position_org_id), request.custom_org_unit_ids)
    for org_id in scope_ids:
        require_capability(user, "authorization:manage", org_id)
    row = RoleBindingModel(workspace_id=user.workspace_id, created_by=user.id, **request.model_dump())
    db.add(row); await db.flush()
    await get_organization_semantic_service().invalidate_org(
        db, user.workspace_id, int(position_org_id), str(user.id),
    )
    payload = {"id": row.id, **request.model_dump(mode="json")}
    meta = await _audit(db, user, "role_binding.create", "role_binding", row.id, None, payload)
    await db.commit(); return {**payload, **meta}


@router.patch("/role-bindings/{binding_id}")
async def update_role_binding(binding_id: int, request: RoleBindingWrite, user: User = Depends(CheckPerm("authorization:manage")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(RoleBindingModel).where(RoleBindingModel.id == binding_id, RoleBindingModel.workspace_id == user.workspace_id))
    row = result.scalar_one_or_none()
    if row is None: raise HTTPException(status_code=404, detail="role_binding_not_found")
    old_org_id = (await db.execute(select(PositionModel.org_unit_id).where(PositionModel.id == row.position_id))).scalar_one()
    require_capability(user, "authorization:manage", int(old_org_id))
    await _validate_binding(db, user.workspace_id, request)
    new_org_id = (await db.execute(select(PositionModel.org_unit_id).where(PositionModel.id == request.position_id))).scalar_one()
    require_capability(user, "authorization:manage", int(new_org_id))
    scope_ids = await resolve_scope_org_ids(db, user.workspace_id, request.scope_type, request.scope_org_unit_id or int(new_org_id), request.custom_org_unit_ids)
    for org_id in scope_ids:
        require_capability(user, "authorization:manage", org_id)
    before = {
        key: (value.isoformat() if isinstance(value, datetime) else value)
        for key in request.__class__.model_fields
        for value in [getattr(row, key)]
    }
    for key, value in request.model_dump().items(): setattr(row, key, value)
    await get_organization_semantic_service().invalidate_org(
        db, user.workspace_id, int(new_org_id), str(user.id),
    )
    if int(old_org_id) != int(new_org_id):
        await get_organization_semantic_service().invalidate_org(
            db, user.workspace_id, int(old_org_id), str(user.id),
        )
    after = request.model_dump(mode="json")
    meta = await _audit(db, user, "role_binding.update", "role_binding", row.id, before, after)
    await db.commit(); return {"id": row.id, **after, **meta}


@router.delete("/role-bindings/{binding_id}", status_code=204)
async def delete_role_binding(binding_id: int, user: User = Depends(CheckPerm("authorization:manage")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(RoleBindingModel).where(RoleBindingModel.id == binding_id, RoleBindingModel.workspace_id == user.workspace_id))
    row = result.scalar_one_or_none()
    if row is None: raise HTTPException(status_code=404, detail="role_binding_not_found")
    org_id = (await db.execute(select(PositionModel.org_unit_id).where(PositionModel.id == row.position_id))).scalar_one()
    require_capability(user, "authorization:manage", int(org_id))
    await db.delete(row)
    await get_organization_semantic_service().invalidate_org(
        db, user.workspace_id, int(org_id), str(user.id),
    )
    await _audit(db, user, "role_binding.delete", "role_binding", row.id, {"role_id": row.role_id, "position_id": row.position_id}, None)
    await db.commit()


@router.get("/exceptions")
async def list_exceptions(user: User = Depends(CheckPerm("authorization:view")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(AuthorizationExceptionModel).where(AuthorizationExceptionModel.workspace_id == user.workspace_id).order_by(AuthorizationExceptionModel.ends_at.desc()))
    visible = await _visible_org_ids(db, user, "authorization:view")
    rows = list(result.scalars().all())
    if visible is not None:
        visible_set = set(visible)
        rows = [
            row for row in rows
            if row.scope_type != "workspace"
            and set(row.scope_org_unit_ids or []).issubset(visible_set)
        ]
    return [{"id": row.id, "user_id": row.user_id, "effect_type": row.effect_type, "capability_codes": row.capability_codes or [], "scope_type": row.scope_type, "scope_org_unit_ids": row.scope_org_unit_ids or [], "reason": row.reason, "owner_id": row.owner_id, "starts_at": row.starts_at, "ends_at": row.ends_at, "status": bool(row.status)} for row in rows]


@router.post("/exceptions", status_code=201)
async def create_exception(request: ExceptionWrite, user: User = Depends(CheckPerm("authorization:delegate")), db: AsyncSession = Depends(get_async_db)):
    if (await db.execute(select(UserModel.id).where(UserModel.id == request.user_id, UserModel.workspace_id == user.workspace_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    if (await db.execute(select(UserModel.id).where(UserModel.id == request.owner_id, UserModel.workspace_id == user.workspace_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="owner_not_found")
    actor = await build_effective_access_context(db, user.workspace_id, user.id)
    requested = canonicalize_capabilities(request.capability_codes)
    if not requested.issubset(actor.capability_scopes): raise HTTPException(status_code=403, detail="delegation_exceeds_capabilities")
    if request.scope_type == "workspace":
        requested_org_ids = await resolve_scope_org_ids(db, user.workspace_id, "workspace", None)
    else:
        if not request.scope_org_unit_ids:
            raise HTTPException(status_code=422, detail="organization_scope_required")
        requested_org_ids = await resolve_scope_org_ids(
            db, user.workspace_id, "custom", None, request.scope_org_unit_ids
        )
    if not all(has_capability(actor, "authorization:delegate", org_id) for org_id in requested_org_ids):
        raise HTTPException(status_code=403, detail="delegation_exceeds_organization_scope")
    for code in requested:
        if not all(has_capability(actor, code, org_id) for org_id in requested_org_ids):
            raise HTTPException(status_code=403, detail={"delegation_exceeds_capability_scope": code})
    row = AuthorizationExceptionModel(workspace_id=user.workspace_id, status=True, created_by=user.id, **request.model_dump())
    row.capability_codes = sorted(requested)
    row.scope_org_unit_ids = requested_org_ids
    db.add(row); await db.flush()
    payload = {"id": row.id, **request.model_dump(mode="json"), "capability_codes": row.capability_codes}
    meta = await _audit(db, user, "exception.create", "authorization_exception", row.id, None, payload, request.reason)
    await db.commit(); return {**payload, **meta}


@router.delete("/exceptions/{exception_id}", status_code=204)
async def revoke_exception(exception_id: int, reason: str = Query(min_length=1), user: User = Depends(CheckPerm("authorization:delegate")), db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(AuthorizationExceptionModel).where(AuthorizationExceptionModel.id == exception_id, AuthorizationExceptionModel.workspace_id == user.workspace_id))
    row = result.scalar_one_or_none()
    if row is None: raise HTTPException(status_code=404, detail="exception_not_found")
    if row.scope_type == "workspace":
        _require_workspace_capability(user, "authorization:delegate")
    else:
        for org_id in row.scope_org_unit_ids or []:
            require_capability(user, "authorization:delegate", int(org_id))
    row.status = False
    await _audit(db, user, "exception.revoke", "authorization_exception", row.id, {"status": True}, {"status": False}, reason)
    await db.commit()


@router.get("/effective-access")
async def effective_access(user_id: str, as_of: datetime | None = None, user: User = Depends(CheckPerm("authorization:view")), db: AsyncSession = Depends(get_async_db)):
    if (await db.execute(select(UserModel.id).where(UserModel.id == user_id, UserModel.workspace_id == user.workspace_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    context = await build_effective_access_context(db, user.workspace_id, user_id, as_of)
    visible = await _visible_org_ids(db, user, "authorization:view")
    if visible is not None:
        assignment_org_ids = {item.org_unit_id for item in context.assignments}
        if not assignment_org_ids or not assignment_org_ids.issubset(set(visible)):
            raise HTTPException(status_code=403, detail="target_user_outside_management_scope")
    return context.to_dict()


@router.get("/audit-events")
async def list_audit_events(limit: int = Query(default=100, ge=1, le=500), user: User = Depends(CheckPerm("authorization:view")), db: AsyncSession = Depends(get_async_db)):
    if await _visible_org_ids(db, user, "authorization:view") is not None:
        # Audit rows currently do not duplicate organization ids; exposing a
        # partial log could leak out-of-scope changes, so scoped operators use
        # effective-access traces instead.
        return []
    result = await db.execute(select(AuthorizationAuditEventModel).where(AuthorizationAuditEventModel.workspace_id == user.workspace_id).order_by(AuthorizationAuditEventModel.created_at.desc()).limit(limit))
    return [{"id": row.id, "actor_id": row.actor_id, "action": row.action, "target_type": row.target_type, "target_id": row.target_id, "before": row.before_json, "after": row.after_json, "reason": row.reason, "created_at": row.created_at} for row in result.scalars().all()]
