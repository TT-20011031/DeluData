from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.semantic_evaluation_service import EVALUATION_CASES, _semantic_verdict
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

