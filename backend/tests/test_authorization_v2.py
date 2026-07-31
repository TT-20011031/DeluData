from datetime import datetime, timedelta
import asyncio
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from app.core.security.auth import User, apply_workspace_admin_access
from app.core.security.capabilities import (
    AUTHORIZATION_V2_MIGRATION_ID,
    CAPABILITIES,
    canonical_capability,
    is_registered_capability,
)
from app.api.organization.authorization import AssignmentWrite, ExceptionWrite, RoleBindingWrite
from app.agents.sql_worker import _requires_semantic_guard
from app.services.generation_authorization_guard import (
    LINEAGE_ATTR,
    lineage_rejection_reason,
    stamp_semantic_dataframe,
)
from app.core.security.rbac_deps import require_capability
from app.services.authorization_service import (
    EffectiveAccessContext,
    _grant_workspace_admin_access,
    has_capability,
    validate_exception_window,
)
from app.services.semantic_access_policy_service import SemanticAccessPolicyService
from app.services.semantic_policy_binding_service import (
    _binding_matches_assignments,
    _context_allows_scope,
    _organization_scope_marker,
    _replace_authorization_condition,
)


def _context() -> EffectiveAccessContext:
    return EffectiveAccessContext(
        workspace_id="workspace-a",
        user_id="user-a",
        as_of=datetime.utcnow(),
        revision=7,
        capability_scopes={
            "org:view": [10, 11],
            "database:query": "*",
        },
        denied_capability_scopes={"org:view": [11]},
    )


def test_capability_aliases_are_canonical() -> None:
    assert canonical_capability("sys:user:view") == "user:view"
    assert canonical_capability("sql:query") == "database:query"
    assert canonical_capability("museum:product_manage") == "museum:manage"


def test_all_checkperm_references_are_registered() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    references: set[str] = set()
    pattern = re.compile(r"Check(?:Workspace)?Perm\(\s*[\"']([^\"']+)")
    for path in app_root.rglob("*.py"):
        references.update(pattern.findall(path.read_text(encoding="utf-8")))
    assert references
    assert not {code for code in references if not is_registered_capability(code)}


def test_scope_allow_union_and_explicit_deny() -> None:
    context = _context()
    assert has_capability(context, "org:view", 10)
    assert not has_capability(context, "org:view", 11)
    assert not has_capability(context, "org:view", 12)
    assert has_capability(context, "database:query", 999)


def test_workspace_admin_bypass_grants_registered_capabilities_and_clears_denies() -> None:
    context = _context()
    granted = _grant_workspace_admin_access(context)

    assert granted.is_workspace_admin is True
    assert granted.denied_capability_scopes == {}
    assert granted.capability_scopes == {code: "*" for code in sorted(CAPABILITIES)}


def test_workspace_admin_identity_uses_owner_id_not_username_or_role_name() -> None:
    user = User(id="owner-id", username="test1", workspace_id="workspace-a")
    apply_workspace_admin_access(user, "different-user-id")
    assert user.is_workspace_admin is False

    apply_workspace_admin_access(user, "owner-id")
    assert user.is_workspace_admin is True
    assert user.role == "admin"
    assert set(user.permissions) == set(CAPABILITIES)


def test_require_capability_uses_scope_not_role_name() -> None:
    user = User(
        id="user-a",
        username="admin",
        role="admin",
        permissions=["org:view"],
        capability_scopes={"org:view": [10]},
        denied_capability_scopes={"org:view": [11]},
    )
    assert require_capability(user, "org:view", 10) is user
    with pytest.raises(Exception) as denied:
        require_capability(user, "org:view", 11)
    assert getattr(denied.value, "status_code", None) == 403
    with pytest.raises(Exception):
        require_capability(user, "role:manage")


def test_exception_window_is_positive_and_at_most_90_days() -> None:
    start = datetime.utcnow()
    validate_exception_window(start, start + timedelta(days=90))
    with pytest.raises(ValueError):
        validate_exception_window(start, start)
    with pytest.raises(ValueError):
        validate_exception_window(start, start + timedelta(days=90, seconds=1))


def test_bound_semantic_runtime_does_not_use_outer_admin_bypass() -> None:
    service = SemanticAccessPolicyService()
    table = SimpleNamespace(
        id=5,
        business_name="订单",
        physical_name="orders",
        status="confirmed",
        is_queryable=True,
        sync_state="current",
    )
    decision = service.check_object_access(
        "table",
        table,
        {"is_admin": True, "semantic_access": {"is_admin": False, "effects_by_asset": {}}},
    )
    assert decision["allowed"] is False
    assert decision["reason"] == "default_hidden"


def test_row_filters_from_multiple_allow_sources_are_or_combined() -> None:
    service = SemanticAccessPolicyService()
    table = SimpleNamespace(id=5)
    column = SimpleNamespace(id=8, table_id=5, physical_name="org_id")
    user_access = {
        "semantic_access": {
            "is_admin": False,
            "effects_by_asset": {
                "table:5": [
                    {"effect_type": "visible", "condition_json": {"row_scope": {"type": "authorization"}}},
                    {"effect_type": "row_filter", "condition_json": {"column_id": 8, "operator": "in", "value": [10]}},
                    {"effect_type": "row_filter", "condition_json": {"column_id": 8, "operator": "in", "value": [20]}},
                ]
            },
        }
    }
    predicate = service.compile_table_predicate(table, [column], user_access, alias="t")
    assert " OR " in predicate
    assert "10" in predicate and "20" in predicate


def test_explicit_hidden_table_overrides_allows() -> None:
    service = SemanticAccessPolicyService()
    table = SimpleNamespace(id=5)
    user_access = {
        "semantic_access": {
            "is_admin": False,
            "effects_by_asset": {
                "table:5": [
                    {"effect_type": "visible", "condition_json": {"row_scope": {"type": "all"}}},
                    {"effect_type": "hidden", "condition_json": {}},
                ]
            },
        }
    }
    assert service.compile_table_predicate(table, [], user_access, alias="t") == "1=0"


def test_owner_template_is_explicit_capability_set() -> None:
    assert "*" not in CAPABILITIES
    assert {"authorization:manage", "semantic_access:manage", "database:manage"}.issubset(CAPABILITIES)


def test_api_time_and_custom_scope_contracts_block_invalid_writes() -> None:
    start = datetime.utcnow()
    with pytest.raises(ValueError):
        AssignmentWrite(
            user_id="u", position_id=1, starts_at=start,
            ends_at=start - timedelta(seconds=1),
        )
    with pytest.raises(ValueError):
        RoleBindingWrite(
            position_id=1, role_id=2, scope_type="custom",
            custom_org_unit_ids=[], starts_at=start,
        )
    with pytest.raises(ValueError):
        ExceptionWrite(
            user_id="u", effect_type="allow", capability_codes=["not:registered"],
            reason="migration exception", owner_id="owner", starts_at=start,
            ends_at=start + timedelta(days=1),
        )


def test_organization_account_management_api_contract_is_stable() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app" / "api" / "organization" / "authorization.py"
    ).read_text(encoding="utf-8")
    assert 'assignment_state: Literal["unassigned"] | None' in source
    assert 'position_id: int | None = None' in source
    assert 'Literal["effective", "upcoming", "expired", "disabled", "all"]' in source
    assert '"username": account.username' in source
    assert '"child_org_units": child_count' in source
    assert '"role_bindings": role_binding_count' in source
    assert 'detail="assignment_overlap"' in source


def test_partial_semantic_manager_cannot_edit_workspace_baseline() -> None:
    context = EffectiveAccessContext(
        workspace_id="w", user_id="manager", as_of=datetime.utcnow(), revision=1,
        capability_scopes={"semantic_access:manage": [10, 11]},
    )
    assert _context_allows_scope(context, "semantic_access:manage", [10])
    assert not _context_allows_scope(context, "semantic_access:manage", [10, 12])
    assert not _context_allows_scope(context, "semantic_access:manage", None)


def test_sql_guard_applies_to_owner_and_role_name_admin() -> None:
    assert asyncio.run(_requires_semantic_guard("owner", "workspace", object())) is True


def test_protected_generation_lineage_rejects_other_user_and_stale_revision() -> None:
    dataframe = SimpleNamespace(attrs={})
    stamp_semantic_dataframe(
        dataframe, user_id="u1", workspace_id="w1", authorization_revision=7,
        authorization_valid_until="2030-01-01T00:00:00+00:00",
    )
    lineage = dataframe.attrs[LINEAGE_ATTR]
    assert lineage_rejection_reason(lineage, user_id="u1", authorization_revision=7) is None
    assert lineage_rejection_reason(lineage, user_id="u2", authorization_revision=7) == "protected_source_user_mismatch"
    assert lineage_rejection_reason(lineage, user_id="u1", authorization_revision=8) == "protected_source_authorization_changed"
    assert lineage_rejection_reason(
        lineage,
        user_id="u1",
        authorization_revision=7,
        now=datetime(2031, 1, 1),
    ) == "protected_source_authorization_expired"


def test_migration_cutover_id_and_safety_tables_are_stable() -> None:
    root = Path(__file__).resolve().parents[1]
    runner = (root / "migrations" / "run_authorization_v2_migration.py").read_text(encoding="utf-8")
    schema = (root / "migrations" / "authorization_v2.sql").read_text(encoding="utf-8")
    assert AUTHORIZATION_V2_MIGRATION_ID == "2026_07_authorization_v2"
    assert "status='complete'" in runner or "'complete'" in runner
    for table in (
        "sys_schema_migrations", "sys_assignments", "sys_role_bindings",
        "sys_authorization_exceptions", "semantic_policy_versions",
        "semantic_policy_version_effects", "authorization_migration_review_items",
    ):
        assert table in schema


def test_v2_cutover_default_denies_even_legacy_wildcard_admin(monkeypatch) -> None:
    import app.services.semantic_policy_binding_service as binding_service

    async def active():
        return True

    async def no_binding(*_args, **_kwargs):
        return None

    monkeypatch.setattr(binding_service, "authorization_v2_is_active", active)
    monkeypatch.setattr(binding_service, "load_bound_semantic_runtime", no_binding)
    runtime = asyncio.run(SemanticAccessPolicyService().load_runtime_access(
        "workspace", 1, {"user_id": "owner", "is_admin": True},
    ))
    assert runtime["is_admin"] is False
    assert runtime["effects_by_asset"] == {}
    assert runtime["authorization_model"] == "v2_default_deny"


def test_runtime_org_scope_refresh_preserves_custom_rule_and_updates_values() -> None:
    condition = {
        "op": "AND",
        "rules": [
            {"_authorization_scope": True, "column_id": 3, "operator": "in", "value": [1]},
            {"column_id": 4, "operator": "=", "value": "approved"},
        ],
    }
    refreshed = _replace_authorization_condition(condition, column_id=9, values=[20, 21])
    assert refreshed["rules"][0]["column_id"] == 9
    assert refreshed["rules"][0]["value"] == [20, 21]
    assert refreshed["rules"][1] == condition["rules"][1]


def test_runtime_org_scope_refresh_supports_new_organization_marker() -> None:
    condition = {
        "op": "AND",
        "rules": [
            {
                "_organization_scope": {
                    "type": "custom_org",
                    "org_unit_ids": [10],
                    "include_descendants": True,
                },
                "column_id": 3,
                "operator": "in",
                "value": [10],
            },
            {"column_id": 4, "operator": "=", "value": "approved"},
        ],
    }
    marker = _organization_scope_marker(condition)
    assert marker == {
        "type": "custom_org",
        "org_unit_ids": [10],
        "include_descendants": True,
    }
    refreshed = _replace_authorization_condition(condition, column_id=9, values=[10, 11])
    assert refreshed["rules"][0]["column_id"] == 9
    assert refreshed["rules"][0]["value"] == [10, 11]
    assert refreshed["rules"][1] == condition["rules"][1]


def test_semantic_organization_target_contract_and_cutover_are_stable() -> None:
    root = Path(__file__).resolve().parents[1]
    service = (root / "app" / "services" / "semantic_policy_binding_service.py").read_text(encoding="utf-8")
    api = (root / "app" / "api" / "config" / "semantic.py").read_text(encoding="utf-8")
    migration = (root / "migrations" / "run_semantic_org_target_migration.py").read_text(encoding="utf-8")
    assert 'TARGET_TYPES = {"baseline", "org_unit", "position", "user"}' in service
    assert "SemanticPolicyBindingModel.target_type.in_(TARGET_TYPES)" in service
    assert 'binding.target_type == "position"' in service
    assert 'binding.target_type == "org_unit"' in service
    assert '"/semantic/access-policies/{target_type}/{target_id}"' in api
    assert 'Literal["baseline", "org_unit", "position", "user"]' in api
    assert "candidate_target_type" in api
    assert "candidate_include_descendants" in api
    assert 'candidate_binding_id or "candidate"' in service
    assert "include_descendants" in migration
    assert "role_binding', 'user_exception" in migration
    assert "preserved_history" in migration


def test_semantic_targets_match_all_effective_assignments_and_department_ancestors() -> None:
    position = SimpleNamespace(
        target_type="position", target_id="8", include_descendants=False,
    )
    direct_department = SimpleNamespace(
        target_type="org_unit", target_id="20", include_descendants=False,
    )
    parent_tree = SimpleNamespace(
        target_type="org_unit", target_id="10", include_descendants=True,
    )
    account = SimpleNamespace(
        target_type="user", target_id="u1", include_descendants=False,
    )

    arguments = ("u1", {20, 30}, {8, 9}, {10, 20, 30})
    assert _binding_matches_assignments(position, *arguments)
    assert _binding_matches_assignments(direct_department, *arguments)
    assert _binding_matches_assignments(parent_tree, *arguments)
    assert _binding_matches_assignments(account, *arguments)
    assert not _binding_matches_assignments(
        SimpleNamespace(target_type="position", target_id="99"), *arguments,
    )


def test_workspace_provisioning_and_manual_sql_use_v2_contract() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    platform_router = (app_root / "api" / "platform" / "router.py").read_text(encoding="utf-8-sig")
    database_router = (app_root / "api" / "database" / "router.py").read_text(encoding="utf-8-sig")
    assert "admin_user.roles.append" not in platform_router
    assert "owner_assignment = AssignmentModel" in platform_router
    assert "owner_binding = RoleBindingModel" in platform_router
    assert "manual_sql_retired" in database_router
