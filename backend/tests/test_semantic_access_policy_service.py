from types import SimpleNamespace

from app.services.semantic_access_policy_service import SemanticAccessPolicyService


def _effect(
    effect_type: str,
    *,
    asset_type: str = "table",
    asset_id: int = 1,
    subject_type: str = "role",
    subject_id: str = "7",
    condition: dict | None = None,
    policy_id: int = 10,
) -> dict:
    return {
        "policy_id": policy_id,
        "effect_key": f"{effect_type}:{asset_type}:{asset_id}:{subject_type}:{subject_id}",
        "subject_type": subject_type,
        "subject_id": subject_id,
        "asset_type": asset_type,
        "asset_id": asset_id,
        "effect_type": effect_type,
        "condition_json": condition or {},
        "compiled_sql": None,
        "priority": 1000 if effect_type == "hidden" else 100,
    }


def _access(service: SemanticAccessPolicyService, effects: list[dict], **overrides) -> dict:
    access = {
        "user_id": "u1",
        "username": "alice",
        "role_ids": ["7"],
        "role_names": ["Sales"],
        "is_admin": False,
        "dept_id": 3,
        "scope_dept_ids": [3, 4],
    }
    access.update(overrides)
    access["semantic_access"] = service._runtime_from_effects(effects, access)
    return access


def _table() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        status="confirmed",
        is_queryable=True,
        sync_state="current",
        is_sensitive=False,
        business_name="Orders",
    )


def _column(column_id: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        id=column_id,
        table_id=1,
        status="confirmed",
        is_queryable=True,
        sync_state="current",
        is_sensitive=False,
        business_name="Mobile",
        physical_name="mobile",
    )


def test_unconfigured_member_is_denied_by_default():
    service = SemanticAccessPolicyService()
    access = _access(service, [])

    decision = service.check_object_access("table", _table(), access)

    assert decision["allowed"] is False
    assert decision["reason"] == "default_hidden"


def test_visible_table_is_inherited_by_columns_and_metrics():
    service = SemanticAccessPolicyService()
    access = _access(
        service,
        [_effect("visible", condition={"row_scope": {"type": "all"}})],
    )
    metric = SimpleNamespace(
        id=8,
        table_id=1,
        status="confirmed",
        is_queryable=True,
        sync_state="current",
        business_name="Revenue",
    )

    assert service.check_object_access("table", _table(), access)["allowed"] is True
    assert service.check_object_access("column", _column(), access)["action"] == "allow_inherited"
    assert service.check_object_access("metric", metric, access)["allowed"] is True


def test_explicit_child_hidden_wins_over_table_visibility():
    service = SemanticAccessPolicyService()
    access = _access(
        service,
        [
            _effect("visible", condition={"row_scope": {"type": "all"}}),
            _effect("hidden", asset_type="column", asset_id=2, subject_type="user", subject_id="u1"),
        ],
    )

    decision = service.check_object_access("column", _column(), access)

    assert decision["allowed"] is False
    assert decision["reason"] == "explicit_hidden"


def test_metric_hidden_by_dependency_reports_the_hidden_columns():
    service = SemanticAccessPolicyService()
    access = _access(
        service,
        [
            _effect("visible", condition={"row_scope": {"type": "all"}}),
            _effect(
                "hidden",
                asset_type="metric",
                asset_id=8,
                subject_type="user",
                subject_id="u1",
                condition={"dependency_column_ids": [5]},
            ),
        ],
    )
    metric = SimpleNamespace(
        id=8,
        table_id=1,
        status="confirmed",
        is_queryable=True,
        sync_state="current",
        business_name="Gross margin",
    )

    decision = service.check_object_access("metric", metric, access)

    assert decision["reason"] == "metric_dependency_hidden"
    assert decision["dependency_column_ids"] == [5]


def test_explicit_table_hidden_wins_over_any_visible_grant():
    service = SemanticAccessPolicyService()
    access = _access(
        service,
        [
            _effect("visible", condition={"row_scope": {"type": "all"}}),
            _effect("hidden", subject_type="all", subject_id="*", policy_id=11),
        ],
    )

    table_decision = service.check_object_access("table", _table(), access)
    column_decision = service.check_object_access("column", _column(), access)

    assert table_decision["reason"] == "explicit_hidden"
    assert column_decision["reason"] == "parent_hidden"


def test_matching_row_grants_are_combined_with_or():
    service = SemanticAccessPolicyService()
    columns = [
        SimpleNamespace(id=2, table_id=1, physical_name="owner_id"),
        SimpleNamespace(id=3, table_id=1, physical_name="department_id"),
    ]
    access = _access(
        service,
        [
            _effect("visible", condition={"row_scope": {"type": "self"}}),
            _effect(
                "row_filter",
                condition={"column_id": 2, "operator": "=", "value_source": "current_user.id"},
            ),
            _effect("visible", subject_type="user", subject_id="u1", condition={"row_scope": {"type": "department"}}, policy_id=11),
            _effect(
                "row_filter",
                subject_type="user",
                subject_id="u1",
                condition={"column_id": 3, "operator": "in", "value_source": "current_user.scope_dept_ids"},
                policy_id=11,
            ),
        ],
    )

    predicate = service.compile_table_predicate(_table(), columns, access, alias="o")

    assert "o.`owner_id` = 'u1'" in predicate
    assert "o.`department_id` IN (3, 4)" in predicate
    assert " OR " in predicate


def test_all_row_scope_dominates_restricted_grants():
    service = SemanticAccessPolicyService()
    access = _access(
        service,
        [
            _effect("visible", condition={"row_scope": {"type": "all"}}),
            _effect("visible", subject_type="user", subject_id="u1", condition={"row_scope": {"type": "self"}}),
            _effect(
                "row_filter",
                subject_type="user",
                subject_id="u1",
                condition={"column_id": 2, "operator": "=", "value_source": "current_user.id"},
            ),
        ],
    )

    assert service.compile_table_predicate(_table(), [_column()], access, alias="o") == ""


def test_missing_dynamic_value_fails_closed_for_that_grant():
    service = SemanticAccessPolicyService()
    access = _access(
        service,
        [
            _effect("visible", condition={"row_scope": {"type": "department"}}),
            _effect(
                "row_filter",
                condition={"column_id": 3, "operator": "in", "value_source": "current_user.scope_dept_ids"},
            ),
        ],
        scope_dept_ids=[],
    )
    department_column = SimpleNamespace(id=3, table_id=1, physical_name="department_id")

    assert service.compile_table_predicate(_table(), [department_column], access, alias="o") == "1=0"


def test_hidden_security_column_can_still_enforce_row_scope():
    service = SemanticAccessPolicyService()
    access = _access(
        service,
        [
            _effect("visible", condition={"row_scope": {"type": "self"}}),
            _effect("hidden", asset_type="column", asset_id=2, subject_type="user", subject_id="u1"),
            _effect(
                "row_filter",
                condition={"column_id": 2, "operator": "=", "value_source": "current_user.id"},
            ),
        ],
    )

    assert service.check_object_access("column", _column(), access)["allowed"] is False
    assert "owner_id" in service.compile_table_predicate(
        _table(),
        [SimpleNamespace(id=2, table_id=1, physical_name="owner_id")],
        access,
        alias="o",
    )


def test_custom_condition_preserves_and_or_structure_and_safely_splits_in_values():
    service = SemanticAccessPolicyService()
    columns = {
        2: SimpleNamespace(id=2, table_id=1, physical_name="region"),
        3: SimpleNamespace(id=3, table_id=1, physical_name="status"),
    }
    condition = {
        "op": "AND",
        "rules": [
            {"column_id": 2, "operator": "in", "value": "华东,华南"},
            {"column_id": 3, "operator": "!=", "value": "closed"},
        ],
    }

    sql = service.compile_condition(condition, columns, alias="o", table_id=1, user_access={})

    assert "o.`region` IN ('华东', '华南')" in sql
    assert "o.`status` != 'closed'" in sql
    assert " AND " in sql
