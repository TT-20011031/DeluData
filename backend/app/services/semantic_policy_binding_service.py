"""Versioned semantic policies bound to ERP authorization targets."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import distinct, func, or_, select, text

from app.core.db.database import get_async_db_manager
from app.core.security.capabilities import AUTHORIZATION_V2_MIGRATION_ID
from app.models.auth.authorization import AssignmentModel, AuthorizationExceptionModel, PositionModel, RoleBindingModel
from app.models.auth.rbac import RoleModel, UserModel
from app.models.auth.organization import DepartmentModel
from app.models.config.semantic import (
    SemanticColumnModel,
    SemanticDatasourceModel,
    SemanticMetricModel,
    SemanticOwnershipMappingModel,
    SemanticPolicyBindingModel,
    SemanticPolicyVersionEffectModel,
    SemanticPolicyVersionModel,
    SemanticTableModel,
)
from app.services.authorization_service import (
    build_effective_access_context,
    bump_authorization_revision,
    record_authorization_audit,
    resolve_scope_org_ids,
)


TARGET_TYPES = {"baseline", "org_unit", "position", "user"}
LEGACY_TARGET_TYPES = {"role_binding", "user_exception"}


class SemanticBindingError(Exception):
    def __init__(self, message: str, *, code: str, status_code: int = 422, details: Any = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details


async def authorization_v2_is_active() -> bool:
    """The cut-over ledger is the switch between legacy and V2 runtime policy."""
    db = get_async_db_manager()
    async with db.session_scope() as session:
        try:
            status = (await session.execute(
                text(
                    "SELECT status FROM sys_schema_migrations "
                    "WHERE migration_id = :migration_id LIMIT 1"
                ),
                {"migration_id": AUTHORIZATION_V2_MIGRATION_ID},
            )).scalar_one_or_none()
        except Exception:  # The additive ledger does not exist before migration.
            return False
        return status == "complete"


async def _target_exists(session, workspace_id: str, target_type: str, target_id: str) -> None:
    if target_type == "baseline":
        if target_id != "*":
            raise SemanticBindingError("全员基线目标无效", code="target_invalid")
        return
    try:
        numeric_id = int(target_id)
    except (TypeError, ValueError) as exc:
        raise SemanticBindingError("授权目标无效", code="target_invalid") from exc
    model = RoleBindingModel if target_type == "role_binding" else AuthorizationExceptionModel
    result = await session.execute(select(model.id).where(model.id == numeric_id, model.workspace_id == workspace_id))
    if result.scalar_one_or_none() is None:
        raise SemanticBindingError("授权目标不存在", code="target_not_found", status_code=404)


async def _target_org_scope(
    session, workspace_id: str, target_type: str, target_id: str,
) -> list[int] | None:
    """Return None for a workspace-wide target, otherwise concrete org ids."""
    if target_type == "baseline":
        return None
    if target_type == "user_exception":
        exception = await session.get(AuthorizationExceptionModel, int(target_id))
        if not exception or exception.workspace_id != workspace_id:
            return []
        if exception.scope_type == "workspace":
            return None
        return list(exception.scope_org_unit_ids or [])
    grant = await session.get(RoleBindingModel, int(target_id))
    if not grant or grant.workspace_id != workspace_id:
        return []
    position = await session.get(PositionModel, grant.position_id)
    if not position or position.workspace_id != workspace_id:
        return []
    if grant.scope_type == "workspace":
        return None
    return await resolve_scope_org_ids(
        session, workspace_id, grant.scope_type,
        grant.scope_org_unit_id or position.org_unit_id, grant.custom_org_unit_ids,
    )


def _context_allows_scope(context, capability: str, org_ids: list[int] | None) -> bool:
    allowed = context.capability_scopes.get(capability)
    denied = context.denied_capability_scopes.get(capability)
    if denied == "*" or allowed is None:
        return False
    if org_ids is None:
        return allowed == "*" and not denied
    denied_ids = set(denied or []) if denied != "*" else set(org_ids)
    if denied_ids.intersection(org_ids):
        return False
    return allowed == "*" or set(org_ids).issubset(set(allowed or []))


async def _assert_actor_scope(
    session, workspace_id: str, actor_id: str, capability: str,
    target_type: str, target_id: str,
) -> None:
    context = await build_effective_access_context(session, workspace_id, actor_id)
    org_ids = await _target_org_scope(session, workspace_id, target_type, target_id)
    if not _context_allows_scope(context, capability, org_ids):
        raise SemanticBindingError(
            "授权目标超出操作者的组织管理范围",
            code="target_scope_denied",
            status_code=403,
        )


async def list_targets(
    workspace_id: str, datasource_id: int, actor_id: str,
) -> list[dict[str, Any]]:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        existing_rows = (await session.execute(select(SemanticPolicyBindingModel).where(
            SemanticPolicyBindingModel.workspace_id == workspace_id,
            SemanticPolicyBindingModel.datasource_id == datasource_id,
        ))).scalars().all()
        existing = {(row.target_type, row.target_id): row for row in existing_rows}
        context = await build_effective_access_context(session, workspace_id, actor_id)
        targets = [{
            "target_type": "baseline", "target_id": "*", "label": "全员基线",
            "binding_id": getattr(existing.get(("baseline", "*")), "id", None),
            "revision": getattr(existing.get(("baseline", "*")), "revision", 0),
            "scope_org_unit_ids": None,
        }] if _context_allows_scope(context, "semantic_access:view", None) else []
        role_rows = (await session.execute(
            select(RoleBindingModel, PositionModel, RoleModel, DepartmentModel)
            .join(PositionModel, PositionModel.id == RoleBindingModel.position_id)
            .join(RoleModel, RoleModel.id == RoleBindingModel.role_id)
            .join(DepartmentModel, DepartmentModel.id == PositionModel.org_unit_id)
            .where(RoleBindingModel.workspace_id == workspace_id, RoleBindingModel.status == True)  # noqa: E712
            .order_by(PositionModel.name, RoleModel.name)
        )).all()
        for grant, position, role, org_unit in role_rows:
            scope_org_unit_ids = await _target_org_scope(session, workspace_id, "role_binding", str(grant.id))
            if not _context_allows_scope(context, "semantic_access:view", scope_org_unit_ids):
                continue
            saved = existing.get(("role_binding", str(grant.id)))
            targets.append({
                "target_type": "role_binding", "target_id": str(grant.id),
                "label": f"{org_unit.name} · {position.name} · {role.name}", "position_id": position.id,
                "role_id": role.id, "org_unit_id": position.org_unit_id,
                "org_unit_name": org_unit.name,
                "scope_org_unit_ids": scope_org_unit_ids,
                "scope_type": grant.scope_type, "starts_at": grant.starts_at, "ends_at": grant.ends_at,
                "binding_id": getattr(saved, "id", None), "revision": getattr(saved, "revision", 0),
            })
        exception_rows = (await session.execute(select(AuthorizationExceptionModel).where(
            AuthorizationExceptionModel.workspace_id == workspace_id,
            AuthorizationExceptionModel.status == True,  # noqa: E712
            AuthorizationExceptionModel.ends_at > datetime.utcnow(),
        ))).scalars().all()
        for exception in exception_rows:
            scope_org_unit_ids = await _target_org_scope(session, workspace_id, "user_exception", str(exception.id))
            if not _context_allows_scope(context, "semantic_access:view", scope_org_unit_ids):
                continue
            saved = existing.get(("user_exception", str(exception.id)))
            targets.append({
                "target_type": "user_exception", "target_id": str(exception.id),
                "label": f"用户例外 · {'拒绝' if exception.effect_type == 'deny' else '允许'} · {exception.user_id}", "user_id": exception.user_id,
                "effect_type": exception.effect_type,
                "reason": exception.reason, "ends_at": exception.ends_at,
                "scope_org_unit_ids": scope_org_unit_ids,
                "binding_id": getattr(saved, "id", None), "revision": getattr(saved, "revision", 0),
            })
        return targets


async def _target_exists_v3(
    session, workspace_id: str, target_type: str, target_id: str,
) -> None:
    """Validate a new organization-oriented semantic target."""
    if target_type == "baseline":
        if target_id != "*":
            raise SemanticBindingError("全员基线目标无效", code="target_invalid")
        return
    if target_type == "user":
        exists = (await session.execute(select(UserModel.id).where(
            UserModel.id == target_id,
            UserModel.workspace_id == workspace_id,
        ))).scalar_one_or_none()
    else:
        try:
            numeric_id = int(target_id)
        except (TypeError, ValueError) as exc:
            raise SemanticBindingError("授权目标无效", code="target_invalid") from exc
        model = {
            "org_unit": DepartmentModel,
            "position": PositionModel,
            "role_binding": RoleBindingModel,
            "user_exception": AuthorizationExceptionModel,
        }.get(target_type)
        if model is None:
            raise SemanticBindingError("授权目标类型无效", code="target_invalid")
        exists = (await session.execute(select(model.id).where(
            model.id == numeric_id,
            model.workspace_id == workspace_id,
        ))).scalar_one_or_none()
    if exists is None:
        raise SemanticBindingError("授权目标不存在", code="target_not_found", status_code=404)


async def _target_org_scope_v3(
    session,
    workspace_id: str,
    target_type: str,
    target_id: str,
    include_descendants: bool = False,
) -> list[int] | None:
    """Resolve the organization boundary of an authorization subject."""
    if target_type == "baseline":
        return None
    if target_type == "org_unit":
        org_unit_id = int(target_id)
        if include_descendants:
            return await resolve_scope_org_ids(
                session, workspace_id, "org_tree", org_unit_id,
            )
        return [org_unit_id]
    if target_type == "position":
        position = await session.get(PositionModel, int(target_id))
        if not position or position.workspace_id != workspace_id:
            return []
        return [int(position.org_unit_id)]
    if target_type == "user":
        now = datetime.utcnow()
        return sorted(set((await session.execute(
            select(PositionModel.org_unit_id)
            .join(AssignmentModel, AssignmentModel.position_id == PositionModel.id)
            .where(
                AssignmentModel.workspace_id == workspace_id,
                AssignmentModel.user_id == target_id,
                AssignmentModel.status == True,  # noqa: E712
                AssignmentModel.starts_at <= now,
                or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
            )
        )).scalars().all()))
    return await _target_org_scope(session, workspace_id, target_type, target_id)


async def _assert_actor_scope_v3(
    session,
    workspace_id: str,
    actor_id: str,
    capability: str,
    target_type: str,
    target_id: str,
    include_descendants: bool = False,
) -> None:
    context = await build_effective_access_context(session, workspace_id, actor_id)
    org_ids = await _target_org_scope_v3(
        session, workspace_id, target_type, target_id, include_descendants,
    )
    if target_type == "user" and org_ids == []:
        org_ids = None
    if not _context_allows_scope(context, capability, org_ids):
        raise SemanticBindingError(
            "授权目标超出操作人的组织管理范围",
            code="target_scope_denied",
            status_code=403,
        )


async def list_organization_targets(
    workspace_id: str,
    datasource_id: int,
    actor_id: str,
    target_type: str = "org_unit",
    org_unit_id: int | None = None,
    search: str = "",
    page: int = 1,
    page_size: int = 100,
    unassigned: bool = False,
) -> dict[str, Any]:
    """List selectable targets without creating policy bindings."""
    if target_type not in TARGET_TYPES:
        raise SemanticBindingError("授权目标类型无效", code="target_invalid")
    db = get_async_db_manager()
    async with db.session_scope() as session:
        datasource = await session.get(SemanticDatasourceModel, datasource_id)
        if not datasource or datasource.workspace_id != workspace_id:
            raise SemanticBindingError("数据源不存在", code="datasource_not_found", status_code=404)
        context = await build_effective_access_context(session, workspace_id, actor_id)
        existing_rows = (await session.execute(select(SemanticPolicyBindingModel).where(
            SemanticPolicyBindingModel.workspace_id == workspace_id,
            SemanticPolicyBindingModel.datasource_id == datasource_id,
            SemanticPolicyBindingModel.target_type.in_(TARGET_TYPES),
        ))).scalars().all()
        existing = {(row.target_type, row.target_id): row for row in existing_rows}
        active_version_ids = [row.active_version_id for row in existing_rows if row.active_version_id]
        active_versions = {
            int(row.id): row for row in (await session.execute(select(
                SemanticPolicyVersionModel,
            ).where(
                SemanticPolicyVersionModel.id.in_(active_version_ids or [-1]),
            ))).scalars()
        }
        denied_bindings: set[int] = set()
        if active_version_ids:
            denied_bindings = set((await session.execute(select(
                SemanticPolicyVersionEffectModel.binding_id,
            ).where(
                SemanticPolicyVersionEffectModel.version_id.in_(active_version_ids),
                SemanticPolicyVersionEffectModel.effect_type == "hidden",
            ))).scalars().all())

        async def payload(kind: str, item_id: str, label: str, **extra: Any) -> dict[str, Any]:
            binding = existing.get((kind, item_id))
            direct_rules = (
                active_versions.get(int(binding.active_version_id)).definition_json or {}
            ).get("tables") or [] if binding and binding.active_version_id in active_versions else []
            probe = binding or SimpleNamespace(
                workspace_id=workspace_id,
                target_type=kind,
                target_id=item_id,
                include_descendants=extra.get("include_descendants", kind == "org_unit"),
            )
            return {
                "target_type": kind,
                "target_id": item_id,
                "label": label,
                **extra,
                "binding_id": getattr(binding, "id", None),
                "revision": getattr(binding, "revision", 0),
                "configured": bool(binding and binding.status and direct_rules),
                "direct_override_count": len(direct_rules),
                "include_descendants": kind == "org_unit",
                "affected_user_count": await _affected_user_count(session, probe),
                "has_explicit_deny": bool(binding and binding.id in denied_bindings),
            }

        items: list[dict[str, Any]] = []
        if target_type == "baseline":
            if _context_allows_scope(context, "semantic_access:view", None):
                items.append(await payload("baseline", "*", "全员基线"))
        elif target_type == "org_unit":
            query = select(DepartmentModel).where(DepartmentModel.workspace_id == workspace_id)
            if org_unit_id is not None:
                query = query.where(DepartmentModel.id == org_unit_id)
            rows = (await session.execute(query.order_by(
                DepartmentModel.order_num, DepartmentModel.id,
            ))).scalars().all()
            for row in rows:
                if not _context_allows_scope(context, "semantic_access:view", [int(row.id)]):
                    continue
                items.append(await payload(
                    "org_unit", str(row.id), row.name,
                    org_unit_id=row.id,
                    parent_id=row.parent_id,
                    include_descendants=True,
                ))
        elif target_type == "position":
            if org_unit_id is None:
                raise SemanticBindingError("岗位目标需要部门", code="org_unit_required")
            if not _context_allows_scope(context, "semantic_access:view", [org_unit_id]):
                raise SemanticBindingError("部门超出管理范围", code="target_scope_denied", status_code=403)
            query = select(PositionModel).where(
                PositionModel.workspace_id == workspace_id,
                PositionModel.org_unit_id == org_unit_id,
            )
            if search.strip():
                query = query.where(PositionModel.name.ilike(f"%{search.strip()}%"))
            rows = (await session.execute(query.order_by(PositionModel.name))).scalars().all()
            for row in rows:
                items.append(await payload(
                    "position", str(row.id), row.name,
                    position_id=row.id,
                    org_unit_id=row.org_unit_id,
                    status=bool(row.status),
                ))
        else:
            now = datetime.utcnow()
            active_conditions = (
                AssignmentModel.status == True,  # noqa: E712
                AssignmentModel.starts_at <= now,
                or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
            )
            if unassigned:
                if not _context_allows_scope(context, "semantic_access:view", None):
                    raise SemanticBindingError(
                        "待分配账号需要工作区级查看权限",
                        code="workspace_scope_required",
                        status_code=403,
                    )
                assigned_ids = select(AssignmentModel.user_id).where(
                    AssignmentModel.workspace_id == workspace_id,
                    *active_conditions,
                )
                query = select(UserModel).where(
                    UserModel.workspace_id == workspace_id,
                    UserModel.id.not_in(assigned_ids),
                )
            else:
                if org_unit_id is None:
                    raise SemanticBindingError("账号目标需要部门", code="org_unit_required")
                if not _context_allows_scope(context, "semantic_access:view", [org_unit_id]):
                    raise SemanticBindingError("部门超出管理范围", code="target_scope_denied", status_code=403)
                query = (
                    select(UserModel)
                    .join(AssignmentModel, AssignmentModel.user_id == UserModel.id)
                    .join(PositionModel, PositionModel.id == AssignmentModel.position_id)
                    .where(
                        UserModel.workspace_id == workspace_id,
                        PositionModel.org_unit_id == org_unit_id,
                        *active_conditions,
                    )
                    .distinct()
                )
            if search.strip():
                value = f"%{search.strip()}%"
                query = query.where(or_(
                    UserModel.username.ilike(value), UserModel.email.ilike(value),
                ))
            rows = (await session.execute(query.order_by(UserModel.username))).scalars().all()
            for row in rows:
                items.append(await payload(
                    "user", str(row.id), row.username,
                    user_id=str(row.id),
                    email=row.email,
                    disabled=bool(row.disabled),
                    org_unit_id=org_unit_id,
                ))

        total = len(items)
        start = max(page - 1, 0) * page_size
        return {
            "items": items[start:start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "can_manage_workspace": _context_allows_scope(
                context, "semantic_access:manage", None,
            ),
        }


# All version/history operations below resolve this name at call time. Point them
# at the organization-aware scope checker while keeping the legacy helper above
# available for reading historical code paths.
_assert_actor_scope = _assert_actor_scope_v3


async def ensure_binding(
    workspace_id: str,
    datasource_id: int,
    target_type: str,
    target_id: str,
    actor_id: str,
    include_descendants: bool = False,
) -> dict[str, Any]:
    if target_type not in TARGET_TYPES:
        raise SemanticBindingError("授权目标类型无效", code="target_invalid")
    db = get_async_db_manager()
    async with db.session_scope() as session:
        datasource = await session.get(SemanticDatasourceModel, datasource_id)
        if not datasource or datasource.workspace_id != workspace_id:
            raise SemanticBindingError("数据源不存在", code="datasource_not_found", status_code=404)
        include_descendants = target_type == "org_unit"
        await _target_exists_v3(session, workspace_id, target_type, target_id)
        await _assert_actor_scope_v3(
            session, workspace_id, actor_id, "semantic_access:manage",
            target_type, target_id, include_descendants,
        )
        result = await session.execute(select(SemanticPolicyBindingModel).where(
            SemanticPolicyBindingModel.workspace_id == workspace_id,
            SemanticPolicyBindingModel.datasource_id == datasource_id,
            SemanticPolicyBindingModel.target_type == target_type,
            SemanticPolicyBindingModel.target_id == target_id,
        ))
        row = result.scalar_one_or_none()
        meta: dict[str, Any] = {}
        if row is None:
            row = SemanticPolicyBindingModel(
                workspace_id=workspace_id, datasource_id=datasource_id,
                target_type=target_type, target_id=target_id,
                include_descendants=include_descendants if target_type == "org_unit" else False,
                created_by=actor_id,
            )
            session.add(row)
            await session.flush()
            audit = await record_authorization_audit(
                session,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="semantic_policy_binding.create",
                target_type="semantic_policy_binding",
                target_id=row.id,
                before=None,
                after=_binding_payload(row),
            )
            meta = {
                "audit_id": audit.id,
                "authorization_revision": await bump_authorization_revision(session, workspace_id),
            }
        return {**_binding_payload(row), **meta}


async def _binding_org_scope(session, binding: SemanticPolicyBindingModel) -> list[int] | None:
    if binding.target_type == "baseline":
        return None
    scope = await _target_org_scope_v3(
        session,
        binding.workspace_id,
        binding.target_type,
        binding.target_id,
        bool(getattr(binding, "include_descendants", False)),
    )
    # Workspace is still an organization authorization boundary at SQL time;
    # materialize every current org so rows cannot escape through an "all" rule.
    if scope is None:
        return await resolve_scope_org_ids(
            session, binding.workspace_id, "workspace", None,
        )
    return scope


async def _prepare_definition(session, binding: SemanticPolicyBindingModel, definition: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = {**definition, "subject": {"type": "all", "id": "*", "label": "授权绑定"}}
    normalized["tables"] = [dict(item) for item in definition.get("tables") or []]
    blockers: list[dict[str, Any]] = []
    org_ids = await _binding_org_scope(session, binding)
    target_exception = None
    if binding.target_type == "user_exception":
        target_exception = await session.get(AuthorizationExceptionModel, int(binding.target_id))
    table_ids = [int(item.get("table_id") or 0) for item in normalized["tables"] if isinstance(item, dict)]
    mappings = (await session.execute(select(SemanticOwnershipMappingModel).where(
        SemanticOwnershipMappingModel.workspace_id == binding.workspace_id,
        SemanticOwnershipMappingModel.datasource_id == binding.datasource_id,
        SemanticOwnershipMappingModel.table_id.in_(table_ids or [-1]),
    ))).scalars().all()
    mapping_by_table = {row.table_id: row for row in mappings}
    for rule in normalized["tables"]:
        if rule.get("decision") != "visible":
            continue
        if target_exception and target_exception.effect_type == "deny":
            blockers.append({
                "code": "deny_exception_cannot_grant",
                "message": f"拒绝型用户例外不能授予表 {int(rule.get('table_id') or 0)} 可见权限",
            })
            continue
        raw_scope = rule.get("row_scope") or {"type": "authorization"}
        scope_type = raw_scope.get("type") or "authorization"
        table_id = int(rule.get("table_id") or 0)
        mapping = mapping_by_table.get(table_id)
        policy_condition: dict[str, Any] | None = None
        if scope_type == "custom":
            policy_condition = dict(raw_scope.get("condition") or {})
        elif scope_type == "self":
            if not mapping or not mapping.user_column_id:
                blockers.append({"code": "ownership_mapping_missing", "message": f"表 {table_id} 缺少人员归属字段映射"})
            else:
                value_source = "current_user.username" if mapping.user_value_kind == "username" else "current_user.id"
                policy_condition = {
                    "column_id": mapping.user_column_id,
                    "operator": "=",
                    "value_source": value_source,
                }

        authorization_condition: dict[str, Any] | None = None
        if binding.target_type != "baseline":
            if not mapping or not mapping.org_column_id:
                blockers.append({"code": "ownership_mapping_missing", "message": f"表 {table_id} 缺少组织归属字段映射"})
            else:
                values: list[Any] = list(org_ids or [])
                if mapping.org_value_kind == "code":
                    values = list((await session.execute(select(DepartmentModel.code).where(
                        DepartmentModel.workspace_id == binding.workspace_id,
                        DepartmentModel.id.in_(org_ids or [-1]),
                    ))).scalars().all())
                authorization_condition = {
                    "_authorization_scope": True,
                    "column_id": mapping.org_column_id,
                    "operator": "in",
                    "value": values,
                }

        conditions = [item for item in (authorization_condition, policy_condition) if item]
        if not conditions:
            rule["row_scope"] = {"type": "all"}
        else:
            condition = conditions[0] if len(conditions) == 1 else {"op": "AND", "rules": conditions}
            rule["row_scope"] = {"type": "custom", "condition": condition}
    return normalized, blockers


async def _active_user_org_ids(
    session, workspace_id: str, user_id: str, *, primary_only: bool = False,
) -> list[int]:
    now = datetime.utcnow()
    query = (
        select(PositionModel.org_unit_id)
        .join(AssignmentModel, AssignmentModel.position_id == PositionModel.id)
        .where(
            AssignmentModel.workspace_id == workspace_id,
            AssignmentModel.user_id == user_id,
            AssignmentModel.status == True,  # noqa: E712
            AssignmentModel.starts_at <= now,
            or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
        )
    )
    if primary_only:
        query = query.where(AssignmentModel.is_primary == True)  # noqa: E712
    values = list((await session.execute(query)).scalars().all())
    if primary_only and not values:
        return await _active_user_org_ids(session, workspace_id, user_id)
    return sorted(set(int(item) for item in values))


async def _target_root_org_ids(session, binding, scope_type: str) -> list[int]:
    if binding.target_type == "org_unit":
        roots = [int(binding.target_id)]
    elif binding.target_type == "position":
        position = await session.get(PositionModel, int(binding.target_id))
        roots = [int(position.org_unit_id)] if position else []
    elif binding.target_type == "user":
        if scope_type == "all_assignments":
            return await _active_user_org_ids(
                session, binding.workspace_id, binding.target_id,
            )
        roots = await _active_user_org_ids(
            session, binding.workspace_id, binding.target_id, primary_only=True,
        )
    else:
        roots = []
    if scope_type != "target_org_tree":
        return roots
    expanded: set[int] = set()
    for root in roots:
        expanded.update(await resolve_scope_org_ids(
            session, binding.workspace_id, "org_tree", root,
        ))
    return sorted(expanded)


async def _custom_org_scope_ids(
    session, workspace_id: str, raw_scope: dict[str, Any],
) -> list[int]:
    requested = sorted(set(int(item) for item in raw_scope.get("org_unit_ids") or []))
    valid = set((await session.execute(select(DepartmentModel.id).where(
        DepartmentModel.workspace_id == workspace_id,
        DepartmentModel.id.in_(requested or [-1]),
    ))).scalars().all())
    if valid != set(requested):
        raise SemanticBindingError("自定义部门包含无效目标", code="organization_scope_invalid")
    if not raw_scope.get("include_descendants"):
        return requested
    expanded: set[int] = set()
    for root in requested:
        expanded.update(await resolve_scope_org_ids(
            session, workspace_id, "org_tree", root,
        ))
    return sorted(expanded)


def _mapping_value_payload(mapping: Any) -> dict[str, Any]:
    value = getattr(mapping, "org_value_mapping_json", None)
    if value is None:
        value = getattr(mapping, "org_value_mapping", None)
    return dict(value or {})


def _typed_source_key(source_type: str, source_value: Any) -> tuple[str, str]:
    return source_type, str(source_value)


def _is_string_data_type(data_type: Any) -> bool:
    value = str(data_type or "").lower()
    return any(item in value for item in ("char", "text", "string", "enum", "set"))


def _external_values_for_orgs(mapping: Any, org_ids: list[int]) -> list[Any]:
    allowed = {int(value) for value in org_ids}
    values: list[Any] = []
    for binding in _mapping_value_payload(mapping).get("bindings") or []:
        if (
            isinstance(binding, dict)
            and binding.get("target_kind") == "org_unit"
            and int(binding.get("org_unit_id") or 0) in allowed
        ):
            values.append(binding.get("source_value"))
    return values


def _explicit_unowned_values(mapping: Any) -> list[Any]:
    return [
        binding.get("source_value")
        for binding in (_mapping_value_payload(mapping).get("bindings") or [])
        if isinstance(binding, dict) and binding.get("target_kind") == "unowned"
    ]


async def _organization_values(
    session,
    workspace_id: str,
    mapping: Any,
    org_ids: list[int],
) -> list[Any]:
    if mapping.org_value_kind == "code":
        return list((await session.execute(select(DepartmentModel.code).where(
            DepartmentModel.workspace_id == workspace_id,
            DepartmentModel.id.in_(org_ids or [-1]),
        ))).scalars().all())
    if mapping.org_value_kind == "external":
        return _external_values_for_orgs(mapping, org_ids)
    return list(org_ids)


async def _build_organization_condition(
    session,
    workspace_id: str,
    mapping: Any,
    org_ids: list[int],
    marker: dict[str, Any],
) -> dict[str, Any]:
    column_id = int(mapping.org_column_id)
    values = await _organization_values(
        session, workspace_id, mapping, list(org_ids or []),
    )
    if marker.get("unowned_access") != "table_grantees":
        return {
            "_organization_scope": marker,
            "column_id": column_id,
            "operator": "in",
            "value": values,
        }

    column = await session.get(SemanticColumnModel, column_id)
    runtime_marker = {**marker, "column_id": column_id}
    rules: list[dict[str, Any]] = []
    if values:
        rules.append({
            "column_id": column_id,
            "operator": "in",
            "value": values,
        })
    rules.append({"column_id": column_id, "operator": "is null"})
    if column and _is_string_data_type(column.data_type):
        rules.append({"column_id": column_id, "operator": "=", "value": ""})
    unowned_values = _explicit_unowned_values(mapping)
    if unowned_values:
        rules.append({
            "column_id": column_id,
            "operator": "in",
            "value": unowned_values,
        })
    return {
        "_organization_scope": runtime_marker,
        "op": "OR",
        "rules": rules,
    }


async def _prepare_definition_v3(
    session,
    binding,
    definition: dict[str, Any],
    actor_id: str | None = None,
    mapping_overrides: dict[int, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compile business row scopes without coupling them to the subject scope."""
    normalized = {
        **definition,
        "subject": {
            # Organization-oriented bindings decide who receives the policy.
            # The compiled effects are therefore universal inside that binding;
            # feeding baseline/org_unit/position into the legacy effect compiler
            # would create an invalid legacy subject.
            "type": "all",
            "id": "*",
            "label": definition.get("name") or "问数数据权限",
        },
    }
    normalized["tables"] = [dict(item) for item in definition.get("tables") or []]
    blockers: list[dict[str, Any]] = []
    table_ids = [
        int(item.get("table_id") or 0)
        for item in normalized["tables"]
        if isinstance(item, dict)
    ]
    mappings = (await session.execute(select(SemanticOwnershipMappingModel).where(
        SemanticOwnershipMappingModel.workspace_id == binding.workspace_id,
        SemanticOwnershipMappingModel.datasource_id == binding.datasource_id,
        SemanticOwnershipMappingModel.table_id.in_(table_ids or [-1]),
    ))).scalars().all()
    mapping_by_table = {row.table_id: row for row in mappings}
    for table_id, payload in (mapping_overrides or {}).items():
        mapping_by_table[int(table_id)] = SimpleNamespace(
            table_id=int(table_id),
            org_column_id=payload.get("org_column_id"),
            org_value_kind=payload.get("org_value_kind") or "id",
            org_value_mapping_json=payload.get("org_value_mapping") or {},
            user_column_id=payload.get("user_column_id"),
            user_value_kind=payload.get("user_value_kind") or "id",
        )
    actor_context = None
    if actor_id:
        actor_context = await build_effective_access_context(
            session, binding.workspace_id, actor_id,
        )

    for rule in normalized["tables"]:
        if rule.get("decision") != "visible":
            continue
        table_id = int(rule.get("table_id") or 0)
        mapping = mapping_by_table.get(table_id)
        raw_scope = dict(rule.get("row_scope") or {})
        scope_type = raw_scope.get("type") or (
            "all" if binding.target_type == "baseline" else "target_org"
        )
        if scope_type == "authorization":
            scope_type = "all" if binding.target_type == "baseline" else "target_org"
        if scope_type == "department":
            scope_type = "target_org_tree" if raw_scope.get("include_descendants", True) else "target_org"
        unowned_access = raw_scope.get("unowned_access")
        if unowned_access not in {None, "table_grantees"}:
            blockers.append({
                "code": "unowned_access_invalid",
                "message": f"表 {table_id} 的无归属行访问策略无效",
            })
            continue

        policy_condition: dict[str, Any] | None = None
        org_scope_ids: list[int] | None = []
        marker: dict[str, Any] | None = None
        if scope_type == "all":
            org_scope_ids = None
        elif scope_type == "self":
            if not mapping or not mapping.user_column_id:
                blockers.append({
                    "code": "ownership_mapping_missing",
                    "message": f"表 {table_id} 缺少人员归属字段映射",
                })
            else:
                policy_condition = {
                    "column_id": mapping.user_column_id,
                    "operator": "=",
                    "value_source": (
                        "current_user.username"
                        if mapping.user_value_kind == "username"
                        else "current_user.id"
                    ),
                }
        elif scope_type in {"target_org", "target_org_tree", "primary_assignment", "all_assignments"}:
            normalized_scope = "target_org" if scope_type == "primary_assignment" else scope_type
            if binding.target_type == "baseline":
                blockers.append({
                    "code": "row_scope_not_supported",
                    "message": f"全员基线不能使用 {scope_type} 数据范围",
                })
                continue
            org_scope_ids = await _target_root_org_ids(session, binding, scope_type)
            marker = {"type": normalized_scope}
            if unowned_access:
                marker["unowned_access"] = unowned_access
        elif scope_type == "custom_org":
            org_scope_ids = await _custom_org_scope_ids(
                session, binding.workspace_id, raw_scope,
            )
            marker = {
                "type": "custom_org",
                "org_unit_ids": list(raw_scope.get("org_unit_ids") or []),
                "include_descendants": bool(raw_scope.get("include_descendants")),
            }
        elif scope_type == "custom":
            org_scope_ids = None
            policy_condition = dict(raw_scope.get("condition") or {})
        else:
            blockers.append({
                "code": "row_scope_not_supported",
                "message": f"表 {table_id} 使用了不支持的数据范围 {scope_type}",
            })
            continue

        if actor_context is not None:
            needs_workspace = scope_type in {"all", "custom"}
            allowed_scope = None if needs_workspace else org_scope_ids
            if not _context_allows_scope(
                actor_context, "semantic_access:manage", allowed_scope,
            ):
                blockers.append({
                    "code": "data_scope_denied",
                    "message": f"表 {table_id} 的数据范围超出操作人的管理范围",
                })

        organization_condition: dict[str, Any] | None = None
        if marker is not None:
            if not mapping or not mapping.org_column_id:
                blockers.append({
                    "code": "ownership_mapping_missing",
                    "message": f"表 {table_id} 缺少组织归属字段映射",
                })
            else:
                organization_condition = await _build_organization_condition(
                    session,
                    binding.workspace_id,
                    mapping,
                    list(org_scope_ids or []),
                    marker,
                )

        conditions = [item for item in (organization_condition, policy_condition) if item]
        if not conditions:
            rule["row_scope"] = {"type": "all"}
        else:
            condition = conditions[0] if len(conditions) == 1 else {
                "op": "AND", "rules": conditions,
            }
            rule["row_scope"] = {"type": "custom", "condition": condition}
    return normalized, blockers


async def persist_target_policy_version_in_session(
    session,
    binding: SemanticPolicyBindingModel,
    definition: dict[str, Any],
    actor_id: str,
    *,
    source_text: str | None = None,
    source_type: str = "manual",
    source_ref: str | None = None,
    confirm_warnings: bool = False,
    mapping_overrides: dict[int, dict[str, Any]] | None = None,
) -> tuple[SemanticPolicyVersionModel, dict[str, Any], list[dict[str, Any]]]:
    """Compile and persist one immutable version inside the caller's transaction."""
    from app.services.semantic_access_policy_service import get_semantic_access_policy_service

    datasource = await session.get(SemanticDatasourceModel, binding.datasource_id)
    prepared, ownership_blockers = await _prepare_definition_v3(
        session,
        binding,
        definition,
        actor_id,
        mapping_overrides=mapping_overrides,
    )
    compilation = await get_semantic_access_policy_service()._compile_definition(
        session,
        binding.workspace_id,
        binding.datasource_id,
        prepared,
        schema_fingerprint=getattr(datasource, "schema_fingerprint", None),
        allow_empty_tables=True,
    )
    blockers = ownership_blockers + list(compilation["validation"].get("blockers") or [])
    warnings = list(compilation["validation"].get("warnings") or [])
    if blockers:
        raise SemanticBindingError(
            "权限配置存在阻断项",
            code="validation_blocked",
            details={"blockers": blockers, "warnings": warnings},
        )
    if warnings and not confirm_warnings:
        raise SemanticBindingError(
            "权限配置存在待确认提醒",
            code="warnings_confirmation_required",
            status_code=409,
            details={"warnings": warnings},
        )

    version_number = int(binding.revision or 0) + 1
    version = SemanticPolicyVersionModel(
        workspace_id=binding.workspace_id,
        datasource_id=binding.datasource_id,
        binding_id=binding.id,
        version=version_number,
        definition_json=definition,
        source_text=source_text,
        source_type=source_type,
        source_ref=source_ref,
        validation_json={"blockers": [], "warnings": warnings},
        compile_summary_json=compilation["summary"],
        schema_fingerprint=getattr(datasource, "schema_fingerprint", None),
        created_by=actor_id,
    )
    session.add(version)
    await session.flush()
    for effect in compilation["effects"]:
        session.add(SemanticPolicyVersionEffectModel(
            workspace_id=binding.workspace_id,
            datasource_id=binding.datasource_id,
            binding_id=binding.id,
            version_id=version.id,
            effect_key=effect["effect_key"],
            asset_type=effect["asset_type"],
            asset_id=effect["asset_id"],
            effect_type=effect["effect_type"],
            condition_json=effect.get("condition_json") or {},
            compiled_sql=effect.get("compiled_sql"),
            priority=effect.get("priority") or 100,
        ))
    binding.active_version_id = version.id
    binding.revision = version_number
    binding.updated_at = datetime.now()
    await session.flush()
    return version, compilation, warnings


async def upsert_ownership_mapping_in_session(
    session,
    *,
    workspace_id: str,
    datasource_id: int,
    table_id: int,
    payload: dict[str, Any],
    actor_id: str,
) -> tuple[SemanticOwnershipMappingModel, dict[str, Any] | None, dict[str, Any]]:
    """Validate and upsert an ownership mapping inside the caller's transaction."""
    table = await session.get(SemanticTableModel, table_id)
    if not table or table.workspace_id != workspace_id or table.datasource_id != datasource_id:
        raise SemanticBindingError("语义表不存在", code="table_not_found", status_code=404)
    column_ids = [payload.get("org_column_id"), payload.get("user_column_id")]
    column_ids = [int(item) for item in column_ids if item is not None]
    if column_ids:
        valid_ids = set((await session.execute(select(SemanticColumnModel.id).where(
            SemanticColumnModel.workspace_id == workspace_id,
            SemanticColumnModel.datasource_id == datasource_id,
            SemanticColumnModel.table_id == table_id,
            SemanticColumnModel.id.in_(column_ids),
        ))).scalars().all())
        if valid_ids != set(column_ids):
            raise SemanticBindingError("归属字段不属于当前语义表", code="ownership_column_invalid")
    org_value_kind = payload.get("org_value_kind") or "id"
    if org_value_kind not in {"id", "code", "external"}:
        raise SemanticBindingError("组织归属值类型无效", code="ownership_value_kind_invalid")
    org_value_mapping = dict(payload.get("org_value_mapping") or {})
    bindings = org_value_mapping.get("bindings") or []
    if not isinstance(bindings, list):
        raise SemanticBindingError("外部组织值映射格式无效", code="ownership_value_mapping_invalid")
    seen_source_values: set[tuple[str, str]] = set()
    requested_org_ids: set[int] = set()
    normalized_bindings: list[dict[str, Any]] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            raise SemanticBindingError("外部组织值映射格式无效", code="ownership_value_mapping_invalid")
        source_type = str(binding.get("source_type") or "")
        source_value = binding.get("source_value")
        if (
            source_type not in {"string", "integer"}
            or isinstance(source_value, (dict, list, bool))
            or source_type == "string" and not isinstance(source_value, str)
            or source_type == "integer" and not isinstance(source_value, int)
        ):
            raise SemanticBindingError("外部组织源值格式无效", code="ownership_source_value_invalid")
        key = _typed_source_key(source_type, source_value)
        if key in seen_source_values:
            raise SemanticBindingError("外部组织源值重复", code="ownership_source_value_duplicated")
        seen_source_values.add(key)
        target_kind = binding.get("target_kind")
        normalized = dict(binding)
        if target_kind == "org_unit":
            org_unit_id = int(binding.get("org_unit_id") or 0)
            if not org_unit_id:
                raise SemanticBindingError("外部组织值缺少目标部门", code="ownership_target_required")
            requested_org_ids.add(org_unit_id)
            normalized["org_unit_id"] = org_unit_id
        elif target_kind == "unowned":
            if not binding.get("manual_unowned") or not str(binding.get("reason") or "").strip():
                raise SemanticBindingError(
                    "非空值标记为无归属需要管理员明确确认并填写原因",
                    code="ownership_unowned_confirmation_required",
                )
            normalized.pop("org_unit_id", None)
        else:
            raise SemanticBindingError("外部组织值目标无效", code="ownership_target_invalid")
        normalized_bindings.append(normalized)
    if requested_org_ids:
        valid_org_ids = set((await session.execute(select(DepartmentModel.id).where(
            DepartmentModel.workspace_id == workspace_id,
            DepartmentModel.status == True,  # noqa: E712
            DepartmentModel.id.in_(requested_org_ids),
        ))).scalars().all())
        if valid_org_ids != requested_org_ids:
            raise SemanticBindingError("外部组织值包含无效目标部门", code="ownership_target_invalid")
    if org_value_mapping:
        org_value_mapping = {
            **org_value_mapping,
            "version": 1,
            "bindings": normalized_bindings,
        }
    if org_value_kind == "external" and not normalized_bindings:
        raise SemanticBindingError(
            "外部组织值映射不能为空", code="ownership_value_mapping_required",
        )

    row = (await session.execute(select(SemanticOwnershipMappingModel).where(
        SemanticOwnershipMappingModel.workspace_id == workspace_id,
        SemanticOwnershipMappingModel.datasource_id == datasource_id,
        SemanticOwnershipMappingModel.table_id == table_id,
    ).with_for_update())).scalar_one_or_none()
    before = None
    if row is None:
        row = SemanticOwnershipMappingModel(
            workspace_id=workspace_id,
            datasource_id=datasource_id,
            table_id=table_id,
        )
        session.add(row)
    else:
        before = {
            "org_column_id": row.org_column_id,
            "org_value_kind": row.org_value_kind,
            "org_value_mapping": row.org_value_mapping_json or {},
            "user_column_id": row.user_column_id,
            "user_value_kind": row.user_value_kind,
        }
    for key in ("org_column_id", "org_value_kind", "user_column_id", "user_value_kind"):
        if key in payload:
            setattr(row, key, payload[key])
    if "org_value_mapping" in payload:
        row.org_value_mapping_json = org_value_mapping
    row.updated_by = actor_id
    await session.flush()
    after = {
        "table_id": table_id,
        "org_column_id": row.org_column_id,
        "org_value_kind": row.org_value_kind,
        "org_value_mapping": row.org_value_mapping_json or {},
        "user_column_id": row.user_column_id,
        "user_value_kind": row.user_value_kind,
    }
    return row, before, after


async def save_and_activate(
    workspace_id: str, binding_id: int, expected_revision: int,
    definition: dict[str, Any], actor_id: str, source_text: str | None = None,
    confirm_warnings: bool = False,
    audit_action: str = "semantic_policy.activate",
    source_type: str = "manual",
    source_ref: str | None = None,
) -> dict[str, Any]:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        result = await session.execute(select(SemanticPolicyBindingModel).where(
            SemanticPolicyBindingModel.id == binding_id,
            SemanticPolicyBindingModel.workspace_id == workspace_id,
        ).with_for_update())
        binding = result.scalar_one_or_none()
        if not binding:
            raise SemanticBindingError("策略绑定不存在", code="binding_not_found", status_code=404)
        datasource = await session.get(SemanticDatasourceModel, binding.datasource_id)
        if (
            datasource
            and bool(getattr(datasource, "access_bootstrap_required", False))
            and source_type == "manual"
        ):
            raise SemanticBindingError(
                "首次权限尚未由已确认访问依据生成，不能绕过依据链直接发布",
                code="access_evidence_required",
                status_code=409,
            )
        await _assert_actor_scope(
            session, workspace_id, actor_id, "semantic_access:manage",
            binding.target_type, binding.target_id,
            bool(getattr(binding, "include_descendants", False)),
        )
        if binding.revision != expected_revision:
            raise SemanticBindingError("配置已被其他人修改，请刷新后重试", code="revision_conflict", status_code=409, details={"current_revision": binding.revision})
        previous_definition = None
        if binding.active_version_id:
            active_version = await session.get(
                SemanticPolicyVersionModel, binding.active_version_id,
            )
            previous_definition = active_version.definition_json if active_version else None
        if source_type == "manual" and _definition_expands_access(
            previous_definition, definition,
        ):
            if binding.target_type in {"baseline", "org_unit"}:
                raise SemanticBindingError(
                    "扩大部门或全员访问必须先修改并确认访问依据",
                    code="access_evidence_change_required",
                    status_code=409,
                )
            if not (source_text or "").strip():
                raise SemanticBindingError(
                    "扩大岗位或账号访问必须填写变更理由",
                    code="change_reason_required",
                    status_code=422,
                )
        previous = binding.active_version_id
        version, _compilation, warnings = await persist_target_policy_version_in_session(
            session,
            binding,
            definition,
            actor_id,
            source_text=source_text,
            source_type=source_type,
            source_ref=source_ref,
            confirm_warnings=confirm_warnings,
        )
        audit = await record_authorization_audit(
            session, workspace_id=workspace_id, actor_id=actor_id,
            action=audit_action, target_type="semantic_policy_binding",
            target_id=binding.id, before={"active_version_id": previous, "revision": expected_revision},
            after={"active_version_id": version.id, "revision": binding.revision},
        )
        await bump_authorization_revision(session, workspace_id)
        affected_user_count = await _affected_user_count(session, binding)
        await session.flush()
        return {
            **_binding_payload(binding), "active_version": _version_payload(version),
            "validation": {"blockers": [], "warnings": warnings}, "audit_id": audit.id,
            "affected_user_count": affected_user_count,
        }


async def suggest_binding_patch(
    workspace_id: str,
    binding_id: int,
    source_text: str,
    actor_id: str,
) -> dict[str, Any]:
    """Generate and validate a form patch without mutating the binding."""
    from app.services.semantic_access_policy_service import get_semantic_access_policy_service

    access_service = get_semantic_access_policy_service()
    db = get_async_db_manager()
    async with db.session_scope() as session:
        binding = await session.get(SemanticPolicyBindingModel, binding_id)
        if not binding or binding.workspace_id != workspace_id:
            raise SemanticBindingError("策略绑定不存在", code="binding_not_found", status_code=404)
        await _assert_actor_scope(
            session, workspace_id, actor_id, "semantic_access:manage",
            binding.target_type, binding.target_id,
            bool(getattr(binding, "include_descendants", False)),
        )
        datasource_id = binding.datasource_id

    suggestion = await access_service.parse_natural_language(
        workspace_id,
        {"datasource_id": datasource_id, "source_text": source_text},
    )
    draft_patch = dict(suggestion.get("draft_patch") or {})
    definition = {
        "name": draft_patch.get("name") or "自然语言建议",
        "tables": list(draft_patch.get("tables") or []),
    }
    async with db.session_scope() as session:
        binding = await session.get(SemanticPolicyBindingModel, binding_id)
        if not binding or binding.workspace_id != workspace_id:
            raise SemanticBindingError("策略绑定不存在", code="binding_not_found", status_code=404)
        datasource = await session.get(SemanticDatasourceModel, datasource_id)
        prepared, ownership_blockers = await _prepare_definition_v3(
            session, binding, definition, actor_id,
        )
        compilation = await access_service._compile_definition(
            session,
            workspace_id,
            datasource_id,
            prepared,
            schema_fingerprint=getattr(datasource, "schema_fingerprint", None),
        )
    return {
        **suggestion,
        "draft_patch": definition,
        "validation": {
            "blockers": ownership_blockers + list(compilation["validation"].get("blockers") or []),
            "warnings": list(compilation["validation"].get("warnings") or []),
        },
        "compile_summary": compilation["summary"],
        "binding_id": binding_id,
    }


async def _affected_user_count(session, binding: SemanticPolicyBindingModel) -> int:
    now = datetime.utcnow()
    if binding.target_type == "baseline":
        return int((await session.execute(select(func.count(UserModel.id)).where(
            UserModel.workspace_id == binding.workspace_id,
            UserModel.disabled == False,  # noqa: E712
        ))).scalar_one() or 0)
    if binding.target_type == "user":
        user = await session.get(UserModel, binding.target_id)
        return int(bool(
            user and user.workspace_id == binding.workspace_id and not user.disabled
        ))
    if binding.target_type == "position":
        return int((await session.execute(select(func.count(distinct(AssignmentModel.user_id))).where(
            AssignmentModel.workspace_id == binding.workspace_id,
            AssignmentModel.position_id == int(binding.target_id),
            AssignmentModel.status == True,  # noqa: E712
            AssignmentModel.starts_at <= now,
            or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
        ))).scalar_one() or 0)
    if binding.target_type == "org_unit":
        org_ids = await _target_org_scope_v3(
            session,
            binding.workspace_id,
            "org_unit",
            binding.target_id,
            bool(getattr(binding, "include_descendants", True)),
        )
        return int((await session.execute(
            select(func.count(distinct(AssignmentModel.user_id)))
            .join(PositionModel, PositionModel.id == AssignmentModel.position_id)
            .where(
                AssignmentModel.workspace_id == binding.workspace_id,
                PositionModel.org_unit_id.in_(org_ids or [-1]),
                AssignmentModel.status == True,  # noqa: E712
                AssignmentModel.starts_at <= now,
                or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
            )
        )).scalar_one() or 0)
    if binding.target_type == "user_exception":
        exception = await session.get(AuthorizationExceptionModel, int(binding.target_id))
        return 1 if exception and exception.status and exception.starts_at <= now < exception.ends_at else 0
    grant = await session.get(RoleBindingModel, int(binding.target_id))
    if not grant or not grant.status or grant.starts_at > now or (grant.ends_at and grant.ends_at <= now):
        return 0
    return int((await session.execute(select(func.count(distinct(AssignmentModel.user_id))).where(
        AssignmentModel.workspace_id == binding.workspace_id,
        AssignmentModel.position_id == grant.position_id,
        AssignmentModel.status == True,  # noqa: E712
        AssignmentModel.starts_at <= now,
        or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
    ))).scalar_one() or 0)


async def list_versions(
    workspace_id: str, binding_id: int, actor_id: str,
) -> list[dict[str, Any]]:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        binding = await session.get(SemanticPolicyBindingModel, binding_id)
        if not binding or binding.workspace_id != workspace_id:
            raise SemanticBindingError("策略绑定不存在", code="binding_not_found", status_code=404)
        await _assert_actor_scope(
            session, workspace_id, actor_id, "semantic_access:view",
            binding.target_type, binding.target_id,
            bool(getattr(binding, "include_descendants", False)),
        )
        rows = (await session.execute(select(SemanticPolicyVersionModel).where(
            SemanticPolicyVersionModel.binding_id == binding_id,
            SemanticPolicyVersionModel.workspace_id == workspace_id,
        ).order_by(SemanticPolicyVersionModel.version.desc()))).scalars().all()
        return [_version_payload(row, active=row.id == binding.active_version_id) for row in rows]


async def rollback_version(
    workspace_id: str,
    binding_id: int,
    version_id: int,
    expected_revision: int,
    actor_id: str,
) -> dict[str, Any]:
    """Rollback by copying an immutable historical definition into a new version."""
    db = get_async_db_manager()
    async with db.session_scope() as session:
        version = await session.get(SemanticPolicyVersionModel, version_id)
        if (
            not version
            or version.workspace_id != workspace_id
            or version.binding_id != binding_id
        ):
            raise SemanticBindingError("历史版本不存在", code="version_not_found", status_code=404)
        definition = dict(version.definition_json or {})
        source_text = f"rollback-from-version:{version.version}"
    return await save_and_activate(
        workspace_id,
        binding_id,
        expected_revision,
        definition,
        actor_id,
        source_text=source_text,
        confirm_warnings=True,
        audit_action="semantic_policy.rollback",
        source_type="rollback",
        source_ref=f"version:{version_id}",
    )


async def get_binding(
    workspace_id: str, binding_id: int, actor_id: str,
) -> dict[str, Any]:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        binding = await session.get(SemanticPolicyBindingModel, binding_id)
        if not binding or binding.workspace_id != workspace_id:
            raise SemanticBindingError("策略绑定不存在", code="binding_not_found", status_code=404)
        await _assert_actor_scope(
            session, workspace_id, actor_id, "semantic_access:view",
            binding.target_type, binding.target_id,
            bool(getattr(binding, "include_descendants", False)),
        )
        payload = _binding_payload(binding)
        if binding.active_version_id:
            version = await session.get(SemanticPolicyVersionModel, binding.active_version_id)
            payload["active_version"] = _version_payload(version) if version else None
        return payload


async def get_target_policy(
    workspace_id: str,
    datasource_id: int,
    target_type: str,
    target_id: str,
    actor_id: str,
) -> dict[str, Any]:
    """Read a direct policy without creating an empty binding."""
    if target_type not in TARGET_TYPES:
        raise SemanticBindingError("授权目标类型无效", code="target_invalid")
    db = get_async_db_manager()
    async with db.session_scope() as session:
        datasource = await session.get(SemanticDatasourceModel, datasource_id)
        if not datasource or datasource.workspace_id != workspace_id:
            raise SemanticBindingError("数据源不存在", code="datasource_not_found", status_code=404)
        await _target_exists_v3(session, workspace_id, target_type, target_id)
        binding = (await session.execute(select(SemanticPolicyBindingModel).where(
            SemanticPolicyBindingModel.workspace_id == workspace_id,
            SemanticPolicyBindingModel.datasource_id == datasource_id,
            SemanticPolicyBindingModel.target_type == target_type,
            SemanticPolicyBindingModel.target_id == target_id,
        ))).scalar_one_or_none()
        include_descendants = target_type == "org_unit"
        await _assert_actor_scope_v3(
            session,
            workspace_id,
            actor_id,
            "semantic_access:view",
            target_type,
            target_id,
            include_descendants,
        )
        policy_context = await _effective_policy_context(
            session,
            workspace_id,
            datasource_id,
            target_type=target_type,
            target_id=target_id,
        )
        effective_tables = list(policy_context["effective"].values())
        if binding is None:
            return {
                "id": None,
                "binding_id": None,
                "workspace_id": workspace_id,
                "datasource_id": datasource_id,
                "target_type": target_type,
                "target_id": target_id,
                "include_descendants": target_type == "org_unit",
                "active_version_id": None,
                "active_version": None,
                "revision": 0,
                "status": True,
                "affected_user_count": await _affected_user_count(session, SimpleNamespace(
                    workspace_id=workspace_id,
                    target_type=target_type,
                    target_id=target_id,
                    include_descendants=include_descendants,
                )),
                "effective_tables": effective_tables,
            }
        payload = _binding_payload(binding)
        if binding.active_version_id:
            version = await session.get(SemanticPolicyVersionModel, binding.active_version_id)
            payload["active_version"] = _version_payload(version) if version else None
        else:
            payload["active_version"] = None
        payload["binding_id"] = binding.id
        payload["affected_user_count"] = await _affected_user_count(session, binding)
        payload["include_descendants"] = target_type == "org_unit"
        payload["effective_tables"] = effective_tables
        return payload


async def save_target_policy(
    workspace_id: str,
    datasource_id: int,
    target_type: str,
    target_id: str,
    expected_revision: int,
    definition: dict[str, Any],
    actor_id: str,
    include_descendants: bool = False,
    source_text: str | None = None,
    confirm_warnings: bool = False,
) -> dict[str, Any]:
    """Atomically create/update a target binding and activate its next version."""
    if target_type not in TARGET_TYPES:
        raise SemanticBindingError("授权目标类型无效", code="target_invalid")
    db = get_async_db_manager()
    async with db.session_scope() as session:
        datasource = await session.get(SemanticDatasourceModel, datasource_id)
        if not datasource or datasource.workspace_id != workspace_id:
            raise SemanticBindingError("数据源不存在", code="datasource_not_found", status_code=404)
        await _target_exists_v3(session, workspace_id, target_type, target_id)
        requested_descendants = target_type == "org_unit"
        await _assert_actor_scope_v3(
            session,
            workspace_id,
            actor_id,
            "semantic_access:manage",
            target_type,
            target_id,
            requested_descendants,
        )
        binding = (await session.execute(select(SemanticPolicyBindingModel).where(
            SemanticPolicyBindingModel.workspace_id == workspace_id,
            SemanticPolicyBindingModel.datasource_id == datasource_id,
            SemanticPolicyBindingModel.target_type == target_type,
            SemanticPolicyBindingModel.target_id == target_id,
        ).with_for_update())).scalar_one_or_none()
        if binding is None:
            if expected_revision != 0:
                raise SemanticBindingError(
                    "配置已被其他人修改，请刷新后重试",
                    code="revision_conflict",
                    status_code=409,
                    details={"current_revision": 0},
                )
            binding = SemanticPolicyBindingModel(
                workspace_id=workspace_id,
                datasource_id=datasource_id,
                target_type=target_type,
                target_id=target_id,
                include_descendants=requested_descendants,
                status=True,
                created_by=actor_id,
            )
            session.add(binding)
            await session.flush()
            previous: dict[str, Any] | None = None
        else:
            if binding.revision != expected_revision:
                raise SemanticBindingError(
                    "配置已被其他人修改，请刷新后重试",
                    code="revision_conflict",
                    status_code=409,
                    details={"current_revision": binding.revision},
                )
            previous = _binding_payload(binding)
            binding.status = True
            binding.include_descendants = requested_descendants

        version, _compilation, warnings = await persist_target_policy_version_in_session(
            session,
            binding,
            definition,
            actor_id,
            source_text=source_text,
            source_type="natural_language" if source_text else "manual",
            confirm_warnings=confirm_warnings,
        )
        audit = await record_authorization_audit(
            session,
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="semantic_policy.target.activate",
            target_type=f"semantic_{target_type}",
            target_id=target_id,
            before=previous,
            after=_binding_payload(binding),
        )
        await bump_authorization_revision(session, workspace_id)
        return {
            **_binding_payload(binding),
            "binding_id": binding.id,
            "active_version": _version_payload(version),
            "validation": {"blockers": [], "warnings": warnings},
            "audit_id": audit.id,
            "affected_user_count": await _affected_user_count(session, binding),
        }


async def save_ownership_mapping(
    workspace_id: str, datasource_id: int, table_id: int, payload: dict[str, Any], actor_id: str,
) -> dict[str, Any]:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        context = await build_effective_access_context(session, workspace_id, actor_id)
        if not _context_allows_scope(context, "semantic_access:manage", None):
            raise SemanticBindingError(
                "归属映射影响整个数据源，需要工作区级问数权限管理能力",
                code="workspace_scope_required",
                status_code=403,
            )
        _row, before, after = await upsert_ownership_mapping_in_session(
            session,
            workspace_id=workspace_id,
            datasource_id=datasource_id,
            table_id=table_id,
            payload=payload,
            actor_id=actor_id,
        )
        audit = await record_authorization_audit(
            session,
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="semantic_ownership_mapping.update",
            target_type="semantic_ownership_mapping",
            target_id=table_id,
            before=before,
            after=after,
        )
        revision = await bump_authorization_revision(session, workspace_id)
        return {**after, "audit_id": audit.id, "authorization_revision": revision}


async def get_ownership_mappings(workspace_id: str, datasource_id: int) -> list[dict[str, Any]]:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        rows = (await session.execute(select(SemanticOwnershipMappingModel).where(
            SemanticOwnershipMappingModel.workspace_id == workspace_id,
            SemanticOwnershipMappingModel.datasource_id == datasource_id,
        ).order_by(SemanticOwnershipMappingModel.table_id))).scalars().all()
        return [{
            "table_id": row.table_id,
            "org_column_id": row.org_column_id,
            "org_value_kind": row.org_value_kind,
            "org_value_mapping": row.org_value_mapping_json or {},
            "user_column_id": row.user_column_id,
            "user_value_kind": row.user_value_kind,
        } for row in rows]


def _replace_authorization_condition(
    condition: dict[str, Any], *, column_id: int, values: list[Any],
) -> dict[str, Any]:
    if condition.get("_authorization_scope") or condition.get("_organization_scope"):
        return {
            **condition,
            "column_id": column_id,
            "operator": "in",
            "value": values,
        }
    if isinstance(condition.get("rules"), list):
        return {
            **condition,
            "rules": [
                _replace_authorization_condition(item, column_id=column_id, values=values)
                if isinstance(item, dict) else item
                for item in condition["rules"]
            ],
        }
    return condition


def _organization_scope_marker(condition: dict[str, Any]) -> dict[str, Any] | None:
    marker = condition.get("_organization_scope")
    if isinstance(marker, dict):
        return marker
    if condition.get("_authorization_scope"):
        return {"type": "target_org_tree"}
    for item in condition.get("rules") or []:
        if isinstance(item, dict):
            found = _organization_scope_marker(item)
            if found:
                return found
    return None


async def _refresh_runtime_authorization_condition(
    session,
    binding: SemanticPolicyBindingModel,
    table_id: int,
    condition: dict[str, Any],
) -> dict[str, Any]:
    marker = _organization_scope_marker(condition)
    if not marker:
        return condition
    mapping = (await session.execute(select(SemanticOwnershipMappingModel).where(
        SemanticOwnershipMappingModel.workspace_id == binding.workspace_id,
        SemanticOwnershipMappingModel.datasource_id == binding.datasource_id,
        SemanticOwnershipMappingModel.table_id == table_id,
    ))).scalar_one_or_none()
    if not mapping or not mapping.org_column_id:
        # A missing mapping after activation must fail closed.
        if marker.get("unowned_access") == "table_grantees":
            column_id = int(marker.get("column_id") or 0)
            return {"column_id": column_id, "operator": "in", "value": []}
        return _replace_authorization_condition(condition, column_id=-1, values=[])
    scope_type = marker.get("type") or "target_org"
    if scope_type == "custom_org":
        org_ids = await _custom_org_scope_ids(
            session, binding.workspace_id, marker,
        )
    elif scope_type in {
        "target_org", "target_org_tree", "primary_assignment", "all_assignments",
    }:
        org_ids = await _target_root_org_ids(session, binding, scope_type)
    else:
        org_ids = await _binding_org_scope(session, binding)
    if marker.get("unowned_access") == "table_grantees":
        return await _build_organization_condition(
            session,
            binding.workspace_id,
            mapping,
            list(org_ids or []),
            {key: value for key, value in marker.items() if key != "column_id"},
        )
    values = await _organization_values(
        session, binding.workspace_id, mapping, list(org_ids or []),
    )
    return _replace_authorization_condition(
        condition, column_id=int(mapping.org_column_id), values=values,
    )


def _binding_matches_assignments(
    binding,
    user_id: str,
    assignment_org_ids: set[int],
    assignment_position_ids: set[int],
    ancestor_org_ids: set[int],
) -> bool:
    """Pure subject matching used by runtime and regression tests."""
    if binding.target_type == "baseline":
        return True
    if binding.target_type == "user":
        return binding.target_id == user_id
    if binding.target_type == "position":
        return int(binding.target_id) in assignment_position_ids
    if binding.target_type == "org_unit":
        target_org_id = int(binding.target_id)
        return target_org_id in ancestor_org_ids
    return False


def _ancestor_chain(org_id: int, parent_by_org: dict[int, int | None]) -> list[int]:
    """Return an organization chain from the selected unit to the root."""
    chain = [int(org_id)]
    seen = {int(org_id)}
    current = parent_by_org.get(int(org_id))
    while current is not None and int(current) not in seen:
        current_id = int(current)
        chain.append(current_id)
        seen.add(current_id)
        current = parent_by_org.get(current_id)
    return chain


def _rules_by_binding(
    bindings: list[SemanticPolicyBindingModel],
    versions: dict[int, SemanticPolicyVersionModel],
) -> dict[int, dict[int, dict[str, Any]]]:
    result: dict[int, dict[int, dict[str, Any]]] = {}
    for binding in bindings:
        version = versions.get(int(binding.active_version_id or 0))
        result[int(binding.id)] = {
            int(rule["table_id"]): dict(rule)
            for rule in (version.definition_json if version else {}).get("tables") or []
            if isinstance(rule, dict) and rule.get("table_id")
        }
    return result


def _winning_bindings_for_table(
    table_id: int,
    *,
    baseline_bindings: list[SemanticPolicyBindingModel],
    user_binding: SemanticPolicyBindingModel | None,
    position_bindings: dict[int, SemanticPolicyBindingModel],
    org_bindings: dict[int, SemanticPolicyBindingModel],
    assignment_position_ids: set[int],
    assignment_org_ids: set[int],
    parent_by_org: dict[int, int | None],
    rules: dict[int, dict[int, dict[str, Any]]],
) -> list[SemanticPolicyBindingModel]:
    """Choose the most-specific bindings that explicitly configure one table."""
    if user_binding and table_id in rules.get(int(user_binding.id), {}):
        return [user_binding]

    matched_positions = [
        binding
        for position_id, binding in position_bindings.items()
        if position_id in assignment_position_ids
        and table_id in rules.get(int(binding.id), {})
    ]
    if matched_positions:
        return matched_positions

    matched_departments: dict[int, SemanticPolicyBindingModel] = {}
    for org_id in assignment_org_ids:
        for candidate_id in _ancestor_chain(org_id, parent_by_org):
            binding = org_bindings.get(candidate_id)
            if binding and table_id in rules.get(int(binding.id), {}):
                matched_departments[int(binding.id)] = binding
                break
    if matched_departments:
        return list(matched_departments.values())

    return [
        binding for binding in baseline_bindings
        if table_id in rules.get(int(binding.id), {})
    ]


def _merge_winning_rules(
    table_id: int,
    bindings: list[SemanticPolicyBindingModel],
    rules: dict[int, dict[int, dict[str, Any]]],
) -> dict[str, Any] | None:
    matched = [rules[int(binding.id)][table_id] for binding in bindings]
    if not matched:
        return None
    hidden_columns = sorted({
        int(item) for rule in matched for item in rule.get("hidden_column_ids") or []
    })
    hidden_metrics = sorted({
        int(item) for rule in matched for item in rule.get("hidden_metric_ids") or []
    })
    if any(rule.get("decision") == "hidden" for rule in matched):
        return {
            "table_id": table_id,
            "decision": "hidden",
            "hidden_column_ids": hidden_columns,
            "hidden_metric_ids": hidden_metrics,
        }
    merged = {
        "table_id": table_id,
        "decision": "visible",
        "hidden_column_ids": hidden_columns,
        "hidden_metric_ids": hidden_metrics,
    }
    row_scopes = [rule.get("row_scope") for rule in matched if rule.get("row_scope")]
    if row_scopes:
        merged["row_scope"] = deepcopy(row_scopes[0])
        if len(row_scopes) > 1:
            merged["row_scopes"] = deepcopy(row_scopes)
    return merged


async def _effective_policy_context(
    session,
    workspace_id: str,
    datasource_id: int,
    *,
    target_type: str,
    target_id: str,
    definition_overrides: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Load sparse policies and resolve their effective per-table precedence.

    ``definition_overrides`` is used only by review-time configuration previews.
    Runtime callers omit it and therefore continue to use activated versions only.
    """
    bindings = list((await session.execute(select(SemanticPolicyBindingModel).where(
        SemanticPolicyBindingModel.workspace_id == workspace_id,
        SemanticPolicyBindingModel.datasource_id == datasource_id,
        SemanticPolicyBindingModel.status == True,  # noqa: E712
        SemanticPolicyBindingModel.active_version_id.is_not(None),
        SemanticPolicyBindingModel.target_type.in_(TARGET_TYPES),
    ))).scalars())
    version_ids = [int(row.active_version_id) for row in bindings if row.active_version_id]
    versions = {
        int(row.id): row for row in (await session.execute(select(
            SemanticPolicyVersionModel,
        ).where(SemanticPolicyVersionModel.id.in_(version_ids or [-1])))).scalars()
    }
    rules = _rules_by_binding(bindings, versions)
    if definition_overrides:
        binding_by_target = {
            (row.target_type, row.target_id): row for row in bindings
        }
        next_preview_id = -1
        for (override_type, override_id), definition in definition_overrides.items():
            binding = binding_by_target.get((override_type, override_id))
            if binding is None:
                binding = SimpleNamespace(
                    id=next_preview_id,
                    workspace_id=workspace_id,
                    datasource_id=datasource_id,
                    target_type=override_type,
                    target_id=override_id,
                    include_descendants=override_type == "org_unit",
                    active_version_id=None,
                    status=True,
                )
                next_preview_id -= 1
                bindings.append(binding)
                binding_by_target[(override_type, override_id)] = binding
            rules[int(binding.id)] = {
                int(rule["table_id"]): dict(rule)
                for rule in (definition or {}).get("tables") or []
                if isinstance(rule, dict) and rule.get("table_id")
            }
    baseline_bindings = [row for row in bindings if row.target_type == "baseline"]
    user_bindings = {row.target_id: row for row in bindings if row.target_type == "user"}
    position_bindings = {
        int(row.target_id): row for row in bindings if row.target_type == "position"
    }
    org_bindings = {
        int(row.target_id): row for row in bindings if row.target_type == "org_unit"
    }
    org_labels = dict((await session.execute(select(
        DepartmentModel.id, DepartmentModel.name,
    ).where(DepartmentModel.workspace_id == workspace_id))).all())
    position_labels = dict((await session.execute(select(
        PositionModel.id, PositionModel.name,
    ).where(PositionModel.workspace_id == workspace_id))).all())
    user_labels = dict((await session.execute(select(
        UserModel.id, UserModel.username,
    ).where(UserModel.workspace_id == workspace_id))).all())

    def source_label(binding: SemanticPolicyBindingModel) -> str:
        if binding.target_type == "baseline":
            return "全员基线"
        if binding.target_type == "org_unit":
            return str(org_labels.get(int(binding.target_id), binding.target_id))
        if binding.target_type == "position":
            return str(position_labels.get(int(binding.target_id), binding.target_id))
        return str(user_labels.get(binding.target_id, binding.target_id))
    parent_by_org = dict((await session.execute(select(
        DepartmentModel.id, DepartmentModel.parent_id,
    ).where(DepartmentModel.workspace_id == workspace_id))).all())

    assignment_position_ids: set[int] = set()
    assignment_org_ids: set[int] = set()
    user_binding = None
    if target_type == "org_unit":
        assignment_org_ids = {int(target_id)}
    elif target_type == "position":
        position = await session.get(PositionModel, int(target_id))
        if position:
            assignment_position_ids = {int(position.id)}
            assignment_org_ids = {int(position.org_unit_id)}
    elif target_type == "user":
        context = await build_effective_access_context(session, workspace_id, target_id)
        assignment_position_ids = {int(item.position_id) for item in context.assignments}
        assignment_org_ids = {int(item.org_unit_id) for item in context.assignments}
        user_binding = user_bindings.get(target_id)

    tables = list((await session.execute(select(SemanticTableModel).where(
        SemanticTableModel.workspace_id == workspace_id,
        SemanticTableModel.datasource_id == datasource_id,
        SemanticTableModel.status == "confirmed",
        SemanticTableModel.sync_state == "current",
        SemanticTableModel.is_queryable == True,  # noqa: E712
    ).order_by(SemanticTableModel.id))).scalars())
    direct_binding = next((
        row for row in bindings
        if row.target_type == target_type and row.target_id == target_id
    ), None)
    effective: dict[int, dict[str, Any]] = {}
    winners: dict[int, list[SemanticPolicyBindingModel]] = {}
    for table in tables:
        table_id = int(table.id)
        if target_type == "baseline":
            selected = [
                row for row in baseline_bindings
                if table_id in rules.get(int(row.id), {})
            ]
        else:
            selected = _winning_bindings_for_table(
                table_id,
                baseline_bindings=baseline_bindings,
                user_binding=user_binding,
                position_bindings=position_bindings,
                org_bindings=org_bindings,
                assignment_position_ids=assignment_position_ids,
                assignment_org_ids=assignment_org_ids,
                parent_by_org=parent_by_org,
                rules=rules,
            )
        winners[table_id] = selected
        merged = _merge_winning_rules(table_id, selected, rules) or {
            "table_id": table_id,
            "decision": "hidden",
            "hidden_column_ids": [],
            "hidden_metric_ids": [],
        }
        source_type = selected[0].target_type if selected else "default_deny"
        direct = bool(
            direct_binding
            and table_id in rules.get(int(direct_binding.id), {})
        )
        sources = [{
            "binding_id": int(row.id),
            "target_type": row.target_type,
            "target_id": row.target_id,
            "label": source_label(row),
        } for row in selected]
        seed = {key: deepcopy(value) for key, value in merged.items() if key != "row_scopes"}
        if target_type == "user" and len(merged.get("row_scopes") or []) > 1:
            seed["row_scope"] = {"type": "all_assignments"}
        effective[table_id] = {
            **merged,
            "is_direct_override": direct,
            "source": "direct" if direct else source_type,
            "sources": sources,
            "override_seed": seed,
        }
    return {
        "bindings": bindings,
        "rules": rules,
        "versions": versions,
        "tables": tables,
        "direct_binding": direct_binding,
        "effective": effective,
        "winners": winners,
    }


def _definition_expands_access(
    previous: dict[str, Any] | None,
    proposed: dict[str, Any],
) -> bool:
    """Conservative structural comparison used to route expansions through evidence."""
    old_rules = {
        int(item["table_id"]): item for item in (previous or {}).get("tables") or []
        if item.get("decision", "visible") == "visible"
    }
    new_rules = {
        int(item["table_id"]): item for item in proposed.get("tables") or []
        if item.get("decision", "visible") == "visible"
    }
    if not set(new_rules).issubset(old_rules):
        return True
    scope_rank = {
        "self": 0, "target_org": 1, "primary_assignment": 1,
        "target_org_tree": 2, "all_assignments": 2, "custom_org": 2,
        "custom": 2, "all": 3,
    }
    for table_id, new_rule in new_rules.items():
        old_rule = old_rules[table_id]
        if not set(new_rule.get("hidden_column_ids") or []).issuperset(
            old_rule.get("hidden_column_ids") or []
        ):
            return True
        if not set(new_rule.get("hidden_metric_ids") or []).issuperset(
            old_rule.get("hidden_metric_ids") or []
        ):
            return True
        old_scope = (old_rule.get("row_scope") or {}).get("type", "all")
        new_scope = (new_rule.get("row_scope") or {}).get("type", "all")
        if scope_rank.get(new_scope, 3) > scope_rank.get(old_scope, 3):
            return True
    return False


async def load_bound_semantic_runtime(
    workspace_id: str, datasource_id: int, user_id: str, as_of: datetime | None = None,
) -> dict[str, Any] | None:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        context = await build_effective_access_context(session, workspace_id, user_id, as_of)
        if context.is_workspace_admin:
            return {
                "is_admin": True,
                "effects_by_asset": {},
                "policy_ids": [],
                "authorization_revision": context.revision,
                "authorization_valid_until": (
                    context.valid_until.isoformat() if context.valid_until else None
                ),
                "authorization_model": "workspace_admin_bypass",
            }
        datasource = await session.get(SemanticDatasourceModel, datasource_id)
        if (
            not datasource
            or datasource.workspace_id != workspace_id
            or bool(getattr(datasource, "access_bootstrap_required", False))
        ):
            # Fail closed for ordinary accounts until an evidence set is compiled and published.
            return None
        policy_context = await _effective_policy_context(
            session,
            workspace_id,
            datasource_id,
            target_type="user",
            target_id=user_id,
        )
        winner_ids_by_table = {
            int(table_id): {int(binding.id) for binding in bindings}
            for table_id, bindings in policy_context["winners"].items()
            if bindings
        }
        binding_ids = sorted({
            binding_id for values in winner_ids_by_table.values() for binding_id in values
        })
        if not binding_ids:
            return None
        binding_by_id = {
            int(row.id): row for row in policy_context["bindings"]
            if int(row.id) in binding_ids
        }
        version_ids = [row.active_version_id for row in binding_by_id.values()]
        effects = (await session.execute(select(SemanticPolicyVersionEffectModel).where(
            SemanticPolicyVersionEffectModel.version_id.in_(version_ids)
        ).order_by(SemanticPolicyVersionEffectModel.priority.desc()))).scalars().all()
        column_tables = dict((await session.execute(select(
            SemanticColumnModel.id, SemanticColumnModel.table_id,
        ).where(
            SemanticColumnModel.workspace_id == workspace_id,
            SemanticColumnModel.datasource_id == datasource_id,
        ))).all())
        metric_tables = dict((await session.execute(select(
            SemanticMetricModel.id, SemanticMetricModel.table_id,
        ).where(
            SemanticMetricModel.workspace_id == workspace_id,
            SemanticMetricModel.datasource_id == datasource_id,
        ))).all())
        effects_by_asset: dict[str, list[dict[str, Any]]] = {}
        for row in effects:
            table_id = (
                int(row.asset_id)
                if row.asset_type == "table"
                else int((column_tables if row.asset_type == "column" else metric_tables).get(
                    int(row.asset_id), 0,
                ))
            )
            if int(row.binding_id) not in winner_ids_by_table.get(table_id, set()):
                continue
            key = f"{row.asset_type}:{row.asset_id}"
            condition = deepcopy(row.condition_json or {})
            if row.effect_type == "row_filter" and row.binding_id in binding_by_id:
                condition = await _refresh_runtime_authorization_condition(
                    session, binding_by_id[row.binding_id], int(row.asset_id), condition,
                )
            effects_by_asset.setdefault(key, []).append({
                "policy_id": row.version_id, "binding_id": row.binding_id,
                "target_type": binding_by_id[row.binding_id].target_type,
                "target_id": binding_by_id[row.binding_id].target_id,
                "effect_key": row.effect_key, "subject_type": "all", "subject_id": "*",
                "asset_type": row.asset_type, "asset_id": row.asset_id,
                "effect_type": row.effect_type, "condition_json": condition,
                "compiled_sql": row.compiled_sql, "priority": row.priority,
            })
        return {
            "is_admin": False,
            "effects_by_asset": effects_by_asset,
            "policy_ids": sorted({int(item) for item in version_ids if item}),
            "authorization_revision": context.revision,
            "authorization_valid_until": context.valid_until.isoformat() if context.valid_until else None,
        }


async def preview_bound_access(
    workspace_id: str,
    datasource_id: int,
    user_id: str,
    actor_id: str,
    as_of: datetime | None = None,
    candidate_binding_id: int | None = None,
    candidate_definition: dict[str, Any] | None = None,
    candidate_target_type: str | None = None,
    candidate_target_id: str | None = None,
    candidate_include_descendants: bool | None = None,
) -> dict[str, Any]:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        target_user = await session.get(UserModel, user_id)
        if not target_user or target_user.workspace_id != workspace_id:
            raise SemanticBindingError("预览用户不存在", code="user_not_found", status_code=404)
        context = await build_effective_access_context(session, workspace_id, user_id, as_of)
        actor = await build_effective_access_context(session, workspace_id, actor_id, as_of)
        user_org_ids = sorted({item.org_unit_id for item in context.assignments})
        if not _context_allows_scope(
            actor,
            "semantic_access:view",
            user_org_ids if user_org_ids else None,
        ):
            raise SemanticBindingError(
                "预览用户超出操作者的组织管理范围",
                code="preview_scope_denied",
                status_code=403,
            )
    runtime = await load_bound_semantic_runtime(workspace_id, datasource_id, user_id, as_of)
    runtime = runtime or {"is_admin": False, "effects_by_asset": {}, "policy_ids": []}
    candidate_validation: dict[str, Any] | None = None
    if candidate_definition is not None:
        from app.services.semantic_access_policy_service import get_semantic_access_policy_service

        async with db.session_scope() as session:
            binding = None
            if candidate_binding_id is not None:
                binding = await session.get(SemanticPolicyBindingModel, candidate_binding_id)
                if (
                    not binding
                    or binding.workspace_id != workspace_id
                    or binding.datasource_id != datasource_id
                ):
                    raise SemanticBindingError("策略绑定不存在", code="binding_not_found", status_code=404)
                target_type = binding.target_type
                target_id = binding.target_id
                include_descendants = (
                    bool(candidate_include_descendants)
                    if candidate_include_descendants is not None
                    else bool(getattr(binding, "include_descendants", False))
                )
            else:
                if candidate_target_type not in TARGET_TYPES or not candidate_target_id:
                    raise SemanticBindingError(
                        "候选策略缺少授权目标",
                        code="candidate_target_required",
                    )
                target_type = candidate_target_type
                target_id = candidate_target_id
                include_descendants = bool(candidate_include_descendants)
                await _target_exists_v3(session, workspace_id, target_type, target_id)
                binding = SimpleNamespace(
                    id=None,
                    workspace_id=workspace_id,
                    datasource_id=datasource_id,
                    target_type=target_type,
                    target_id=target_id,
                    include_descendants=include_descendants,
                )
            await _assert_actor_scope(
                session, workspace_id, actor_id, "semantic_access:view",
                target_type, target_id, include_descendants,
            )
            assignment_org_ids = {int(item.org_unit_id) for item in context.assignments}
            assignment_position_ids = {int(item.position_id) for item in context.assignments}
            applicable = target_type == "baseline"
            if target_type == "user":
                applicable = target_id == user_id
            elif target_type == "position":
                applicable = int(target_id) in assignment_position_ids
            elif target_type == "org_unit":
                subject_org_ids = await _target_org_scope_v3(
                    session,
                    workspace_id,
                    "org_unit",
                    target_id,
                    include_descendants,
                )
                applicable = bool(assignment_org_ids.intersection(subject_org_ids or []))
            datasource = await session.get(SemanticDatasourceModel, datasource_id)
            prepared, ownership_blockers = await _prepare_definition_v3(
                session, binding, candidate_definition, actor_id,
            )
            compilation = await get_semantic_access_policy_service()._compile_definition(
                session,
                workspace_id,
                datasource_id,
                prepared,
                schema_fingerprint=getattr(datasource, "schema_fingerprint", None),
            )
            candidate_validation = {
                "blockers": ownership_blockers + list(compilation["validation"].get("blockers") or []),
                "warnings": list(compilation["validation"].get("warnings") or []),
            }
            if not runtime.get("is_admin"):
                retained: dict[str, list[dict[str, Any]]] = {}
                for key, items in runtime["effects_by_asset"].items():
                    kept = [item for item in items if item.get("binding_id") != candidate_binding_id]
                    if kept:
                        retained[key] = kept
                if applicable:
                    for effect in compilation["effects"]:
                        key = f'{effect["asset_type"]}:{effect["asset_id"]}'
                        retained.setdefault(key, []).append({
                            "policy_id": "candidate",
                            "binding_id": candidate_binding_id or "candidate",
                            "target_type": target_type,
                            "target_id": target_id,
                            "effect_key": effect["effect_key"],
                            "asset_type": effect["asset_type"],
                            "asset_id": effect["asset_id"],
                            "effect_type": effect["effect_type"],
                            "condition_json": effect.get("condition_json") or {},
                            "compiled_sql": effect.get("compiled_sql"),
                            "priority": effect.get("priority") or 100,
                        })
                runtime = {**runtime, "effects_by_asset": retained}
    decisions = []
    for asset_key, effects in runtime["effects_by_asset"].items():
        denied = any(item["effect_type"] == "hidden" for item in effects)
        allowed = any(item["effect_type"] == "visible" for item in effects) and not denied
        decisions.append({
            "asset_key": asset_key, "allowed": allowed,
            "reason": "explicit_hidden" if denied else "explicit_visible" if allowed else "default_deny",
            "sources": [{"binding_id": item.get("binding_id"), "version_id": item.get("policy_id"), "target_type": item.get("target_type"), "target_id": item.get("target_id"), "effect_type": item.get("effect_type")} for item in effects],
        })
    async with db.session_scope() as session:
        preview_user = await session.get(UserModel, user_id)
        preview_columns = (await session.execute(select(SemanticColumnModel).where(
            SemanticColumnModel.workspace_id == workspace_id,
            SemanticColumnModel.datasource_id == datasource_id,
        ))).scalars().all()
    columns_by_table: dict[int, dict[int, Any]] = {}
    for column in preview_columns:
        columns_by_table.setdefault(int(column.table_id), {})[int(column.id)] = column
    primary_assignment = next((item for item in context.assignments if item.is_primary), None)
    predicate_user_access = {
        "user_id": user_id,
        "username": getattr(preview_user, "username", None),
        "dept_id": getattr(primary_assignment, "org_unit_id", None),
        "scope_dept_ids": sorted({item.org_unit_id for item in context.assignments}),
    }
    from app.services.semantic_access_policy_service import get_semantic_access_policy_service
    access_service = get_semantic_access_policy_service()
    table_predicates: dict[str, str] = {}
    table_keys = [key for key in runtime["effects_by_asset"] if key.startswith("table:")]
    for key in table_keys:
        effects = runtime["effects_by_asset"][key]
        if any(item["effect_type"] == "hidden" for item in effects):
            table_predicates[key] = "1=0"
            continue
        visible = [item for item in effects if item["effect_type"] == "visible"]
        if not visible:
            table_predicates[key] = "1=0"
            continue
        if any((item.get("condition_json") or {}).get("row_scope", {}).get("type") == "all" for item in visible):
            table_predicates[key] = ""
            continue
        table_id = int(key.split(":", 1)[1])
        predicates = []
        for item in effects:
            if item["effect_type"] != "row_filter":
                continue
            try:
                predicates.append(access_service.compile_condition(
                    item.get("condition_json") or {},
                    columns_by_table.get(table_id, {}),
                    alias="t",
                    table_id=table_id,
                    user_access=predicate_user_access,
                ))
            except Exception:
                predicates.append("1=0")
        predicates = [item for item in predicates if item]
        table_predicates[key] = " OR ".join(f"({item})" for item in predicates) if predicates else "1=0"
    return {
        "authorization": context.to_dict(),
        "semantic": {
            **runtime,
            "decisions": decisions,
            "table_predicates": table_predicates,
            "candidate_validation": candidate_validation,
        },
    }


def _binding_payload(row: SemanticPolicyBindingModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "binding_id": row.id,
        "workspace_id": row.workspace_id,
        "datasource_id": row.datasource_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "include_descendants": bool(getattr(row, "include_descendants", False)),
        "active_version_id": row.active_version_id,
        "revision": row.revision,
        "status": bool(row.status),
    }


def _version_payload(row: SemanticPolicyVersionModel, active: bool | None = None) -> dict[str, Any]:
    payload = {"id": row.id, "binding_id": row.binding_id, "version": row.version, "definition": row.definition_json or {}, "source_text": row.source_text, "source_type": getattr(row, "source_type", "manual"), "source_ref": getattr(row, "source_ref", None), "validation": row.validation_json or {}, "compile_summary": row.compile_summary_json or {}, "created_by": row.created_by, "created_at": row.created_at}
    if active is not None: payload["active"] = active
    return payload
