"""Dependency-free primitives for evidence scoring and result comparison."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable


SCORE_VERSION = "evidence-v1"
SOURCE_RELIABILITY = {
    "human_confirmation": 1.00,
    "explicit_fk": 0.95,
    "deterministic_validation": 0.95,
    "golden_sql": 0.90,
    "shadow_equivalent": 0.85,
    "evaluation_equivalent": 0.85,
    "aggregate_profile": 0.75,
    "runtime_pattern": 0.70,
    "naming_rule": 0.55,
    "llm_proposal": 0.40,
}


def stable_fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def score_evidence(evidence: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Combine strongest independent evidence per source and subtract conflicts."""

    strongest: dict[tuple[str, str], float] = {}
    normalized: list[dict[str, Any]] = []
    for item in evidence:
        source_type = str(item.get("source_type") or "unknown")
        direction = "conflict" if item.get("direction") == "conflict" else "support"
        reliability = max(0.0, min(1.0, float(item.get("reliability", SOURCE_RELIABILITY.get(source_type, 0.5)))))
        strength = max(0.0, min(1.0, float(item.get("strength", 1.0))))
        contribution = reliability * strength
        key = (direction, source_type)
        strongest[key] = max(strongest.get(key, 0.0), contribution)
        normalized.append({**item, "source_type": source_type, "direction": direction, "reliability": reliability, "strength": strength})

    support_product = math.prod(1.0 - value for (direction, _), value in strongest.items() if direction == "support")
    conflict_product = math.prod(1.0 - value for (direction, _), value in strongest.items() if direction == "conflict")
    support_score = 1.0 - support_product
    conflict_score = 1.0 - conflict_product
    score = max(0.0, min(1.0, support_score * (1.0 - conflict_score)))
    source_types = sorted({source for (direction, source) in strongest if direction == "support"})
    return {
        "score": round(score, 6),
        "support_score": round(support_score, 6),
        "conflict_score": round(conflict_score, 6),
        "source_types": source_types,
        "source_count": len(source_types),
        "supporting": [item for item in normalized if item["direction"] == "support"],
        "conflicting": [item for item in normalized if item["direction"] == "conflict"],
        "score_version": SCORE_VERSION,
    }


def auto_eligible(
    score: float,
    source_types: Iterable[str],
    *,
    has_conflict: bool,
    deterministic_check_passed: bool,
    threshold: float = 0.98,
    minimum_sources: int = 2,
) -> bool:
    sources = set(source_types)
    return bool(
        score >= threshold
        and len(sources) >= minimum_sources
        and not has_conflict
        and deterministic_check_passed
        and sources != {"llm_proposal"}
    )


def _json_scalar(value: Any) -> Any:
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        number = Decimal(str(value))
        return format(number.normalize(), "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def _canonical_row(row: Any, columns: list[str]) -> list[Any]:
    if isinstance(row, dict):
        normalized_row = {str(key).strip().lower(): value for key, value in row.items()}
        values = [normalized_row.get(column) for column in columns]
    else:
        values = list(row) if isinstance(row, (list, tuple)) else [row]
    normalized = []
    for value in values:
        value = _json_scalar(value)
        normalized.append(value)
    return normalized


def result_signature(payload: dict[str, Any], max_rows: int = 1000) -> dict[str, Any]:
    columns = [str(item).strip().lower() for item in (payload.get("columns") or [])]
    rows = payload.get("data") or payload.get("result_preview") or []
    canonical_rows = [_canonical_row(row, columns) for row in rows[:max_rows]]
    canonical_rows.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
    return {
        "success": bool(payload.get("success")),
        "error_type": payload.get("error_type"),
        "columns": columns,
        "row_count": int(payload.get("row_count") or len(rows)),
        "rows_hashed": len(canonical_rows),
        "row_hash": stable_fingerprint(canonical_rows),
        "truncated": len(rows) > max_rows or int(payload.get("row_count") or 0) > len(rows),
    }


def compare_execution_results(semantic: dict[str, Any], legacy: dict[str, Any]) -> dict[str, Any]:
    semantic_sig = result_signature(semantic)
    legacy_sig = result_signature(legacy)
    semantic_ok = semantic_sig["success"]
    legacy_ok = legacy_sig["success"]
    if not semantic_ok and not legacy_ok:
        verdict = "both_failed"
    elif semantic_ok and not legacy_ok:
        verdict = "semantic_better"
    elif legacy_ok and not semantic_ok:
        verdict = "legacy_better"
    elif semantic_sig["columns"] != legacy_sig["columns"]:
        verdict = "incomparable"
    elif semantic_sig["row_count"] != legacy_sig["row_count"]:
        verdict = "incomparable"
    elif semantic_sig["truncated"] or legacy_sig["truncated"]:
        verdict = "incomparable"
    elif semantic_sig["row_hash"] == legacy_sig["row_hash"]:
        verdict = "equivalent"
    else:
        verdict = "incomparable"
    return {"verdict": verdict, "semantic": semantic_sig, "legacy": legacy_sig}
