import json
from types import SimpleNamespace

import pytest

from app.services.organization_semantic_service import (
    OrganizationSemanticError,
    OrganizationSemanticService,
    business_context_quality,
    normalize_business_context,
    restrict_profile_collaborations,
    stable_fingerprint,
)
from app.services.permission_evidence_service import (
    PermissionEvidenceError,
    PermissionEvidenceService,
    access_level_from_legacy,
    baseline_access_from_legacy,
    filter_enabled_evidence_items,
    generated_grant_requires_override_reason,
    grant_is_compilable,
    legacy_access_class,
    requires_manual_review,
    resolve_matrix_decision,
    sensitive_field_review_requires_reason,
)
from app.services.semantic_policy_binding_service import _definition_expands_access


def _evidence_service() -> PermissionEvidenceService:
    return object.__new__(PermissionEvidenceService)


def _profile_service() -> OrganizationSemanticService:
    return object.__new__(OrganizationSemanticService)


def test_stable_fingerprint_is_key_order_independent():
    assert stable_fingerprint({"b": 2, "a": [1]}) == stable_fingerprint({"a": [1], "b": 2})


def test_profile_content_requires_all_structured_sections():
    service = _profile_service()
    with pytest.raises(OrganizationSemanticError) as exc:
        service._validate_profile_content({"positioning": "生产履约"})
    assert exc.value.code == "invalid_profile"


def test_profile_content_normalizes_whitespace():
    service = _profile_service()
    content = {
        "positioning": " 生产履约 ",
        "responsibilities": [" 排产 ", ""],
        "business_objects": [],
        "data_produced": [],
        "data_consumed": [],
        "collaborations": [],
        "boundaries": [],
        "keywords": [" 生产 "],
    }
    normalized = service._validate_profile_content(content)
    assert normalized["positioning"] == "生产履约"
    assert normalized["responsibilities"] == ["排产"]
    assert normalized["keywords"] == ["生产"]
    assert normalized["known_facts"] == []
    assert normalized["assumptions"] == []
    assert normalized["missing_information"] == []
    assert normalized["permission_impacts"] == []


def test_profile_insight_sections_accept_single_llm_string():
    service = _profile_service()
    content = {
        "positioning": "生产履约",
        "responsibilities": [],
        "business_objects": [],
        "data_produced": [],
        "data_consumed": [],
        "collaborations": [],
        "boundaries": [],
        "keywords": [],
        "permission_impacts": "无法确定设备维保数据是否归属本部门",
    }
    normalized = service._validate_profile_content(content)
    assert normalized["permission_impacts"] == ["无法确定设备维保数据是否归属本部门"]

    content["permission_impacts"] = {
        "影响范围": "设备维保表",
        "审核建议": "确认责任部门后再放开",
    }
    normalized = service._validate_profile_content(content)
    assert normalized["permission_impacts"] == [
        "影响范围：设备维保表；审核建议：确认责任部门后再放开"
    ]


def test_profile_collaborations_only_keep_real_peer_top_departments():
    content = {
        "collaborations": [
            "销售部（接收订单）",
            "采购部（获取原料）",
            "财务部：成本核算",
            "销售部（接收订单）",
        ],
    }
    restricted = restrict_profile_collaborations(
        content,
        {"销售部", "财务部"},
    )
    assert restricted["collaborations"] == [
        "销售部（接收订单）",
        "财务部：成本核算",
    ]
    assert content["collaborations"][1] == "采购部（获取原料）"


def test_profile_validation_restricts_collaborations_when_peers_are_known():
    service = _profile_service()
    content = {
        "positioning": "生产履约",
        "responsibilities": [],
        "business_objects": [],
        "data_produced": [],
        "data_consumed": [],
        "collaborations": ["销售部", "未创建部门"],
        "boundaries": [],
        "keywords": [],
    }
    normalized = service._validate_profile_content(
        content,
        allowed_collaboration_names={"销售部", "财务部"},
    )
    assert normalized["collaborations"] == ["销售部"]


def test_business_context_is_optional_and_normalized():
    normalized = normalize_business_context({
        "content": "  ",
        "industry": " 制造业 ",
        "core_offerings": [" 设备生产 ", "设备生产", ""],
        "business_objects": ["订单"],
    })
    assert normalized["content"] == ""
    assert normalized["industry"] == "制造业"
    assert normalized["core_offerings"] == ["设备生产"]
    assert normalize_business_context({})["industry"] == ""


def test_business_context_quality_tracks_recommended_facts_without_blocking():
    assert business_context_quality({})["level"] == "missing"
    partial = business_context_quality({"industry": "制造业"})
    assert partial["level"] == "partial"
    assert partial["required_completed"] == 1
    sufficient = business_context_quality({
        "industry": "制造业",
        "core_offerings": ["设备"],
        "business_objects": ["订单"],
    })
    assert sufficient["level"] == "sufficient"
    assert sufficient["completeness_percent"] == 100


def test_target_and_table_confirmation_include_high_risk_and_sensitive_grants():
    service = _evidence_service()
    risky_table = SimpleNamespace(
        requires_individual_review=True,
        is_sensitive=False,
        access_class="restricted",
    )
    partial_relation = SimpleNamespace(
        access_level="partial",
        business_role="required_consumer",
        relation_role="required_consumer",
        row_scope_json={"type": "target_org_tree"},
    )

    assert service._table_relation_is_confirmable(
        "table", risky_table, partial_relation,
    )
    assert service._table_relation_is_confirmable(
        "target", risky_table, partial_relation,
    )
    assert service._field_relation_is_confirmable(
        "table", risky_table, sensitive=True,
    )
    assert service._field_relation_is_confirmable(
        "target", risky_table, sensitive=True,
    )


def test_legacy_ordinary_confirmation_still_excludes_risk_and_sensitive_grants():
    service = _evidence_service()
    risky_table = SimpleNamespace(
        requires_individual_review=True,
        is_sensitive=False,
        access_class="restricted",
    )
    ordinary_table = SimpleNamespace(
        requires_individual_review=False,
        is_sensitive=False,
        access_class="department_scoped",
    )
    partial_relation = SimpleNamespace(
        access_level="partial",
        business_role="required_consumer",
        relation_role="required_consumer",
        row_scope_json={"type": "target_org_tree"},
    )
    visible_relation = SimpleNamespace(
        access_level="visible",
        business_role="required_consumer",
        relation_role="required_consumer",
        row_scope_json={"type": "all"},
    )

    assert not service._table_relation_is_confirmable(
        "ordinary_target", risky_table, visible_relation,
    )
    assert not service._table_relation_is_confirmable(
        "ordinary_target", ordinary_table, partial_relation,
    )
    assert not service._field_relation_is_confirmable(
        "ordinary_target", ordinary_table, sensitive=True,
    )
    assert service._table_relation_is_confirmable(
        "ordinary_target", ordinary_table, visible_relation,
    )
    assert service._field_relation_is_confirmable(
        "ordinary_target", ordinary_table, sensitive=False,
    )


def test_generation_rejects_unknown_or_missing_ids():
    service = _evidence_service()
    snapshot = {
        "tables": [SimpleNamespace(id=10)],
        "columns": [SimpleNamespace(id=100, table_id=10)],
        "departments": [SimpleNamespace(id=1), SimpleNamespace(id=2)],
    }
    result = {
        "tables": [{
            "table_id": 10,
            "access_class": "department_scoped",
            "reason": "履职使用",
            "department_relations": [{
                "org_unit_id": 1,
                "relation_role": "owner",
                "reason": "负责",
                "confidence": 0.8,
                "row_scope": {"type": "all"},
            }],
            "field_exceptions": [],
        }],
    }
    with pytest.raises(ValueError, match="未覆盖全部一级部门"):
        service._validate_generation(result, snapshot)

    result["tables"][0]["department_relations"].append({
        "org_unit_id": 999,
        "relation_role": "none",
        "reason": "",
        "confidence": 0.5,
        "row_scope": {"type": "all"},
    })
    with pytest.raises(ValueError, match="非法或重复部门"):
        service._validate_generation(result, snapshot)


def test_generation_requires_fixed_vocabulary_and_all_tables():
    service = _evidence_service()
    snapshot = {
        "tables": [SimpleNamespace(id=10), SimpleNamespace(id=11)],
        "columns": [],
        "departments": [SimpleNamespace(id=1)],
    }
    result = {
        "tables": [{
            "table_id": 10,
            "access_class": "public",
            "reason": "",
            "department_relations": [],
            "field_exceptions": [],
        }],
    }
    with pytest.raises(ValueError, match="基线访问无效"):
        service._validate_generation(result, snapshot)


def test_generation_uses_table_and_field_evidence_without_row_scope():
    service = _evidence_service()
    snapshot = {
        "tables": [SimpleNamespace(id=10)],
        "columns": [SimpleNamespace(id=100, table_id=10)],
        "departments": [SimpleNamespace(id=1), SimpleNamespace(id=2)],
    }
    result = {
        "tables": [{
            "table_id": 10,
            "access_class": "department_scoped",
            "reason": "按部门职责使用",
            "department_relations": [
                {
                    "org_unit_id": 1,
                    "relation_role": "owner",
                    "reason": "负责该业务对象",
                    "confidence": 0.9,
                    "row_scope": {"type": "model_invented_scope"},
                },
                {
                    "org_unit_id": 2,
                    "relation_role": "none",
                    "reason": "无明确业务需要",
                    "confidence": 0.8,
                },
            ],
            "field_exceptions": [{
                "column_id": 100,
                "org_unit_id": 2,
                "access_decision": "model_invented_decision",
                "relation_role": "model_invented_role",
                "reason": "字段不属于该部门",
                "confidence": 0.9,
            }],
        }],
    }

    validated = service._validate_generation(result, snapshot)

    assert [relation["row_scope"] for relation in validated[0]["department_relations"]] == [
        {"type": "all"},
        {"type": "all"},
    ]
    assert validated[0]["field_exceptions"][0]["column_id"] == 100
    assert validated[0]["field_exceptions"][0]["field_decision"] == "hidden"
    assert validated[0]["field_exceptions"][0]["business_role"] == "none"


def test_generation_rejects_missing_table_relation_confidence():
    service = _evidence_service()
    snapshot = {
        "tables": [SimpleNamespace(id=10)],
        "columns": [],
        "departments": [SimpleNamespace(id=1)],
    }
    result = {
        "tables": [{
            "table_id": 10,
            "baseline_access": "controlled",
            "reason": "按部门职责使用",
            "department_relations": [{
                "org_unit_id": 1,
                "business_role": "owner",
                "reason": "负责该业务对象",
            }],
            "field_exceptions": [],
        }],
    }

    with pytest.raises(ValueError, match="部门关系置信度"):
        service._validate_generation(result, snapshot)


def test_generation_rejects_missing_field_exception_confidence():
    service = _evidence_service()
    snapshot = {
        "tables": [SimpleNamespace(id=10)],
        "columns": [SimpleNamespace(id=100, table_id=10)],
        "departments": [SimpleNamespace(id=1)],
    }
    result = {
        "tables": [{
            "table_id": 10,
            "baseline_access": "controlled",
            "reason": "按部门职责使用",
            "department_relations": [{
                "org_unit_id": 1,
                "business_role": "owner",
                "reason": "负责该业务对象",
                "confidence": 0.9,
            }],
            "field_exceptions": [{
                "column_id": 100,
                "org_unit_id": 1,
                "field_decision": "hidden",
                "business_role": "none",
                "reason": "字段不属于该部门",
            }],
        }],
    }

    with pytest.raises(ValueError, match="字段例外置信度"):
        service._validate_generation(result, snapshot)


@pytest.mark.asyncio
async def test_generation_retries_only_the_batch_with_missing_confidence():
    class FakeLlm:
        def __init__(self):
            self.batch_calls: list[list[int]] = []

        async def generate_json(self, messages, **_kwargs):
            payload = json.loads(messages[1]["content"])
            table_ids = [int(row["table_id"]) for row in payload["tables"]]
            self.batch_calls.append(table_ids)
            omit_confidence = len(self.batch_calls) == 1
            return {
                "tables": [{
                    "table_id": table_id,
                    "baseline_access": "controlled",
                    "reason": "按部门职责使用",
                    "department_relations": [{
                        "org_unit_id": 1,
                        "business_role": "owner",
                        "reason": "负责该业务对象",
                        **({} if omit_confidence else {"confidence": 0.9}),
                    }],
                    "field_exceptions": [],
                } for table_id in table_ids],
            }

    service = _evidence_service()
    service.llm = FakeLlm()
    service.settings = SimpleNamespace(
        llm=SimpleNamespace(fast_model="fast", model="default"),
    )
    tables = [SimpleNamespace(id=table_id) for table_id in range(1, 10)]
    snapshot = {
        "tables": tables,
        "columns": [],
        "departments": [SimpleNamespace(id=1)],
        "payload": {
            "tables": [{"table_id": table_id} for table_id in range(1, 10)],
        },
    }

    result = await service._generate_evidence_drafts(snapshot)

    assert service.llm.batch_calls == [
        list(range(1, 9)),
        list(range(1, 9)),
        [9],
    ]
    assert [row["table_id"] for row in result] == list(range(1, 10))


def test_generation_disallows_conditional_consumer_without_row_evidence():
    service = _evidence_service()
    snapshot = {
        "tables": [SimpleNamespace(id=10)],
        "columns": [],
        "departments": [SimpleNamespace(id=1)],
    }
    result = {
        "tables": [{
            "table_id": 10,
            "access_class": "department_scoped",
            "reason": "履职使用",
            "department_relations": [{
                "org_unit_id": 1,
                "relation_role": "conditional_consumer",
                "reason": "需要行级条件",
                "confidence": 0.8,
            }],
            "field_exceptions": [],
        }],
    }

    with pytest.raises(ValueError, match="部门关系无效"):
        service._validate_generation(result, snapshot)


def test_field_exception_assets_are_unique_across_departments():
    service = _evidence_service()
    validated = [{
        "table_id": 10,
        "access_class": "restricted",
        "field_exceptions": [
            {"column_id": 100, "org_unit_id": 1},
            {"column_id": 100, "org_unit_id": 2},
            {"column_id": 101, "org_unit_id": 2},
        ],
    }]

    assets = service._unique_field_exception_assets(validated)

    assert [exception["column_id"] for _, exception in assets] == [100, 101]
    assert len(validated[0]["field_exceptions"]) == 3


def test_row_scope_validation_is_fail_closed():
    service = _evidence_service()
    with pytest.raises(PermissionEvidenceError) as exc:
        service._validate_row_scope({"type": "free_text"})
    assert exc.value.code == "invalid_row_scope"


def test_scope_rank_detects_row_scope_expansion():
    service = _evidence_service()
    assert service._scope_rank({"type": "self"}) < service._scope_rank({"type": "target_org"})
    assert service._scope_rank({"type": "target_org"}) < service._scope_rank({"type": "all"})


def test_v2_legacy_mapping_is_fail_closed_and_round_trippable():
    assert baseline_access_from_legacy("workspace_public") == "workspace_visible"
    assert baseline_access_from_legacy("restricted") == "controlled"
    assert legacy_access_class("workspace_visible", False) == "workspace_public"
    assert legacy_access_class("controlled", True) == "restricted"
    assert access_level_from_legacy("none", {"type": "all"}) == "hidden"
    assert access_level_from_legacy("owner", {"type": "all"}) == "visible"
    assert access_level_from_legacy("required_consumer", {"type": "target_org"}) == "partial"


def test_v2_only_grants_require_review_and_pending_grants_never_compile():
    assert requires_manual_review(grant_kind="public", decision="workspace_visible")
    assert not requires_manual_review(grant_kind="public", decision="controlled")
    assert requires_manual_review(grant_kind="table", decision="visible")
    assert requires_manual_review(grant_kind="table", decision="partial")
    assert not requires_manual_review(grant_kind="table", decision="hidden")
    assert requires_manual_review(grant_kind="field", decision="visible")
    assert not requires_manual_review(grant_kind="field", decision="hidden")
    assert not grant_is_compilable("pending")
    assert not grant_is_compilable("auto_safe")
    assert grant_is_compilable("accepted")
    assert grant_is_compilable("modified")


def test_direct_change_classifier_allows_tightening_but_detects_expansion():
    previous = {"tables": [{
        "table_id": 10,
        "decision": "visible",
        "hidden_column_ids": [100],
        "hidden_metric_ids": [],
        "row_scope": {"type": "target_org_tree"},
    }]}
    tightened = {"tables": [{
        "table_id": 10,
        "decision": "visible",
        "hidden_column_ids": [100, 101],
        "hidden_metric_ids": [],
        "row_scope": {"type": "target_org"},
    }]}
    expanded = {"tables": [{
        "table_id": 10,
        "decision": "visible",
        "hidden_column_ids": [],
        "hidden_metric_ids": [],
        "row_scope": {"type": "all"},
    }]}
    assert _definition_expands_access(previous, tightened) is False
    assert _definition_expands_access(previous, expanded) is True


def test_matrix_resolution_uses_deny_first_and_distinguishes_partial_access():
    visible = resolve_matrix_decision([
        ("baseline", "*", {
            "table_id": 10,
            "decision": "visible",
            "row_scope": {"type": "all"},
        }),
        ("inherited", "1", {
            "table_id": 10,
            "decision": "visible",
            "row_scope": {"type": "target_org"},
        }),
    ])
    assert visible == ("visible", "baseline", "*", "grant")

    partial = resolve_matrix_decision([
        ("inherited", "1", {
            "table_id": 10,
            "decision": "visible",
            "row_scope": {"type": "target_org"},
        }),
    ])
    assert partial == ("partial", "inherited", "1", "grant")

    hidden = resolve_matrix_decision([
        ("baseline", "*", {"table_id": 10, "decision": "visible"}),
        ("direct", "2", {"table_id": 10, "decision": "hidden"}),
    ])
    assert hidden == ("hidden", "direct", "2", "explicit_hidden")
    assert resolve_matrix_decision([]) == (
        "hidden", "default_deny", None, "default_deny",
    )


def test_accepting_generated_sensitive_field_does_not_require_reason():
    assert sensitive_field_review_requires_reason(
        action="accepted", field_decision="visible", has_reason=False,
    ) is False
    assert sensitive_field_review_requires_reason(
        action="modified", field_decision="visible", has_reason=False,
    ) is True
    assert sensitive_field_review_requires_reason(
        action="modified", field_decision="visible", has_reason=True,
    ) is False


def test_accepting_generated_high_risk_grant_does_not_require_reason():
    assert generated_grant_requires_override_reason(
        expands=False,
        sensitive_grant=True,
        review_status="accepted",
        has_reason=False,
    ) is False
    assert generated_grant_requires_override_reason(
        expands=True,
        sensitive_grant=True,
        review_status="accepted",
        has_reason=False,
    ) is True
    assert generated_grant_requires_override_reason(
        expands=False,
        sensitive_grant=True,
        review_status="modified",
        has_reason=False,
    ) is True


def test_disabled_assets_are_excluded_from_review_and_progress():
    enabled_table = SimpleNamespace(
        asset_type="table", table_id=10, asset_id=10,
    )
    enabled_column = SimpleNamespace(
        asset_type="column", table_id=10, asset_id=100,
    )
    disabled_column = SimpleNamespace(
        asset_type="column", table_id=10, asset_id=101,
    )
    disabled_table = SimpleNamespace(
        asset_type="table", table_id=11, asset_id=11,
    )

    assert filter_enabled_evidence_items(
        [enabled_table, enabled_column, disabled_column, disabled_table],
        {10},
        {100},
    ) == [enabled_table, enabled_column]
