import pytest
from pydantic import ValidationError

from app.api.config.semantic import (
    SemanticAccessNaturalLanguageRequest,
    SemanticAccessPolicyDraftRequest,
    _semantic_access_asset_payload,
)


def test_structured_draft_accepts_subject_table_and_row_scope():
    request = SemanticAccessPolicyDraftRequest(
        subject={"type": "user", "id": "test1"},
        tables=[
            {
                "table_id": 1,
                "decision": "visible",
                "hidden_column_ids": [2],
                "hidden_metric_ids": [],
                "row_scope": {"type": "department", "column_id": 3},
            }
        ],
    )

    payload = request.model_dump(exclude_none=True)
    assert payload["subject"] == {"type": "user", "id": "test1"}
    assert payload["tables"][0]["row_scope"]["include_descendants"] is True


def test_v1_effect_dsl_is_rejected_by_the_public_contract():
    with pytest.raises(ValidationError):
        SemanticAccessPolicyDraftRequest(
            subject={"type": "role", "id": "7"},
            tables=[],
            subject_json=[{"type": "role", "id": "7"}],
            constraint_json=[{"effect": "table_allow"}],
        )


def test_access_asset_payload_only_keeps_enabled_current_assets():
    payload = _semantic_access_asset_payload({
        "datasource": {"id": 7, "name": "orders"},
        "tables": [
            {"id": 1, "status": "confirmed", "is_queryable": True, "sync_state": "current"},
            {"id": 2, "status": "disabled", "is_queryable": True, "sync_state": "current"},
        ],
        "columns": [
            {"id": 11, "table_id": 1, "status": "confirmed", "is_queryable": True, "sync_state": "current"},
            {"id": 12, "table_id": 2, "status": "confirmed", "is_queryable": True, "sync_state": "current"},
            {"id": 13, "table_id": 1, "status": "confirmed", "is_queryable": False, "sync_state": "current"},
        ],
        "metrics": [
            {"id": 21, "table_id": 1, "status": "confirmed", "is_queryable": True, "sync_state": "current"},
            {"id": 22, "table_id": 1, "status": "confirmed", "is_queryable": True, "sync_state": "stale"},
        ],
        "relationships": [{"id": 31}],
        "business_suggestions": [{"id": 41}],
        "recent_runs": [{"id": 51}],
    })

    assert [row["id"] for row in payload["tables"]] == [1]
    assert [row["id"] for row in payload["columns"]] == [11]
    assert [row["id"] for row in payload["metrics"]] == [21]
    assert payload["relationships"] == []
    assert payload["business_suggestions"] == []
    assert payload["recent_runs"] == []


def test_natural_language_request_contains_no_activation_flag():
    with pytest.raises(ValidationError):
        SemanticAccessNaturalLanguageRequest(
            source_text="让 test1 查看订单表",
            activate=True,
        )
