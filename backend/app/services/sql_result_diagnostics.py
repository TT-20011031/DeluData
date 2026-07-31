"""Lightweight result diagnostics shared by semantic and legacy SQL paths."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any, Sequence


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip().lower()
    return text in {"", "none", "null", "undefined", "nan"}


def _numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_dimension_column(name: str) -> bool:
    text = str(name or "").lower()
    return any(
        token in text
        for token in (
            "name",
            "customer",
            "client",
            "goods",
            "product",
            "material",
            "device",
            "dept",
            "workshop",
            "客户",
            "品名",
            "物料",
            "设备",
            "车间",
        )
    )


def analyze_sql_result(
    *,
    question: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any] | Mapping[str, Any]],
    expected_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return structured warnings for common NL2SQL result quality issues."""

    diagnostics: list[dict[str, Any]] = []
    row_count = len(rows)
    if row_count == 0:
        return diagnostics

    column_names = [str(column) for column in columns]

    def row_value(row: Sequence[Any] | Mapping[str, Any], index: int) -> Any:
        if isinstance(row, Mapping):
            return row.get(column_names[index])
        return row[index] if index < len(row) else None

    blank_columns: list[dict[str, Any]] = []
    for index, column in enumerate(column_names):
        values = [row_value(row, index) for row in rows]
        blank_count = sum(1 for value in values if _is_blank(value))
        ratio = blank_count / max(1, len(values))
        if ratio >= 0.4 and blank_count >= 3:
            blank_columns.append(
                {
                    "column": column,
                    "blank_count": blank_count,
                    "blank_ratio": round(ratio, 2),
                }
            )
    if blank_columns:
        diagnostics.append(
            {
                "type": "high_null_ratio",
                "code": "high_null_ratio",
                "severity": "warning",
                "message": "部分字段空值较多，可能存在字段质量或字段映射问题。",
                "details": {"columns": blank_columns[:5]},
            }
        )

    normalized_question = str(question or "").lower()
    expects_topn = any(
        token in normalized_question
        for token in ("top", "最高", "最低", "前", "最多", "最少", "排序", "排名")
    )
    if expected_limit and row_count < expected_limit and expects_topn:
        diagnostics.append(
            {
                "type": "short_result_set",
                "code": "short_result_set",
                "severity": "info",
                "message": f"仅找到 {row_count} 条有效数据，少于期望的 {expected_limit} 条。",
                "details": {"row_count": row_count, "expected_limit": expected_limit},
            }
        )

    if row_count >= 3 and expects_topn:
        dimension_indexes = [
            index for index, column in enumerate(column_names) if _is_dimension_column(column)
        ]
        metric_indexes = []
        for index, column in enumerate(column_names):
            if index in dimension_indexes:
                continue
            numeric_values = [
                _numeric(row_value(row, index))
                for row in rows
            ]
            numeric_values = [value for value in numeric_values if value is not None]
            if len(numeric_values) >= min(3, row_count):
                metric_indexes.append(index)

        for metric_index in metric_indexes:
            values = [
                row_value(row, metric_index)
                for row in rows[: min(10, row_count)]
            ]
            counts = Counter(str(value) for value in values if not _is_blank(value))
            repeated_value, repeated_count = counts.most_common(1)[0] if counts else ("", 0)
            if repeated_count >= 3 and len(counts) <= 2:
                details: dict[str, Any] = {
                    "metric_column": column_names[metric_index],
                    "repeated_value": repeated_value,
                    "repeated_count": repeated_count,
                }
                if dimension_indexes:
                    details["dimension_columns"] = [column_names[i] for i in dimension_indexes[:3]]
                diagnostics.append(
                    {
                        "type": "repeated_metric_values",
                        "code": "repeated_metric_values",
                        "severity": "warning",
                        "message": "多个维度行的指标值高度重复，建议检查 Join 条件、Join fanout 或聚合口径。",
                        "details": details,
                    }
                )
                break

    return diagnostics


def append_diagnostics_text(result_text: str, diagnostics: Sequence[dict[str, Any]]) -> str:
    if not diagnostics:
        return result_text
    lines = [result_text.rstrip(), "", "数据质量提示:"]
    for item in diagnostics:
        lines.append(f"- {item.get('message') or item.get('code')}")
    return "\n".join(lines) + "\n"

