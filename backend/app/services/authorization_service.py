"""Effective organization/role authorization resolution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security.capabilities import (
    AUTHORIZATION_V2_MIGRATION_ID,
    CAPABILITIES,
    canonical_capability,
    canonicalize_capabilities,
)
from app.models.auth.authorization import (
    AssignmentModel,
    AuthorizationAuditEventModel,
    AuthorizationExceptionModel,
    AuthorizationRevisionModel,
    PositionModel,
    RoleBindingModel,
)
from app.models.auth.organization import DepartmentModel
from app.models.auth.rbac import RoleModel, UserModel
from app.models.auth.workspace import WorkspaceModel


VALID_SCOPE_TYPES = {"workspace", "org_unit", "org_tree", "custom", "self"}
MAX_EXCEPTION_DAYS = 90


@dataclass(slots=True)
class EffectiveAssignment:
    id: int
    position_id: int
    position_name: str
    org_unit_id: int
    org_unit_name: str
    is_primary: bool
    ends_at: datetime | None


@dataclass(slots=True)
class EffectiveRoleBinding:
    id: int
    position_id: int
    role_id: int
    role_name: str
    scope_type: str
    scope_org_unit_ids: list[int]
    capability_codes: list[str]
    ends_at: datetime | None


@dataclass(slots=True)
class EffectiveAuthorizationException:
    id: int
    effect_type: str
    reason: str
    owner_id: str
    starts_at: datetime
    ends_at: datetime
    capability_codes: list[str]
    scope_org_unit_ids: list[int]


@dataclass(slots=True)
class EffectiveAccessContext:
    workspace_id: str
    user_id: str
    as_of: datetime
    revision: int
    assignments: list[EffectiveAssignment] = field(default_factory=list)
    role_bindings: list[EffectiveRoleBinding] = field(default_factory=list)
    capability_scopes: dict[str, list[int] | str] = field(default_factory=dict)
    denied_capability_scopes: dict[str, list[int] | str] = field(default_factory=dict)
    exception_ids: list[int] = field(default_factory=list)
    exceptions: list[EffectiveAuthorizationException] = field(default_factory=list)
    legacy_fallback: bool = False
    is_workspace_admin: bool = False

    @property
    def capabilities(self) -> list[str]:
        return sorted(self.capability_scopes)

    @property
    def valid_until(self) -> datetime | None:
        candidates = [
            value
            for value in (
                *(item.ends_at for item in self.assignments),
                *(item.ends_at for item in self.role_bindings),
                *(item.ends_at for item in self.exceptions),
            )
            if value is not None
        ]
        return min(candidates) if candidates else None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = self.capabilities
        payload["as_of"] = self.as_of.isoformat()
        payload["valid_until"] = self.valid_until.isoformat() if self.valid_until else None
        return payload


def _effective_clause(model: Any, as_of: datetime):
    return and_(
        model.status == True,  # noqa: E712
        model.starts_at <= as_of,
        or_(model.ends_at.is_(None), model.ends_at > as_of),
    )


async def _workspace_org_ids(db: AsyncSession, workspace_id: str) -> list[int]:
    result = await db.execute(
        select(DepartmentModel.id).where(
            DepartmentModel.workspace_id == workspace_id,
            DepartmentModel.status == True,  # noqa: E712
        )
    )
    return [int(item) for item in result.scalars().all()]


async def resolve_scope_org_ids(
    db: AsyncSession,
    workspace_id: str,
    scope_type: str,
    anchor_org_unit_id: int | None,
    custom_org_unit_ids: Iterable[int] | None = None,
) -> list[int]:
    if scope_type not in VALID_SCOPE_TYPES:
        raise ValueError(f"invalid scope type: {scope_type}")
    if scope_type == "workspace":
        return await _workspace_org_ids(db, workspace_id)
    if scope_type in {"self", "org_unit"}:
        return [anchor_org_unit_id] if anchor_org_unit_id is not None else []
    if scope_type == "org_tree":
        if anchor_org_unit_id is None:
            return []
        result = await db.execute(
            select(DepartmentModel.id).where(
                DepartmentModel.workspace_id == workspace_id,
                DepartmentModel.status == True,  # noqa: E712
                or_(
                    DepartmentModel.id == anchor_org_unit_id,
                    DepartmentModel.ancestors.like(f"%/{anchor_org_unit_id}/%"),
                ),
            )
        )
        return [int(item) for item in result.scalars().all()]

    requested = sorted({int(item) for item in custom_org_unit_ids or []})
    if not requested:
        return []
    result = await db.execute(
        select(DepartmentModel.id).where(
            DepartmentModel.workspace_id == workspace_id,
            DepartmentModel.id.in_(requested),
        )
    )
    valid = sorted(int(item) for item in result.scalars().all())
    if valid != requested:
        raise ValueError("custom organization scope crosses workspace or contains missing units")
    return valid


def _merge_scope(target: dict[str, set[int] | str], code: str, scope: list[int] | str) -> None:
    code = canonical_capability(code)
    if scope == "*" or target.get(code) == "*":
        target[code] = "*"
        return
    current = target.setdefault(code, set())
    assert isinstance(current, set)
    current.update(scope)


def _grant_workspace_admin_access(context: EffectiveAccessContext) -> EffectiveAccessContext:
    """Grant the workspace owner every registered tenant capability.

    This bypass is intentionally scoped to a context that has already been
    resolved for one workspace; it never grants platform or cross-workspace
    access.
    """
    context.is_workspace_admin = True
    context.capability_scopes = {code: "*" for code in sorted(CAPABILITIES)}
    context.denied_capability_scopes = {}
    return context


async def build_effective_access_context(
    db: AsyncSession,
    workspace_id: str,
    user_id: str,
    as_of: datetime | None = None,
    *,
    allow_legacy_fallback: bool = True,
) -> EffectiveAccessContext:
    as_of = as_of or datetime.utcnow()
    if as_of.tzinfo is not None:
        as_of = as_of.astimezone(timezone.utc).replace(tzinfo=None)
    if allow_legacy_fallback:
        try:
            migration_status = (await db.execute(
                text(
                    "SELECT status FROM sys_schema_migrations "
                    "WHERE migration_id = :migration_id LIMIT 1"
                ),
                {"migration_id": AUTHORIZATION_V2_MIGRATION_ID},
            )).scalar_one_or_none()
            if migration_status == "complete":
                allow_legacy_fallback = False
        except Exception:
            # Before the additive migration exists, the old role relation is the
            # only available source. Deployment performs the V2 migration first.
            pass
    revision_result = await db.execute(
        select(AuthorizationRevisionModel.revision).where(
            AuthorizationRevisionModel.workspace_id == workspace_id
        )
    )
    revision = int(revision_result.scalar_one_or_none() or 1)
    context = EffectiveAccessContext(workspace_id, user_id, as_of, revision)
    workspace_owner_id = (await db.execute(
        select(WorkspaceModel.owner_id).where(WorkspaceModel.id == workspace_id)
    )).scalar_one_or_none()
    context.is_workspace_admin = bool(
        workspace_owner_id and str(workspace_owner_id) == str(user_id)
    )

    assignment_result = await db.execute(
        select(AssignmentModel, PositionModel, DepartmentModel)
        .join(PositionModel, PositionModel.id == AssignmentModel.position_id)
        .join(DepartmentModel, DepartmentModel.id == PositionModel.org_unit_id)
        .where(
            AssignmentModel.workspace_id == workspace_id,
            AssignmentModel.user_id == user_id,
            PositionModel.workspace_id == workspace_id,
            PositionModel.status == True,  # noqa: E712
            DepartmentModel.workspace_id == workspace_id,
            DepartmentModel.status == True,  # noqa: E712
            _effective_clause(AssignmentModel, as_of),
        )
    )
    assignment_rows = assignment_result.all()
    position_org: dict[int, int] = {}
    for assignment, position, org in assignment_rows:
        context.assignments.append(
            EffectiveAssignment(
                id=assignment.id,
                position_id=position.id,
                position_name=position.name,
                org_unit_id=org.id,
                org_unit_name=org.name,
                is_primary=bool(assignment.is_primary),
                ends_at=assignment.ends_at,
            )
        )
        position_org[position.id] = org.id

    allow_scopes: dict[str, set[int] | str] = {}
    deny_scopes: dict[str, set[int] | str] = {}
    if position_org:
        binding_result = await db.execute(
            select(RoleBindingModel, RoleModel)
            .join(RoleModel, RoleModel.id == RoleBindingModel.role_id)
            .options(selectinload(RoleModel.permissions))
            .where(
                RoleBindingModel.workspace_id == workspace_id,
                RoleBindingModel.position_id.in_(position_org),
                or_(RoleModel.workspace_id == workspace_id, RoleModel.workspace_id.is_(None)),
                _effective_clause(RoleBindingModel, as_of),
            )
        )
        for binding, role in binding_result.unique().all():
            anchor = binding.scope_org_unit_id or position_org.get(binding.position_id)
            org_ids = await resolve_scope_org_ids(
                db,
                workspace_id,
                binding.scope_type,
                anchor,
                binding.custom_org_unit_ids,
            )
            codes = sorted(canonicalize_capabilities(p.code for p in role.permissions))
            scope_value: list[int] | str = "*" if binding.scope_type == "workspace" else org_ids
            for code in codes:
                _merge_scope(allow_scopes, code, scope_value)
            context.role_bindings.append(
                EffectiveRoleBinding(
                    id=binding.id,
                    position_id=binding.position_id,
                    role_id=role.id,
                    role_name=role.name,
                    scope_type=binding.scope_type,
                    scope_org_unit_ids=org_ids,
                    capability_codes=codes,
                    ends_at=binding.ends_at,
                )
            )

    exception_result = await db.execute(
        select(AuthorizationExceptionModel).where(
            AuthorizationExceptionModel.workspace_id == workspace_id,
            AuthorizationExceptionModel.user_id == user_id,
            _effective_clause(AuthorizationExceptionModel, as_of),
        )
    )
    for exception in exception_result.scalars().all():
        context.exception_ids.append(exception.id)
        scope: list[int] | str = (
            "*" if exception.scope_type == "workspace" else list(exception.scope_org_unit_ids or [])
        )
        destination = deny_scopes if exception.effect_type == "deny" else allow_scopes
        for code in canonicalize_capabilities(exception.capability_codes or []):
            _merge_scope(destination, code, scope)
        context.exceptions.append(EffectiveAuthorizationException(
            id=exception.id,
            effect_type=exception.effect_type,
            reason=exception.reason,
            owner_id=exception.owner_id,
            starts_at=exception.starts_at,
            ends_at=exception.ends_at,
            capability_codes=sorted(canonicalize_capabilities(exception.capability_codes or [])),
            scope_org_unit_ids=list(exception.scope_org_unit_ids or []),
        ))

    if not context.assignments and allow_legacy_fallback:
        user_result = await db.execute(
            select(UserModel)
            .options(selectinload(UserModel.roles).selectinload(RoleModel.permissions))
            .where(UserModel.id == user_id, UserModel.workspace_id == workspace_id)
        )
        user = user_result.unique().scalar_one_or_none()
        if user:
            legacy_codes = canonicalize_capabilities(
                permission.code for role in user.roles for permission in role.permissions
            )
            for code in legacy_codes:
                _merge_scope(allow_scopes, code, "*")
            context.legacy_fallback = True

    def serialize(source: dict[str, set[int] | str]) -> dict[str, list[int] | str]:
        return {
            code: value if value == "*" else sorted(value)
            for code, value in source.items()
        }

    context.capability_scopes = serialize(allow_scopes)
    context.denied_capability_scopes = serialize(deny_scopes)
    for code, denied in list(context.denied_capability_scopes.items()):
        if denied == "*":
            context.capability_scopes.pop(code, None)
    if context.is_workspace_admin:
        return _grant_workspace_admin_access(context)
    return context


def has_capability(
    context: EffectiveAccessContext,
    capability: str,
    target_org_unit_id: int | None = None,
) -> bool:
    code = canonical_capability(capability)
    allowed = context.capability_scopes.get(code)
    if allowed is None:
        return False
    denied = context.denied_capability_scopes.get(code)
    if denied == "*":
        return False
    if target_org_unit_id is None:
        return True
    if denied and target_org_unit_id in denied:
        return False
    return allowed == "*" or target_org_unit_id in allowed


def validate_exception_window(starts_at: datetime, ends_at: datetime, max_days: int = MAX_EXCEPTION_DAYS) -> None:
    if ends_at <= starts_at:
        raise ValueError("exception end time must be after start time")
    if ends_at - starts_at > timedelta(days=max_days):
        raise ValueError(f"exception duration cannot exceed {max_days} days")


async def bump_authorization_revision(db: AsyncSession, workspace_id: str) -> int:
    result = await db.execute(
        select(AuthorizationRevisionModel).where(
            AuthorizationRevisionModel.workspace_id == workspace_id
        ).with_for_update()
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = AuthorizationRevisionModel(workspace_id=workspace_id, revision=1)
        db.add(row)
    else:
        row.revision += 1
    await db.flush()
    return int(row.revision)


async def record_authorization_audit(
    db: AsyncSession,
    *,
    workspace_id: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str | int | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> AuthorizationAuditEventModel:
    event = AuthorizationAuditEventModel(
        workspace_id=workspace_id,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        before_json=before or {},
        after_json=after or {},
        reason=reason,
    )
    db.add(event)
    await db.flush()
    return event
