from __future__ import annotations

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

from app.services.semantic_auto_governance_service import SemanticAutoGovernanceService
from app.services.semantic_evidence import (
    auto_eligible,
    compare_execution_results,
    score_evidence,
)
from app.models.config.semantic import SemanticGovernancePolicyModel


def test_scoring_uses_only_strongest_fact_from_each_source_type():
    result = score_evidence(
        [
            {"source_type": "golden_sql", "reliability": 0.9, "strength": 0.4},
            {"source_type": "golden_sql", "reliability": 0.9, "strength": 1.0},
            {"source_type": "aggregate_profile", "reliability": 0.75, "strength": 1.0},
        ]
    )

    assert result["score"] == 0.975
    assert result["source_count"] == 2


def test_conflicting_evidence_lowers_score_and_blocks_automation():
    result = score_evidence(
        [
            {"source_type": "explicit_fk", "reliability": 0.95},
            {"source_type": "golden_sql", "reliability": 0.90},
            {
                "source_type": "deterministic_validation",
                "direction": "conflict",
                "reliability": 0.95,
            },
        ]
    )

    assert result["conflict_score"] == 0.95
    assert result["score"] < 0.65
    assert not auto_eligible(
        result["score"],
        result["source_types"],
        has_conflict=True,
        deterministic_check_passed=True,
    )


def test_llm_proposal_alone_can_never_auto_apply():
    result = score_evidence([{"source_type": "llm_proposal", "reliability": 1.0}])

    assert not auto_eligible(
        1.0,
        result["source_types"],
        has_conflict=False,
        deterministic_check_passed=True,
        minimum_sources=1,
    )


def test_result_comparison_requires_same_columns_count_and_normalized_rows():
    semantic = {
        "success": True,
        "columns": ["Name", "amount"],
        "row_count": 2,
        "data": [{"Name": "B", "amount": 2.0}, {"Name": "A", "amount": 1}],
    }
    legacy = {
        "success": True,
        "columns": ["name", "amount"],
        "row_count": 2,
        "data": [{"name": "A", "amount": 1.0}, {"name": "B", "amount": 2}],
    }

    assert compare_execution_results(semantic, legacy)["verdict"] == "equivalent"

    legacy["row_count"] = 3
    assert compare_execution_results(semantic, legacy)["verdict"] == "incomparable"


def test_one_success_is_better_not_equivalent():
    success = {"success": True, "columns": ["id"], "row_count": 1, "data": [{"id": 1}]}
    failure = {"success": False, "error_type": "compile_error"}

    assert compare_execution_results(success, failure)["verdict"] == "semantic_better"
    assert compare_execution_results(failure, success)["verdict"] == "legacy_better"
    assert compare_execution_results(failure, failure)["verdict"] == "both_failed"


class _ProfileExecutor:
    def __init__(self, estimated_rows: int):
        self.estimated_rows = estimated_rows
        self.queries: list[str] = []

    def execute_query(self, sql: str, **_kwargs):
        self.queries.append(sql)
        if "information_schema.tables" in sql:
            return SimpleNamespace(error=None, columns=["estimated_rows"], rows=[[self.estimated_rows]])
        return SimpleNamespace(
            error=None,
            columns=[
                "sampled_rows",
                "c0_non_null",
                "c0_distinct",
                "c0_avg_length",
                "c0_email",
                "c0_phone",
            ],
            rows=[[100, 90, 70, 8.5, 80, 0]],
        )


def test_string_profile_never_contains_min_max_or_sample_values():
    executor = _ProfileExecutor(100)
    policy = SimpleNamespace(exact_row_threshold=50000, sample_row_limit=10000, table_timeout_sec=5)
    table = SimpleNamespace(physical_name="customers")
    column = SimpleNamespace(physical_name="email", data_type="varchar(255)")

    result = SemanticAutoGovernanceService()._profile_table_sync(executor, table, [column], policy)

    assert result["status"] == "complete"
    assert result["profiles"][0]["range_json"] == {}
    assert "sample_values" not in result["profiles"][0]
    assert "top_k" not in result["profiles"][0]
    aggregate_query = executor.queries[-1].lower()
    assert "min(" not in aggregate_query
    assert "max(" not in aggregate_query
    assert "group by" not in aggregate_query


def test_large_table_profile_uses_bounded_subquery():
    executor = _ProfileExecutor(50001)
    policy = SimpleNamespace(exact_row_threshold=50000, sample_row_limit=10000, table_timeout_sec=5)
    table = SimpleNamespace(physical_name="events")
    column = SimpleNamespace(physical_name="event_name", data_type="varchar(255)")

    result = SemanticAutoGovernanceService()._profile_table_sync(executor, table, [column], policy)

    assert result["profiles"][0]["sample_method"] == "bounded"
    assert "limit 10000" in executor.queries[-1].lower()


def _candidate(**overrides):
    base = {
        "id": 1,
        "run_id": 1,
        "target_type": "columns",
        "target_id": 10,
        "candidate_type": "business_semantics",
        "title": "业务语义建议",
        "before_json": {},
        "proposed_patch_json": {"business_name": "客户", "description": "客户档案", "synonyms": ["用户"]},
        "applied_patch_json": {},
        "supporting_evidence_json": [{"source_type": "golden_sql", "direction": "support", "claim_type": "golden_sql_reference", "value": {"question": "客户数"}}],
        "conflicting_evidence_json": [],
        "source_types": ["golden_sql"],
        "score": 0.91,
        "score_version": "evidence-v1",
        "risk_level": "medium",
        "status": "needs_review",
        "auto_eligible": False,
        "deterministic_check_passed": False,
        "policy_version": "evidence-v1",
        "decision_reason": None,
        "decided_by": None,
        "decided_at": None,
        "applied_at": None,
        "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 1),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_candidate_payload_includes_admin_friendly_governance_fields():
    payload = SemanticAutoGovernanceService()._candidate_payload(_candidate())

    assert payload["priority_reason"]
    assert payload["evidence_summaries"][0].startswith("支持证据: 黄金 SQL")
    assert payload["editable_fields"] == [
        {"key": "business_name", "label": "业务名", "type": "text", "value": "客户"},
        {"key": "description", "label": "业务说明", "type": "textarea", "value": "客户档案"},
        {"key": "synonyms", "label": "同义词", "type": "tags", "value": ["用户"]},
    ]


def test_candidate_priority_prefers_blocking_and_sensitive_work_before_naming_suggestions():
    service = SemanticAutoGovernanceService()
    naming = _candidate(candidate_type="business_semantics", source_types=["naming_rule"], score=0.99)
    sensitive = _candidate(candidate_type="mark_sensitive", source_types=["aggregate_profile", "deterministic_validation"], score=0.8)
    invalid = _candidate(candidate_type="disable_invalid_asset", target_type="metrics", source_types=["explicit_fk", "deterministic_validation"], score=0.75)

    ordered = sorted([naming, sensitive, invalid], key=service._candidate_priority, reverse=True)

    assert [item.candidate_type for item in ordered] == ["disable_invalid_asset", "mark_sensitive", "business_semantics"]


def test_business_suggestion_sync_failure_is_reported_without_blocking(monkeypatch):
    class FailingSemanticQueryService:
        async def generate_business_suggestions(self, *_args, **_kwargs):
            raise RuntimeError("llm unavailable")

    import app.services.semantic_query_service as semantic_query_service

    monkeypatch.setattr(semantic_query_service, "get_semantic_query_service", lambda: FailingSemanticQueryService())

    result = asyncio.run(SemanticAutoGovernanceService()._sync_business_suggestions({"run": SimpleNamespace(id=7, workspace_id="w1")}))

    assert result["business_suggestion_status"] == "partial"
    assert result["business_suggestions"] == 0
    assert "llm unavailable" in result["business_suggestion_error"]


def test_governance_policy_defaults_are_lightweight_candidate_generation():
    table = SemanticGovernancePolicyModel.__table__

    assert table.c.run_after_scan.default.arg is False
    assert table.c.max_tables_per_run.default.arg == 5
    assert table.c.sample_row_limit.default.arg == 1000
    assert table.c.table_timeout_sec.default.arg == 3
    assert table.c.max_run_seconds.default.arg == 90


def test_profile_connection_check_reports_failure_without_raising():
    class FailingExecutor:
        def execute_query(self, *_args, **_kwargs):
            return SimpleNamespace(error="connect timeout")

    result = SemanticAutoGovernanceService()._check_profile_connection_sync(FailingExecutor(), timeout_sec=3)

    assert result == {"connection_check": "failed", "connection_error": "connect timeout"}


def test_profile_connection_check_skips_when_database_is_not_configured(monkeypatch):
    async def missing_connection(_run):
        return None

    service = SemanticAutoGovernanceService()
    monkeypatch.setattr(service, "_profile_connection_url", missing_connection)

    result = asyncio.run(service._check_profile_connection({
        "run": SimpleNamespace(id=9, workspace_id="w1", triggered_by=None),
        "policy": SimpleNamespace(table_timeout_sec=3),
    }))

    assert result["connection_check"] == "skipped"
    assert "连接" in result["connection_error"]


def test_profile_collection_uses_run_scoped_executor_key(monkeypatch):
    created_keys: list[str] = []

    class FakeExecutor:
        def __init__(self, user_id, _connection_url, connect_timeout_sec=None):
            created_keys.append(user_id)
            assert connect_timeout_sec == 3

    async def fake_connection_url(_run):
        return "mysql+pymysql://readonly:secret@db/app"

    service = SemanticAutoGovernanceService()
    monkeypatch.setattr(service, "_profile_connection_url", fake_connection_url)
    monkeypatch.setattr(
        "app.services.semantic_auto_governance_service.ReadOnlyExecutor",
        FakeExecutor,
    )

    result = asyncio.run(service._collect_profiles_guarded({
        "run": SimpleNamespace(id=9, workspace_id="w1", datasource_id=2, schema_fingerprint="fp"),
        "policy": SimpleNamespace(max_tables_per_run=5, table_timeout_sec=3, max_run_seconds=90),
        "tables": [],
        "columns": [],
    }, time.monotonic()))

    assert created_keys == ["semantic-governance-w1-9-profile"]
    assert result["profiled_tables"] == 0
    assert result["failed_tables"] == 0


def test_run_payload_exposes_worker_status_without_internal_token():
    run = SimpleNamespace(
        id=7,
        status="failed",
        stage="interrupted",
        progress=100,
        trigger_type="manual",
        scan_id=None,
        schema_fingerprint="fp",
        observe_only=True,
        summary_json={"connection_check": "failed"},
        budget_json={"max_tables_per_run": 5},
        error_message="connection timeout",
        worker_id="semantic-governance:test-worker",
        run_token="secret-token",
        attempt=2,
        max_attempts=2,
        lease_expires_at=datetime(2026, 1, 1, 10, 0, 0),
        heartbeat_at=datetime(2026, 1, 1, 9, 59, 0),
        cancel_requested_at=None,
        created_at=datetime(2026, 1, 1, 9, 0, 0),
        started_at=datetime(2026, 1, 1, 9, 1, 0),
        completed_at=datetime(2026, 1, 1, 10, 1, 0),
    )

    payload = SemanticAutoGovernanceService()._run_payload(run)

    assert payload["worker_id"] == "semantic-governance:test-worker"
    assert payload["attempt"] == 2
    assert payload["max_attempts"] == 2
    assert payload["lease_expires_at"] == "2026-01-01T10:00:00"
    assert payload["heartbeat_at"] == "2026-01-01T09:59:00"
    assert payload["retryable"] is True
    assert "run_token" not in payload
