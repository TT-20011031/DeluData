from types import SimpleNamespace

import pytest

from app.services.semantic_access_bootstrap_service import (
    SemanticAccessBootstrapError,
    SemanticAccessBootstrapService,
    _evidence_baseline_access,
    _evidence_field_access,
    _evidence_table_access,
    _merge_table_definition,
    _review_published_tables,
    _row_ownership_generation_summary,
)
from app.services import semantic_access_bootstrap_service as bootstrap_module
from app.entrypoints.semantic_access_bootstrap_worker import SemanticAccessBootstrapWorker
from app.services.semantic_policy_binding_service import (
    _build_organization_condition,
    _prepare_definition_v3,
)


def _service() -> SemanticAccessBootstrapService:
    return SemanticAccessBootstrapService.__new__(SemanticAccessBootstrapService)


@pytest.mark.asyncio
async def test_target_draft_includes_effective_inherited_tables(monkeypatch):
    inherited = [{
        "table_id": 1,
        "decision": "visible",
        "hidden_column_ids": [3],
        "hidden_metric_ids": [],
        "row_scope": {"type": "all"},
        "is_direct_override": False,
        "source": "org_unit",
        "sources": [{
            "binding_id": 8,
            "target_type": "org_unit",
            "target_id": "22",
            "label": "生产部",
        }],
        "override_seed": {
            "table_id": 1,
            "decision": "visible",
            "hidden_column_ids": [3],
            "hidden_metric_ids": [],
            "row_scope": {"type": "all"},
        },
    }]

    draft_definition = {"name": "生产部", "tables": [{
        "table_id": 1,
        "decision": "visible",
        "hidden_column_ids": [3],
        "hidden_metric_ids": [],
        "row_scope": {"type": "all"},
    }]}

    class Result:
        def __init__(self, *, rows=None, single=None):
            self.rows = rows or []
            self.single = single

        def scalar_one_or_none(self):
            return self.single

        def scalars(self):
            return self.rows

    class Session:
        def __init__(self):
            self.execute_count = 0

        async def execute(self, _query):
            self.execute_count += 1
            if self.execute_count == 1:
                return Result(rows=[SimpleNamespace(
                    target_type="org_unit",
                    target_id="22",
                    definition_json=draft_definition,
                )])
            return Result(single=None)

    class SessionScope:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_args):
            return None

    class Database:
        def session_scope(self):
            return SessionScope()

    captured = {}

    async def effective_context(*_args, **kwargs):
        captured.update(kwargs)
        return {"effective": {1: inherited[0]}}

    service = _service()

    async def allow(*_args, **_kwargs):
        return None

    async def run(*_args, **_kwargs):
        return SimpleNamespace(id=12, datasource_id=1, status="review_ready")

    async def label(*_args, **_kwargs):
        return "生产部经理"

    async def snapshot(*_args, **_kwargs):
        return None, {"name": "生产部经理", "tables": []}, None

    service._assert_workspace_manage = allow
    service._run = run
    service._resolve_target_label = label
    service._binding_snapshot = snapshot
    service._run_payload = lambda current: {"run_id": current.id}
    monkeypatch.setattr(bootstrap_module, "get_async_db_manager", lambda: Database())
    monkeypatch.setattr(bootstrap_module, "_effective_policy_context", effective_context)

    result = await service.get_target_draft(
        "workspace-1", 12, "position", "10", "admin-1",
    )

    assert result["target"]["definition"]["tables"] == []
    assert result["effective_tables"] == inherited
    assert captured["definition_overrides"] == {
        ("org_unit", "22"): draft_definition,
    }


def test_single_table_publish_preserves_other_active_rules():
    active = {
        "name": "生产部",
        "tables": [
            {"table_id": 1, "decision": "visible", "hidden_column_ids": [11]},
            {"table_id": 2, "decision": "visible", "hidden_column_ids": []},
        ],
    }
    draft = {
        "name": "生产部",
        "tables": [
            {"table_id": 1, "decision": "visible", "hidden_column_ids": []},
            {"table_id": 3, "decision": "visible", "hidden_column_ids": []},
        ],
    }

    merged = _merge_table_definition(active, draft, 1)

    assert merged["tables"] == [
        {"table_id": 1, "decision": "visible", "hidden_column_ids": []},
        {"table_id": 2, "decision": "visible", "hidden_column_ids": []},
    ]


def test_single_table_publish_can_remove_an_active_rule():
    active = {"name": "生产部", "tables": [
        {"table_id": 1, "decision": "visible"},
        {"table_id": 2, "decision": "visible"},
    ]}

    merged = _merge_table_definition(active, {"name": "生产部", "tables": []}, 1)

    assert merged["tables"] == [{"table_id": 2, "decision": "visible"}]


def test_published_table_review_state_reflects_manual_edits():
    candidates = [{
        "table_id": 1,
        "selected": True,
        "decision": "visible",
        "hidden_column_ids": [12],
        "hidden_metric_ids": [],
        "row_scope": "all",
        "review_state": "pending",
        "field_suggestions": [
            {"column_id": 11, "effective_decision": "visible", "review_state": "pending"},
            {"column_id": 12, "effective_decision": "hidden", "review_state": "pending"},
        ],
    }]
    edited_rule = {
        "table_id": 1,
        "decision": "visible",
        "hidden_column_ids": [],
        "hidden_metric_ids": [],
        "row_scope": {"type": "all"},
    }

    reviewed = _review_published_tables(candidates, {1: edited_rule}, {1})

    assert reviewed[0]["review_state"] == "modified"
    assert reviewed[0]["field_suggestions"][0]["review_state"] == "accepted"
    assert reviewed[0]["field_suggestions"][1]["review_state"] == "modified"


def test_row_ownership_generation_summary_prefers_v2_and_reads_v1():
    assert _row_ownership_generation_summary({
        "row_ownership_v1": {"version": 1, "candidates": ["legacy"]},
        "row_ownership_v2": {"version": 2, "candidates": ["current"]},
    }) == {"version": 2, "candidates": ["current"]}
    assert _row_ownership_generation_summary({
        "row_ownership_v1": {"version": 1, "candidates": ["legacy"]},
    }) == {"version": 1, "candidates": ["legacy"]}


def _table(*, sensitive: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=11, is_sensitive=sensitive)


def _classification(**overrides) -> dict:
    value = {
        "shared": True,
        "shared_confidence": 0.92,
        "global_reference": False,
        "global_reference_confidence": 0.0,
        "all_departments": False,
        "all_departments_confidence": 0.0,
        "reason": "公共业务元数据",
        "department_access": [
            {"org_unit_id": 7, "confidence": 0.91, "reason": "销售业务相关"},
        ],
    }
    value.update(overrides)
    return value


def test_baseline_only_selects_high_confidence_non_sensitive_shared_tables():
    service = _service()

    allowed = service._candidate_decision(
        table=_table(), item=_classification(), target_type="baseline", target_id="*",
        has_mapping=False, has_mapping_proposal=False,
    )
    medium = service._candidate_decision(
        table=_table(), item=_classification(shared_confidence=0.59),
        target_type="baseline", target_id="*", has_mapping=False, has_mapping_proposal=False,
    )
    sensitive = service._candidate_decision(
        table=_table(sensitive=True), item=_classification(), target_type="baseline", target_id="*",
        has_mapping=False, has_mapping_proposal=False,
    )

    assert allowed == {
        "selected": True,
        "confidence": 0.92,
        "reason": "公共业务元数据",
        "row_scope": "all",
        "requires_mapping": False,
    }
    assert medium["selected"] is False
    assert sensitive["selected"] is False


def test_only_active_first_level_departments_without_existing_versions_are_targets():
    service = _service()
    departments = [
        SimpleNamespace(id=1, parent_id=None, status=True),
        SimpleNamespace(id=2, parent_id=1, status=True),
        SimpleNamespace(id=3, parent_id=None, status=False),
        SimpleNamespace(id=4, parent_id=None, status=True),
    ]

    selected = service._eligible_top_departments(
        departments,
        configured={("org_unit", "4")},
    )

    assert [row.id for row in selected] == [1]


def test_department_business_table_no_longer_requires_ownership_mapping():
    service = _service()

    without_mapping = service._candidate_decision(
        table=_table(), item=_classification(), target_type="org_unit", target_id="7",
        has_mapping=False, has_mapping_proposal=False,
    )
    assert without_mapping["selected"] is True
    assert without_mapping["row_scope"] == "all"
    assert without_mapping["requires_mapping"] is False


def test_sensitive_fields_and_metrics_are_hidden_in_every_selected_table_rule():
    service = _service()
    context = {
        "columns_by_table": {
            11: [SimpleNamespace(id=101, is_sensitive=True), SimpleNamespace(id=102, is_sensitive=False)],
        },
        "metrics_by_table": {
            11: [SimpleNamespace(id=201, is_sensitive=False), SimpleNamespace(id=202, is_sensitive=True)],
        },
    }

    hidden_columns, hidden_metrics = service._sensitive_asset_exceptions(context, 11)

    assert hidden_columns == [101]
    assert hidden_metrics == [202]


def test_only_high_confidence_non_sensitive_global_reference_uses_all_scope():
    service = _service()

    allowed = service._candidate_decision(
        table=_table(), item=_classification(
            global_reference=True,
            global_reference_confidence=0.91,
            department_access=[],
        ),
        target_type="org_unit", target_id="7", has_mapping=False, has_mapping_proposal=False,
    )
    medium = service._candidate_decision(
        table=_table(),
        item=_classification(
            global_reference=True,
            global_reference_confidence=0.59,
            department_access=[{"org_unit_id": 7, "confidence": 0.2, "reason": "不直接相关"}],
        ),
        target_type="org_unit", target_id="7", has_mapping=False, has_mapping_proposal=False,
    )
    sensitive = service._candidate_decision(
        table=_table(sensitive=True), item=_classification(
            global_reference=True, global_reference_confidence=0.91,
            department_access=[{"org_unit_id": 7, "confidence": 0.2, "reason": "不直接相关"}],
        ),
        target_type="org_unit", target_id="7", has_mapping=False, has_mapping_proposal=False,
    )

    assert allowed["selected"] is True
    assert allowed["row_scope"] == "all"
    assert medium["selected"] is False
    assert sensitive["selected"] is False
    assert sensitive["row_scope"] == "all"


def test_non_sensitive_all_department_table_is_recommended_to_every_department():
    service = _service()
    item = _classification(
        all_departments=True,
        all_departments_confidence=0.76,
        department_access=[
            {"org_unit_id": 7, "confidence": 0.2, "reason": "单部门相关性低"},
            {"org_unit_id": 8, "confidence": 0.3, "reason": "单部门相关性低"},
        ],
    )

    decisions = [service._candidate_decision(
        table=_table(), item=item, target_type="org_unit", target_id=str(target_id),
        has_mapping=False, has_mapping_proposal=False,
    ) for target_id in (7, 8)]

    assert all(row["selected"] for row in decisions)
    assert all(row["confidence"] == 0.76 for row in decisions)
    assert all(row["row_scope"] == "all" for row in decisions)


def test_field_contract_supports_common_decision_and_department_override():
    service = _service()
    columns = [SimpleNamespace(id=101), SimpleNamespace(id=102)]
    context = {
        "baseline_enabled": True,
        "top_departments": [SimpleNamespace(id=7), SimpleNamespace(id=8)],
    }
    parsed = {"columns": [{
        "column_id": 101,
        "default_decision": "visible",
        "confidence": 0.88,
        "reason": "通用订单编号",
        "target_overrides": [],
    }, {
        "column_id": 102,
        "default_decision": "visible",
        "confidence": 0.81,
        "reason": "业务字段",
        "target_overrides": [{
            "target_type": "org_unit",
            "target_id": "8",
            "decision": "hidden",
            "confidence": 0.79,
            "reason": "仅销售部门需要",
        }],
    }]}

    result = service._validate_field_batch(parsed, columns, context, 11)

    assert result[101]["default_decision"] == "visible"
    assert result[102]["target_overrides"][0]["target_id"] == "8"


def test_field_suggestions_are_target_specific_and_force_sensitive_hidden():
    service = _service()
    context = {
        "columns_by_table": {11: [
            SimpleNamespace(id=101, is_sensitive=False, ordinal_position=1),
            SimpleNamespace(id=102, is_sensitive=False, ordinal_position=2),
            SimpleNamespace(id=103, is_sensitive=True, ordinal_position=3),
        ]},
    }
    fields = {
        101: {
            "column_id": 101, "default_decision": "visible", "confidence": 0.9,
            "reason": "通用字段", "target_overrides": [],
        },
        102: {
            "column_id": 102, "default_decision": "visible", "confidence": 0.9,
            "reason": "部门字段", "target_overrides": [{
                "target_type": "org_unit", "target_id": "8", "decision": "hidden",
                "confidence": 0.8, "reason": "部门 8 不需要",
            }],
        },
        103: {
            "column_id": 103, "default_decision": "visible", "confidence": 0.99,
            "reason": "模型误判", "target_overrides": [],
        },
    }

    _sales, sales_hidden, sales_summary = service._field_suggestions_for_target(
        context, 11, "org_unit", "7", fields,
    )
    admin, admin_hidden, admin_summary = service._field_suggestions_for_target(
        context, 11, "org_unit", "8", fields,
    )

    assert sales_hidden == [103]
    assert admin_hidden == [102, 103]
    assert sales_summary == {"visible": 2, "hidden": 1, "sensitive_hidden": 1, "attention": 0}
    assert admin_summary == {"visible": 1, "hidden": 2, "sensitive_hidden": 1, "attention": 0}
    assert next(row for row in admin if row["column_id"] == 103)["source"] == "governance"


def test_model_output_rejects_unknown_table_department_and_column_ids():
    service = _service()
    chunk = [SimpleNamespace(id=11)]
    context = {
        "top_departments": [SimpleNamespace(id=7)],
        "columns_by_table": {11: [SimpleNamespace(id=101)]},
    }
    valid = {
        "tables": [{
            "table_id": 11,
            "shared": False,
            "shared_confidence": 0.1,
            "global_reference": False,
            "global_reference_confidence": 0.1,
            "all_departments": False,
            "all_departments_confidence": 0.1,
            "department_access": [{"org_unit_id": 7, "confidence": 0.8}],
        }],
    }

    assert service._validate_batch(valid, chunk, context)[11]["department_access"][0]["org_unit_id"] == 7

    invalid_table = {"tables": [{**valid["tables"][0], "table_id": 99}]}
    invalid_department = {"tables": [{
        **valid["tables"][0],
        "department_access": [{"org_unit_id": 999, "confidence": 0.8}],
    }]}
    missing_department = {"tables": [{
        **valid["tables"][0],
        "department_access": [],
    }]}

    with pytest.raises(ValueError, match="表 ID"):
        service._validate_batch(invalid_table, chunk, context)
    with pytest.raises(ValueError, match="一级部门 ID"):
        service._validate_batch(invalid_department, chunk, context)
    with pytest.raises(ValueError, match="必须返回所有一级部门"):
        service._validate_batch(missing_department, chunk, context)


def test_generation_contract_requires_every_department_and_all_department_signal():
    service = _service()

    messages = service._generation_messages({"tables": [], "departments": []})
    prompt = messages[1]["content"]

    assert '"all_departments":false' in prompt
    assert "每个一级部门各返回一次" in prompt
    assert "不相关部门不要输出" not in prompt


def test_partial_batch_validation_keeps_valid_tables_and_retries_only_invalid_ones():
    service = _service()
    chunk = [SimpleNamespace(id=11), SimpleNamespace(id=12)]
    context = {
        "top_departments": [SimpleNamespace(id=7)],
        "columns_by_table": {11: [], 12: []},
    }
    table = {
        "shared": False,
        "shared_confidence": 0.1,
        "global_reference": False,
        "global_reference_confidence": 0.1,
        "all_departments": False,
        "all_departments_confidence": 0.1,
        "department_access": [{"org_unit_id": 7, "confidence": 0.1}],
    }
    parsed = {"tables": [
        {**table, "table_id": 11},
        {
            **table,
            "table_id": 12,
            "department_access": [],
        },
    ]}

    valid, errors = service._validate_batch_partial(parsed, chunk, context)

    assert list(valid) == [11]
    assert "必须返回所有一级部门" in errors[12]


def test_worker_lease_token_prevents_stale_worker_writes():
    service = _service()
    run = SimpleNamespace(
        status="running",
        worker_id="worker-b",
        run_token="new-token",
    )

    service._assert_worker_ownership(run, "worker-b", "new-token")
    with pytest.raises(SemanticAccessBootstrapError) as exc:
        service._assert_worker_ownership(run, "worker-a", "expired-token")

    assert exc.value.code == "worker_lease_lost"


@pytest.mark.asyncio
async def test_worker_passes_its_lease_identity_to_run_execution():
    class FakeService:
        executed: tuple[int, str | None, str | None] | None = None

        async def execute_run(self, run_id, *, worker_id=None, run_token=None):
            self.executed = (run_id, worker_id, run_token)

        async def heartbeat_run(self, *args, **kwargs):
            return False

    worker = SemanticAccessBootstrapWorker.__new__(SemanticAccessBootstrapWorker)
    worker.worker_id = "worker-test"
    worker.service = FakeService()
    worker.heartbeat_sec = 10

    await worker._process_run({"run_id": 9, "run_token": "lease-token"})

    assert worker.service.executed == (9, "worker-test", "lease-token")


def test_direct_draft_preserves_explicit_table_denies():
    service = _service()

    rule = service._rule_from_candidate({
        "table_id": 11,
        "selected": False,
        "decision": "hidden",
        "hidden_column_ids": [101],
        "hidden_metric_ids": [],
        "row_scope": "all",
    })

    assert rule["decision"] == "hidden"
    assert rule["hidden_column_ids"] == [101]
    assert "row_scope" not in rule


def test_direct_draft_keeps_row_scope_only_for_visible_tables():
    service = _service()

    rule = service._rule_from_candidate({
        "table_id": 11,
        "selected": True,
        "decision": "visible",
        "hidden_column_ids": [],
        "hidden_metric_ids": [],
        "row_scope": "target_org",
    })

    assert rule["decision"] == "visible"
    assert rule["row_scope"] == {"type": "target_org"}


def test_direct_draft_preserves_unowned_access_marker():
    service = _service()

    rule = service._rule_from_candidate({
        "table_id": 11,
        "selected": True,
        "decision": "visible",
        "hidden_column_ids": [],
        "hidden_metric_ids": [],
        "row_scope": {
            "type": "target_org_tree",
            "unowned_access": "table_grantees",
        },
    })

    assert rule["row_scope"] == {
        "type": "target_org_tree",
        "unowned_access": "table_grantees",
    }


def test_complete_ownership_domain_becomes_explicit_department_conditions():
    service = _service()
    mapping = SimpleNamespace(
        proposed_mapping_json={
            "org_column_id": 72,
            "org_value_kind": "external",
            "org_value_mapping": {
                "bindings": [
                    {
                        "source_type": "string",
                        "source_value": "生产部",
                        "target_kind": "org_unit",
                        "org_unit_id": 22,
                    },
                    {
                        "source_type": "string",
                        "source_value": "制造中心",
                        "target_kind": "org_unit",
                        "org_unit_id": 22,
                    },
                ],
            },
        },
        validation_json={
            "blockers": [],
            "unresolved_values": [],
            "null_rows": 0,
            "empty_rows": 0,
            "source_values": [
                {"source_type": "string", "source_value": "生产部"},
                {"source_type": "string", "source_value": "制造中心"},
            ],
        },
    )

    assert service._row_scope_suggestions_from_mapping(mapping) == {
        "22": {
            "type": "custom",
            "condition": {
                "column_id": 72,
                "operator": "in",
                "value": ["生产部", "制造中心"],
            },
        },
    }


@pytest.mark.parametrize("validation_patch", [
    {"null_rows": 1},
    {"empty_rows": 1},
    {"unresolved_values": [{"source_type": "string", "source_value": "未知部门"}]},
])
def test_incomplete_ownership_domain_keeps_full_table_visibility(validation_patch):
    service = _service()
    validation = {
        "blockers": [],
        "unresolved_values": [],
        "null_rows": 0,
        "empty_rows": 0,
        "source_values": [{"source_type": "string", "source_value": "生产部"}],
        **validation_patch,
    }
    mapping = SimpleNamespace(
        proposed_mapping_json={
            "org_column_id": 72,
            "org_value_kind": "external",
            "org_value_mapping": {
                "bindings": [{
                    "source_type": "string",
                    "source_value": "生产部",
                    "target_kind": "org_unit",
                    "org_unit_id": 22,
                }],
            },
        },
        validation_json=validation,
    )

    assert service._row_scope_suggestions_from_mapping(mapping) == {}


def test_row_scope_suggestion_is_embedded_only_in_matching_department():
    service = _service()

    def target(target_type: str, target_id: str, selected: bool):
        candidate = {
            "table_id": 6,
            "selected": selected,
            "decision": "visible" if selected else "hidden",
            "hidden_column_ids": [],
            "hidden_metric_ids": [],
            "row_scope": {"type": "all"},
            "confidence": 1.0,
        }
        return SimpleNamespace(
            target_type=target_type,
            target_id=target_id,
            target_label=target_id,
            candidates_json=[candidate],
            definition_json={
                "tables": [service._rule_from_candidate(candidate)],
            },
            included=True,
            status="proposed",
            edited_by=None,
        )

    production = target("org_unit", "22", True)
    finance = target("org_unit", "23", True)
    mapping = SimpleNamespace(
        id=3,
        table_id=6,
        confidence=0.95,
        proposed_mapping_json={
            "org_column_id": 72,
            "org_value_kind": "external",
            "org_value_mapping": {
                "bindings": [{
                    "source_type": "string",
                    "source_value": "生产部",
                    "target_kind": "org_unit",
                    "org_unit_id": 22,
                }],
            },
        },
        validation_json={
            "blockers": [],
            "unresolved_values": [],
            "null_rows": 0,
            "empty_rows": 0,
            "source_values": [{"source_type": "string", "source_value": "生产部"}],
            "grant_snapshot": {
                "baseline_visible": False,
                "explicit_org_unit_ids": [22, 23],
                "scoped_org_unit_ids": [22, 23],
            },
        },
    )

    assert service._embed_mapping_row_scope_suggestion(
        [production, finance],
        mapping,
        actor_id="admin",
    ) is True
    production_rule = production.definition_json["tables"][0]
    assert production_rule["row_scope"] == {
        "type": "custom",
        "condition": {
            "column_id": 72,
            "operator": "=",
            "value": "生产部",
        },
    }
    assert production.candidates_json[0]["row_scope_suggestion"] is True
    assert production.candidates_json[0]["confidence"] == 0.95
    assert finance.definition_json["tables"][0]["row_scope"] == {"type": "all"}
    assert "row_scope_suggestion" not in finance.candidates_json[0]


@pytest.mark.asyncio
async def test_external_organization_condition_includes_owned_and_unowned_rows():
    class FakeSession:
        async def get(self, _model, _column_id):
            return SimpleNamespace(data_type="varchar(64)")

    mapping = SimpleNamespace(
        org_column_id=101,
        org_value_kind="external",
        org_value_mapping_json={
            "bindings": [
                {
                    "source_type": "string",
                    "source_value": "Sales",
                    "target_kind": "org_unit",
                    "org_unit_id": 7,
                },
                {
                    "source_type": "string",
                    "source_value": "Sales child",
                    "target_kind": "org_unit",
                    "org_unit_id": 8,
                },
                {
                    "source_type": "string",
                    "source_value": "Legacy unassigned",
                    "target_kind": "unowned",
                    "manual_unowned": True,
                },
            ],
        },
    )

    condition = await _build_organization_condition(
        FakeSession(),
        "workspace",
        mapping,
        [7, 8],
        {"type": "target_org_tree", "unowned_access": "table_grantees"},
    )

    assert condition["op"] == "OR"
    assert condition["_organization_scope"]["unowned_access"] == "table_grantees"
    assert condition["rules"] == [
        {"column_id": 101, "operator": "in", "value": ["Sales", "Sales child"]},
        {"column_id": 101, "operator": "is null"},
        {"column_id": 101, "operator": "=", "value": ""},
        {"column_id": 101, "operator": "in", "value": ["Legacy unassigned"]},
    ]


@pytest.mark.asyncio
async def test_integer_organization_condition_does_not_treat_zero_as_unowned():
    class FakeSession:
        async def get(self, _model, _column_id):
            return SimpleNamespace(data_type="bigint")

    mapping = SimpleNamespace(
        org_column_id=101,
        org_value_kind="id",
        org_value_mapping_json={},
    )

    condition = await _build_organization_condition(
        FakeSession(),
        "workspace",
        mapping,
        [7, 8],
        {"type": "target_org_tree", "unowned_access": "table_grantees"},
    )

    assert condition["rules"] == [
        {"column_id": 101, "operator": "in", "value": [7, 8]},
        {"column_id": 101, "operator": "is null"},
    ]


@pytest.mark.asyncio
async def test_accepting_mapping_replaces_baseline_with_department_scoped_rules():
    service = _service()

    def target(target_type: str, target_id: str, selected: bool):
        candidate = {
            "table_id": 11,
            "selected": selected,
            "decision": "visible" if selected else "hidden",
            "hidden_column_ids": [],
            "hidden_metric_ids": [],
            "row_scope": {"type": "all"},
        }
        return SimpleNamespace(
            target_type=target_type,
            target_id=target_id,
            target_label=target_id,
            candidates_json=[candidate],
            definition_json={
                "tables": [service._rule_from_candidate(candidate)] if selected else [],
            },
            included=selected,
            status="proposed",
            edited_by=None,
        )

    targets = [
        target("baseline", "*", True),
        target("org_unit", "7", False),
        target("org_unit", "8", False),
    ]

    class FakeResult:
        def scalars(self):
            return targets

    class FakeSession:
        async def execute(self, _query):
            return FakeResult()

    mapping = SimpleNamespace(
        table_id=11,
        validation_json={
            "grant_snapshot": {
                "baseline_visible": True,
                "explicit_org_unit_ids": [],
                "scoped_org_unit_ids": [7, 8],
            },
        },
    )

    await service._apply_mapping_scope_decision(
        FakeSession(),
        SimpleNamespace(id=1),
        mapping,
        accepted=True,
        actor_id="admin",
    )

    assert targets[0].definition_json["tables"] == []
    for department_target in targets[1:]:
        assert department_target.definition_json["tables"][0]["row_scope"] == {
            "type": "target_org_tree",
            "unowned_access": "table_grantees",
        }


@pytest.mark.asyncio
async def test_rejecting_mapping_preserves_original_full_table_grants():
    service = _service()
    candidate = {
        "table_id": 11,
        "selected": False,
        "decision": "hidden",
        "hidden_column_ids": [],
        "hidden_metric_ids": [],
        "row_scope": {"type": "target_org_tree", "unowned_access": "table_grantees"},
    }
    baseline = SimpleNamespace(
        target_type="baseline",
        target_id="*",
        target_label="baseline",
        candidates_json=[candidate.copy()],
        definition_json={"tables": []},
        included=False,
        status="edited",
        edited_by=None,
    )
    department = SimpleNamespace(
        target_type="org_unit",
        target_id="7",
        target_label="Sales",
        candidates_json=[candidate.copy()],
        definition_json={"tables": []},
        included=False,
        status="edited",
        edited_by=None,
    )
    targets = [baseline, department]

    class FakeResult:
        def scalars(self):
            return targets

    class FakeSession:
        async def execute(self, _query):
            return FakeResult()

    mapping = SimpleNamespace(
        table_id=11,
        validation_json={
            "grant_snapshot": {
                "baseline_visible": True,
                "explicit_org_unit_ids": [],
                "scoped_org_unit_ids": [7],
            },
        },
    )

    await service._apply_mapping_scope_decision(
        FakeSession(),
        SimpleNamespace(id=1),
        mapping,
        accepted=False,
        actor_id="admin",
    )

    assert baseline.definition_json["tables"][0]["row_scope"] == {"type": "all"}
    assert department.definition_json["tables"] == []


def test_existing_direct_draft_removes_only_invalid_hidden_row_scopes():
    service = _service()

    normalized = service._normalize_draft_definition({
        "name": "生产部",
        "tables": [
            {"table_id": 10, "decision": "hidden", "row_scope": {"type": "all"}},
            {"table_id": 11, "decision": "visible", "row_scope": {"type": "target_org"}},
        ],
    })

    assert "row_scope" not in normalized["tables"][0]
    assert normalized["tables"][1]["row_scope"] == {"type": "target_org"}


def test_evidence_projection_is_fail_closed_and_supports_v1_rows():
    assert _evidence_baseline_access(SimpleNamespace(
        baseline_access="workspace_visible",
        access_class="restricted",
    )) == "workspace_visible"
    assert _evidence_baseline_access(SimpleNamespace(
        baseline_access=None,
        access_class="restricted",
    )) == "controlled"
    assert _evidence_table_access(SimpleNamespace(
        access_level="partial",
        access_decision="hidden",
        row_scope_json={"type": "all"},
    )) == "partial"
    assert _evidence_table_access(SimpleNamespace(
        access_level=None,
        access_decision="visible",
        row_scope_json={"type": "target_org"},
    )) == "partial"
    assert _evidence_field_access(SimpleNamespace(
        field_decision=None,
        access_decision="visible",
    )) == "visible"


@pytest.mark.asyncio
async def test_organization_binding_compiles_with_universal_legacy_subject():
    class FakeScalars:
        def all(self):
            return []

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        async def execute(self, _query):
            return FakeResult()

    binding = SimpleNamespace(
        target_type="org_unit",
        target_id="7",
        workspace_id="workspace",
        datasource_id=1,
    )

    prepared, blockers = await _prepare_definition_v3(
        FakeSession(), binding, {"name": "销售部", "tables": []}, actor_id=None,
    )

    assert prepared["subject"] == {"type": "all", "id": "*", "label": "销售部"}
    assert blockers == []
