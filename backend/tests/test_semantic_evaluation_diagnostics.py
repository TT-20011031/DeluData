from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.semantic_evaluation_service import (
    EVALUATION_CASES,
    SemanticEvaluationService,
    _contract_diagnostics,
    _semantic_verdict,
)
from app.services.sql_result_diagnostics import analyze_sql_result


def test_semantic_evaluation_cases_are_fixed_and_unique():
    case_ids = [case.case_id for case in EVALUATION_CASES]

    assert len(EVALUATION_CASES) == 15
    assert len(case_ids) == len(set(case_ids))
    assert all(case.question for case in EVALUATION_CASES)
    assert any(case.expected_limit == 10 for case in EVALUATION_CASES)


def test_semantic_verdict_uses_semantic_chain_only():
    assert _semantic_verdict({"success": False, "diagnostics": []}) == "fail"
    assert _semantic_verdict({"success": True, "diagnostics": []}) == "pass"
    assert _semantic_verdict({"success": True, "diagnostics": [{"code": "short_result_set"}]}) == "pass_with_warnings"


def test_sales_regression_cases_have_machine_readable_contracts():
    cases = {case.case_id: case for case in EVALUATION_CASES}

    assert cases["case_03"].expected_query_type == "trend"
    assert cases["case_03"].expected_tables == ["clean_jf_sale_order"]
    assert cases["case_07"].expected_query_type == "compare"
    assert "变化率" in cases["case_07"].required_sql_fragments


def test_successful_sql_using_wrong_table_fails_semantic_contract():
    case = next(item for item in EVALUATION_CASES if item.case_id == "case_03")
    payload = {
        "success": True,
        "semantic_intent": {"query_type": "trend"},
        "referenced_tables": ["clean_make_order_proofing_detail"],
        "sql": "SELECT DATE_FORMAT(make_date, '%Y-%m') FROM clean_make_order_proofing_detail WHERE make_date >= '2025-03-01' AND make_date < '2026-06-01'",
        "diagnostics": [],
    }
    payload["diagnostics"] = _contract_diagnostics(payload, case)

    assert payload["diagnostics"][0]["type"] == "contract_violation"
    assert _semantic_verdict(payload) == "fail"


def test_evaluation_worker_passes_case_question_as_original_query():
    case = next(item for item in EVALUATION_CASES if item.case_id == "case_03")
    observed = {}

    class FakeWorker:
        async def execute_task(self, **kwargs):
            observed.update(kwargs)
            return {"success": False, "error_type": "expected_test_stop"}

    asyncio.run(SemanticEvaluationService()._run_worker(
        FakeWorker(),
        case=case,
        user_id="u1",
        workspace_id="w1",
        run_mode="semantic_only",
    ))

    assert observed["task_description"] == case.question
    assert observed["original_query"] == case.question


def test_result_diagnostics_detect_high_null_ratio():
    diagnostics = analyze_sql_result(
        question="top 10 materials",
        columns=["name", "spec"],
        rows=[
            {"name": "A", "spec": None},
            {"name": "B", "spec": ""},
            {"name": "C", "spec": "undefined"},
            {"name": "D", "spec": None},
        ],
        expected_limit=10,
    )

    assert any(item["type"] == "high_null_ratio" for item in diagnostics)


def test_result_diagnostics_detect_short_topn_result():
    diagnostics = analyze_sql_result(
        question="show top 10 customers",
        columns=["customer", "amount"],
        rows=[{"customer": "A", "amount": 100}, {"customer": "B", "amount": 90}],
        expected_limit=10,
    )

    assert any(item["type"] == "short_result_set" for item in diagnostics)


def test_result_diagnostics_detect_repeated_metric_values():
    diagnostics = analyze_sql_result(
        question="top 10 customers by sales amount",
        columns=["customer", "amount"],
        rows=[
            {"customer": "A", "amount": 100},
            {"customer": "B", "amount": 100},
            {"customer": "C", "amount": 100},
            {"customer": "D", "amount": 100},
        ],
        expected_limit=10,
    )

    assert any(item["type"] == "repeated_metric_values" for item in diagnostics)

