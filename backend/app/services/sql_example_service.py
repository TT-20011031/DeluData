"""Account-private, permission-governed SQL example support.

Simple examples are converted into a semantic intent and compiled again at
query time.  Examples whose query shape cannot be represented losslessly by
the semantic intent model (derived tables, fixed predicates, computed
projections, repeated table roles, and similar constructs) are executed as a
verified SQL template.  Template execution is allowed only after current
schema/object permissions are revalidated and is rejected when any referenced
table requires a row-level filter that cannot be injected losslessly.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select, update

from app.core.db.database import get_async_db_manager
from app.core.db.read_only_executor import ReadOnlyExecutor
from app.models.auth.authorization import AuthorizationRevisionModel
from app.models.config.db_config import (
    get_user_db_config_async,
    get_workspace_admin_db_config_async,
    get_workspace_db_config_async,
)
from app.models.config.sql_example import (
    SqlExample,
    SqlExampleModel,
    SqlExampleValidationData,
)
from app.models.config.sql_example_embeddings import (
    search_sql_examples_by_similarity,
    update_sql_example_embedding,
)
from app.services.semantic_access_policy_service import (
    get_semantic_access_policy_service,
)
from app.services.sql_result_diagnostics import (
    analyze_sql_result,
    append_diagnostics_text,
)

SQL_EXAMPLE_VALIDATION_MODEL_VERSION = 2
_GENERIC_VALUES = {
    "",
    "xx",
    "xxx",
    "某个",
    "某客户",
    "某供应商",
    "客户",
    "客户名称",
    "供应商",
    "供应商名称",
    "示例",
    "待填写",
    "?",
}


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **details}


def _normalize_identifier(value: Any) -> str:
    return str(value or "").strip().strip("`").lower()


def _normalize_question(question: str, parameters: list[dict[str, Any]]) -> str:
    text = str(question or "").strip()
    for parameter in parameters:
        key = str(parameter.get("key") or "parameter")
        value = str(parameter.get("example_value") or "").strip()
        label = str(parameter.get("label") or "").strip()
        if value and value.lower() not in _GENERIC_VALUES:
            text = text.replace(value, f"{{{{{key}}}}}")
        elif value:
            text = re.sub(re.escape(value), f"{{{{{key}}}}}", text, flags=re.IGNORECASE)
        if f"{{{{{key}}}}}" not in text and label:
            text = text.replace(f"某个{label}", f"{{{{{key}}}}}")
            text = text.replace(f"某{label}", f"{{{{{key}}}}}")
    return text or str(question or "").strip()


def _sql_expression(sql: str):
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ValueError("服务器缺少 SQL 解析组件") from exc

    normalized_sql = re.sub(r"\s+", " ", str(sql or "")).strip().lower()
    if re.search(r"\binto\s+(?:out|dump)file\b|\bfor\s+update\b|\block\s+in\s+share\s+mode\b", normalized_sql):
        raise ValueError("SQL 示例不能包含文件写入或加锁语句")

    try:
        expressions = sqlglot.parse(str(sql or ""), read="mysql")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"SQL 解析失败：{exc}") from exc
    # sqlglot represents a trailing ``; -- comment`` as a separate Semicolon
    # expression. It is formatting metadata, not a second SQL statement, so
    # ignore it while still rejecting any real second statement below.
    semicolon_expression = getattr(exp, "Semicolon", None)
    expressions = [
        item
        for item in expressions
        if item is not None
        and not (
            semicolon_expression is not None
            and isinstance(item, semicolon_expression)
        )
    ]
    if len(expressions) != 1:
        raise ValueError("只能填写一条只读 SELECT SQL")
    expression = expressions[0]
    forbidden = tuple(
        item
        for item in (
            getattr(exp, "Insert", None),
            getattr(exp, "Update", None),
            getattr(exp, "Delete", None),
            getattr(exp, "Create", None),
            getattr(exp, "Drop", None),
            getattr(exp, "Alter", None),
            getattr(exp, "Command", None),
            getattr(exp, "Merge", None),
            getattr(exp, "TruncateTable", None),
        )
        if item is not None
    )
    if isinstance(expression, forbidden) or any(expression.find_all(*forbidden)):
        raise ValueError("SQL 示例只允许只读 SELECT，不能包含增删改或结构变更")
    if not expression.find(exp.Select):
        raise ValueError("SQL 示例必须包含 SELECT 查询")
    dangerous_functions = {
        "benchmark",
        "get_lock",
        "is_free_lock",
        "is_used_lock",
        "load_file",
        "master_pos_wait",
        "name_const",
        "release_all_locks",
        "release_lock",
        "sleep",
        "sys_eval",
        "sys_exec",
    }
    for function in expression.find_all(exp.Func):
        function_name = _normalize_identifier(
            getattr(function, "name", "")
            or getattr(function, "sql_name", lambda: "")()
        )
        if function_name in dangerous_functions:
            raise ValueError(f"SQL 示例不能使用危险函数：{function_name}")
    return expression, exp


def _requires_verified_sql_execution(sql: str) -> bool:
    """Return whether semantic-intent compilation would be lossy for ``sql``.

    ``IntentQuery`` deliberately models common analytical shapes, not an
    arbitrary SQL AST.  Treat every construct that currently loses semantics
    as a verified template instead of silently replacing it with a different
    query.
    """

    expression, exp = _sql_expression(sql)
    complex_types = tuple(
        item
        for item in (
            getattr(exp, "Subquery", None),
            getattr(exp, "CTE", None),
            getattr(exp, "Union", None),
            getattr(exp, "Intersect", None),
            getattr(exp, "Except", None),
            getattr(exp, "Window", None),
            getattr(exp, "Having", None),
        )
        if item is not None
    )
    if complex_types and any(expression.find_all(*complex_types)):
        return True

    table_names = [
        _normalize_identifier(table.name)
        for table in expression.find_all(exp.Table)
        if _normalize_identifier(table.name)
    ]
    if len(table_names) != len(set(table_names)):
        return True

    # Static predicates are not currently persisted by ``_intent_from_sql``.
    if expression.find(exp.Where) is not None:
        return True

    select_node = expression.find(exp.Select)
    if select_node is not None:
        for projection in select_node.expressions:
            target = projection.this if isinstance(projection, exp.Alias) else projection
            if not isinstance(target, (exp.Column, exp.Star)):
                return True

    return False


def _bounded_example_sql(expression: Any, max_rows: int) -> str:
    """Render one validated expression with a bounded top-level LIMIT."""

    limit = max(1, min(int(max_rows or 100), 1000))
    current_limit = expression.args.get("limit")
    if current_limit is not None:
        try:
            current_value = int(current_limit.expression.this)
        except (AttributeError, TypeError, ValueError):
            current_value = limit + 1
        if 0 < current_value <= limit:
            return expression.sql(dialect="mysql")
    return expression.limit(limit, copy=True).sql(dialect="mysql")


def _format_verified_result_text(columns: list[str], rows: list[tuple[Any, ...]], row_count: int) -> str:
    if not columns:
        return "查询成功，无返回字段"
    output = " | ".join(str(column) for column in columns) + "\n"
    output += "-" * 50 + "\n"
    for row in rows[:20]:
        output += " | ".join(str(value) for value in row) + "\n"
    if row_count > 20:
        output += f"... 共 {row_count} 行"
    return output


def _catalog_maps(catalog: Any) -> tuple[dict[str, Any], dict[tuple[int, str], Any]]:
    tables: dict[str, Any] = {}
    for table in catalog.tables:
        for name in (table.physical_name, table.business_name, *(table.synonyms or [])):
            normalized = _normalize_identifier(name)
            if normalized:
                tables.setdefault(normalized, table)
    columns: dict[tuple[int, str], Any] = {}
    for column in catalog.columns:
        for name in (column.physical_name, column.business_name, *(column.synonyms or [])):
            normalized = _normalize_identifier(name)
            if normalized:
                columns.setdefault((int(column.table_id), normalized), column)
    return tables, columns


def _catalog_validation_fingerprint(catalog: Any) -> str:
    """Fingerprint both the physical schema and governed semantic model."""

    payload = {
        "physical": str(catalog.datasource.schema_fingerprint or ""),
        "tables": [
            (item.id, item.physical_name, item.business_name, item.status, item.is_queryable, getattr(item, "sync_state", ""))
            for item in catalog.tables
        ],
        "columns": [
            (
                item.id,
                item.table_id,
                item.physical_name,
                item.business_name,
                item.data_type,
                item.status,
                item.is_queryable,
                getattr(item, "sync_state", ""),
            )
            for item in catalog.columns
        ],
        "metrics": [
            (
                item.id,
                item.table_id,
                item.name,
                item.business_name,
                item.formula,
                item.status,
                item.is_queryable,
                getattr(item, "sync_state", ""),
            )
            for item in catalog.metrics
        ],
        "relationships": [
            (
                item.id,
                item.left_table_id,
                item.right_table_id,
                item.left_column_id,
                item.right_column_id,
                item.status,
                item.is_queryable,
                getattr(item, "sync_state", ""),
            )
            for item in catalog.relationships
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_sql_references(
    sql: str,
    catalog: Any,
) -> tuple[Any, list[Any], list[Any], dict[str, Any], list[dict[str, Any]]]:
    expression, exp = _sql_expression(sql)
    table_map, column_map = _catalog_maps(catalog)
    cte_names = {
        _normalize_identifier(cte.alias_or_name)
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
    }
    referenced_tables: list[Any] = []
    aliases: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    for table_ref in expression.find_all(exp.Table):
        physical = _normalize_identifier(table_ref.name)
        if physical in cte_names:
            continue
        database_name = _normalize_identifier(getattr(table_ref, "db", ""))
        expected_database = _normalize_identifier(catalog.datasource.database)
        if database_name and database_name != expected_database:
            errors.append(
                _error(
                    "datasource_mismatch",
                    f"表 {table_ref.sql()} 不属于当前语义数据源 {catalog.datasource.database}",
                    database=database_name,
                )
            )
            continue
        table = table_map.get(physical)
        if not table:
            errors.append(_error("unknown_table", f"语义模型中找不到表：{table_ref.name}", table=table_ref.name))
            continue
        if int(table.id) not in {int(item.id) for item in referenced_tables}:
            referenced_tables.append(table)
        aliases[_normalize_identifier(table_ref.alias_or_name or table_ref.name)] = table
        aliases[physical] = table

    selected_aliases = {
        _normalize_identifier(item.alias)
        for select_node in expression.find_all(exp.Select)
        for item in select_node.expressions
        if getattr(item, "alias", None)
    }
    referenced_columns: list[Any] = []
    for column_ref in expression.find_all(exp.Column):
        name = _normalize_identifier(column_ref.name)
        if not name or (not column_ref.table and name in selected_aliases):
            continue
        qualifier = _normalize_identifier(column_ref.table)
        candidates: list[Any] = []
        if qualifier and qualifier in aliases:
            column = column_map.get((int(aliases[qualifier].id), name))
            if column:
                candidates = [column]
        else:
            candidates = [
                column_map[(int(table.id), name)]
                for table in referenced_tables
                if (int(table.id), name) in column_map
            ]
        unique = {int(item.id): item for item in candidates}
        if len(unique) == 1:
            column = next(iter(unique.values()))
            if int(column.id) not in {int(item.id) for item in referenced_columns}:
                referenced_columns.append(column)
        elif len(unique) > 1:
            errors.append(_error("ambiguous_column", f"字段 {column_ref.name} 未指定表，无法唯一确定", column=column_ref.name))
        else:
            errors.append(_error("unknown_column", f"语义模型中找不到字段：{column_ref.sql()}", column=column_ref.sql()))
    return expression, referenced_tables, referenced_columns, aliases, errors


def _parameter_label(column: Any) -> tuple[str, str]:
    label_source = f"{column.business_name} {column.physical_name}".lower()
    if "供应商" in label_source or "supplier" in label_source or "vendor" in label_source:
        return "supplier_name", "供应商"
    if "客户" in label_source or "customer" in label_source or "client" in label_source:
        return "customer_name", "客户"
    if "月份" in label_source or "month" in label_source:
        return "month", "月份"
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", str(column.physical_name or "parameter")).strip("_")
    return normalized or f"column_{column.id}", str(column.business_name or column.physical_name)


def _detect_parameters(
    expression: Any,
    exp: Any,
    question: str,
    referenced_tables: list[Any],
    aliases: dict[str, Any],
    catalog: Any,
) -> list[dict[str, Any]]:
    _, column_map = _catalog_maps(catalog)
    detected: list[dict[str, Any]] = []
    seen: set[int] = set()
    comparison_types = (exp.EQ, exp.Like, exp.ILike)
    for comparison in expression.find_all(comparison_types):
        left = comparison.this
        right = comparison.expression
        if not isinstance(left, exp.Column):
            continue
        literal = right if isinstance(right, (exp.Literal, exp.Placeholder, exp.Parameter)) else None
        qualifier = _normalize_identifier(left.table)
        name = _normalize_identifier(left.name)
        column = None
        if qualifier and qualifier in aliases:
            column = column_map.get((int(aliases[qualifier].id), name))
        else:
            candidates = [
                column_map[(int(table.id), name)]
                for table in referenced_tables
                if (int(table.id), name) in column_map
            ]
            if len({int(item.id) for item in candidates}) == 1:
                column = candidates[0]
        if not column or int(column.id) in seen:
            continue
        key, label = _parameter_label(column)
        is_business_parameter = key in {"customer_name", "supplier_name", "month"}
        if literal is None and not is_business_parameter:
            continue
        value = (
            str(getattr(literal, "this", "") or literal.sql()).strip("'\"% :@")
            if literal is not None
            else ""
        )
        is_placeholder = (
            value.lower() in _GENERIC_VALUES
            or "{{" in value
            or (literal is not None and ":" in literal.sql())
        )
        if not (is_business_parameter or is_placeholder or (value and value in question)):
            continue
        seen.add(int(column.id))
        detected.append(
            {
                "key": key,
                "label": label,
                "data_type": "date" if key == "month" else "text",
                "table_id": int(column.table_id),
                "column_id": int(column.id),
                "field": str(column.business_name or column.physical_name),
                "required": True,
                "aliases": [label, str(column.business_name), str(column.physical_name)],
                "example_value": value,
                "operator": "eq" if isinstance(comparison, exp.EQ) else "contains",
            }
        )
    if not any(item.get("key") == "month" for item in detected):
        month_markers = ("本月", "当月", "这个月", "上月", "下月", "月份")
        explicit_month = re.search(r"\d{4}\s*年\s*\d{1,2}\s*月|(?<!\d)\d{4}[-/]\d{1,2}(?!\d)", question)
        sql_text = expression.sql(dialect="mysql").lower()
        has_month_semantics = bool(explicit_month) or any(item in question for item in month_markers) or any(
            item in sql_text
            for item in ("curdate(", "current_date", "date_format(", "month(", "extract(month")
        )
        if has_month_semantics:
            time_predicate_types = tuple(
                item
                for item in (
                    getattr(exp, "GT", None),
                    getattr(exp, "GTE", None),
                    getattr(exp, "LT", None),
                    getattr(exp, "LTE", None),
                    getattr(exp, "Between", None),
                )
                if item is not None
            )
            predicate_columns = [
                column_ref
                for predicate in expression.find_all(*time_predicate_types)
                for column_ref in predicate.find_all(exp.Column)
            ]
            for column_ref in [*predicate_columns, *expression.find_all(exp.Column)]:
                qualifier = _normalize_identifier(column_ref.table)
                name = _normalize_identifier(column_ref.name)
                column = None
                if qualifier and qualifier in aliases:
                    column = column_map.get((int(aliases[qualifier].id), name))
                else:
                    candidates = [
                        column_map[(int(table.id), name)]
                        for table in referenced_tables
                        if (int(table.id), name) in column_map
                    ]
                    if len({int(item.id) for item in candidates}) == 1:
                        column = candidates[0]
                if not column:
                    continue
                date_label = f"{column.physical_name} {column.business_name} {column.data_type}".lower()
                if not any(item in date_label for item in ("date", "time", "日期", "时间", "月份", "年月")):
                    continue
                detected.append(
                    {
                        "key": "month",
                        "label": "月份",
                        "data_type": "month",
                        "table_id": int(column.table_id),
                        "column_id": int(column.id),
                        "field": str(column.business_name or column.physical_name),
                        "required": True,
                        "aliases": ["月份", "月", str(column.business_name), str(column.physical_name)],
                        "example_value": (
                            explicit_month.group(0).replace(" ", "")
                            if explicit_month
                            else next((item for item in month_markers if item in question), "本月")
                        ),
                        "operator": "month_range",
                    }
                )
                break
    return detected


def _merge_confirmed_parameters(
    detected: list[dict[str, Any]],
    supplied: Optional[list[dict[str, Any]]],
    catalog: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not supplied:
        return detected, []
    column_by_id = {int(item.id): item for item in catalog.columns}
    detected_by_column = {
        int(item.get("column_id") or 0): item
        for item in detected
        if int(item.get("column_id") or 0)
    }
    output: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    supplied_column_ids: set[int] = set()
    for raw in supplied:
        try:
            column_id = int(raw.get("column_id") or 0)
        except (TypeError, ValueError):
            column_id = 0
        column = column_by_id.get(column_id)
        if not column:
            errors.append(_error("parameter_column_missing", "动态参数没有绑定有效的语义字段"))
            continue
        supplied_column_ids.add(column_id)
        default_key, default_label = _parameter_label(column)
        detected_item = detected_by_column.get(column_id, {})
        output.append(
            {
                "key": str(raw.get("key") or default_key),
                "label": str(raw.get("label") or default_label),
                "data_type": str(raw.get("data_type") or "text"),
                "table_id": int(column.table_id),
                "column_id": int(column.id),
                "field": str(column.business_name or column.physical_name),
                "required": bool(raw.get("required", True)),
                "aliases": [str(item) for item in (raw.get("aliases") or []) if str(item).strip()],
                "example_value": str(raw.get("example_value") or ""),
                "operator": str(raw.get("operator") or detected_item.get("operator") or "eq"),
            }
        )
    # Existing examples may have been saved before a newly supported parameter
    # type (for example an explicit month) could be detected. Preserve the
    # user's confirmed bindings and append only genuinely new SQL parameters.
    for item in detected:
        column_id = int(item.get("column_id") or 0)
        if column_id and column_id not in supplied_column_ids:
            output.append(item)
            supplied_column_ids.add(column_id)
    return output, errors


def _resolve_expression_column(
    node: Any,
    columns: list[Any],
    aliases: dict[str, Any],
) -> Optional[Any]:
    if node is None:
        return None
    name = _normalize_identifier(getattr(node, "name", ""))
    qualifier = _normalize_identifier(getattr(node, "table", ""))
    if not name:
        return None
    candidates = [
        column
        for column in columns
        if _normalize_identifier(column.physical_name) == name
        and (
            not qualifier
            or (qualifier in aliases and int(column.table_id) == int(aliases[qualifier].id))
        )
    ]
    unique = {int(item.id): item for item in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _aggregate_kind(aggregate: Any, exp: Any) -> str:
    if isinstance(aggregate, exp.Sum):
        return "sum"
    if isinstance(aggregate, exp.Avg):
        return "avg"
    if isinstance(aggregate, exp.Min):
        return "min"
    if isinstance(aggregate, exp.Max):
        return "max"
    if isinstance(aggregate, exp.Count):
        distinct_type = getattr(exp, "Distinct", None)
        is_distinct = bool(aggregate.args.get("distinct")) or (
            distinct_type is not None and isinstance(aggregate.this, distinct_type)
        )
        return "count_distinct" if is_distinct else "count"
    return ""


def _metric_matches_aggregate(metric: Any, aggregate_kind: str) -> bool:
    aliases = {
        "countdistinct": "countdistinct",
        "distinctcount": "countdistinct",
        "count": "count",
        "sum": "sum",
        "avg": "avg",
        "average": "avg",
        "min": "min",
        "max": "max",
    }
    actual = _normalize_identifier(getattr(metric, "aggregation", ""))
    expected = _normalize_identifier(aggregate_kind)
    return aliases.get(actual, actual) == aliases.get(expected, expected)


def _intent_from_sql(
    expression: Any,
    exp: Any,
    tables: list[Any],
    columns: list[Any],
    aliases: dict[str, Any],
    catalog: Any,
):
    from app.services.semantic_query_service import IntentQuery

    aggregates = list(expression.find_all(exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max))
    group_columns = [
        item
        for group in expression.find_all(exp.Group)
        for item in group.expressions
        if isinstance(item, exp.Column)
    ]
    dimensions: list[str] = []
    for node in group_columns:
        column = _resolve_expression_column(node, columns, aliases)
        if column:
            dimensions.append(str(column.business_name or column.physical_name))

    if not aggregates:
        select_node = expression.find(exp.Select)
        selected_nodes = [
            node
            for projection in (select_node.expressions if select_node is not None else [])
            for node in projection.find_all(exp.Column)
        ]
        for node in selected_nodes:
            column = _resolve_expression_column(node, columns, aliases)
            if column:
                dimensions.append(str(column.business_name or column.physical_name))

    metrics: list[str] = []
    for aggregate in aggregates:
        aggregate_column = next(aggregate.find_all(exp.Column), None)
        matched_column = _resolve_expression_column(aggregate_column, columns, aliases)
        if matched_column is None:
            continue
        aggregate_kind = _aggregate_kind(aggregate, exp)
        candidates = [
            metric
            for metric in catalog.metrics
            if int(metric.table_id) == int(matched_column.table_id)
            and int(metric.column_id or 0) == int(matched_column.id)
            and str(getattr(metric, "status", "")) == "confirmed"
            and bool(getattr(metric, "is_queryable", False))
            and str(getattr(metric, "sync_state", "current")) == "current"
            and _metric_matches_aggregate(metric, aggregate_kind)
        ]
        if candidates:
            metrics.append(str(candidates[0].name))

    order_by: list[dict[str, str]] = []
    for ordered in expression.find_all(exp.Ordered):
        order_node = ordered.this
        order_name = _normalize_identifier(getattr(order_node, "name", ""))
        target = ""
        for metric in catalog.metrics:
            if str(getattr(metric, "status", "")) != "confirmed" or not bool(getattr(metric, "is_queryable", False)):
                continue
            if order_name in {
                _normalize_identifier(metric.name),
                _normalize_identifier(metric.business_name),
            }:
                target = str(metric.name)
                break
        if not target and metrics:
            target = metrics[0]
        if target:
            order_by.append({"field": target, "direction": "desc" if bool(ordered.args.get("desc")) else "asc"})
            break

    time_column_counts: dict[int, int] = {}
    time_predicate_types = tuple(
        item
        for item in (
            getattr(exp, "GT", None),
            getattr(exp, "GTE", None),
            getattr(exp, "LT", None),
            getattr(exp, "LTE", None),
            getattr(exp, "Between", None),
        )
        if item is not None
    )
    for predicate in expression.find_all(*time_predicate_types):
        for node in predicate.find_all(exp.Column):
            column = _resolve_expression_column(node, columns, aliases)
            if not column:
                continue
            label = f"{column.physical_name} {column.business_name} {column.data_type}".lower()
            if not any(token in label for token in ("date", "time", "日期", "时间", "月份", "年月")):
                continue
            column_id = int(column.id)
            time_column_counts[column_id] = time_column_counts.get(column_id, 0) + 1
    selected_time_column_id = (
        max(time_column_counts, key=lambda column_id: (time_column_counts[column_id], -column_id))
        if time_column_counts
        else None
    )

    limit = 100
    limit_node = expression.find(exp.Limit)
    if limit_node is not None:
        try:
            limit = max(1, min(int(limit_node.expression.this), 1000))
        except (AttributeError, TypeError, ValueError):
            limit = 100

    has_order = bool(order_by) or any(expression.find_all(exp.Order))
    query_type = "topn" if aggregates and has_order else "aggregate" if aggregates else "detail"
    return IntentQuery(
        query_type=query_type,
        tables=list(dict.fromkeys(str(item.business_name or item.physical_name) for item in tables)),
        metrics=list(dict.fromkeys(metrics)),
        dimensions=list(dict.fromkeys(dimensions)),
        explicit_dimensions=list(dict.fromkeys(dimensions)),
        filters=[],
        time_range=None,
        order_by=order_by,
        limit=limit,
        confidence=0.8,
        selected_time_column_id=selected_time_column_id,
    )


async def _authorization_revision(workspace_id: str) -> int:
    db_manager = get_async_db_manager()
    try:
        async with db_manager.session_scope() as session:
            value = (
                await session.execute(
                    select(AuthorizationRevisionModel.revision).where(
                        AuthorizationRevisionModel.workspace_id == workspace_id
                    )
                )
            ).scalar_one_or_none()
        return int(value or 0)
    except Exception:  # noqa: BLE001 - legacy development databases use revision 0
        return 0


async def validate_sql_example(
    *,
    question: str,
    sql: str,
    workspace_id: str,
    user_id: str,
    parameters: Optional[list[dict[str, Any]]] = None,
) -> SqlExampleValidationData:
    """Validate one example without executing its SQL."""

    from app.services.semantic_query_service import SemanticQueryError, get_semantic_query_service

    errors: list[dict[str, Any]] = []
    preview_sql = ""
    service = get_semantic_query_service()
    catalog = await service.get_catalog(workspace_id)
    if not catalog:
        return SqlExampleValidationData(
            status="invalid",
            errors=[_error("semantic_catalog_missing", "当前工作区尚未配置可用语义模型")],
        )

    try:
        expression, table_refs, column_refs, aliases, parse_errors = _parse_sql_references(sql, catalog)
        errors.extend(parse_errors)
    except ValueError as exc:
        return SqlExampleValidationData(
            status="invalid",
            errors=[_error("invalid_sql", str(exc))],
            schema_fingerprint=_catalog_validation_fingerprint(catalog),
            authorization_revision=await _authorization_revision(workspace_id),
            model_version=SQL_EXAMPLE_VALIDATION_MODEL_VERSION,
        )

    detected = _detect_parameters(expression, __import__("sqlglot").exp, question, table_refs, aliases, catalog)
    confirmed_parameters, parameter_errors = _merge_confirmed_parameters(detected, parameters, catalog)
    errors.extend(parameter_errors)
    verified_template = (
        not confirmed_parameters
        and _requires_verified_sql_execution(sql)
    )
    referenced_column_ids = {int(item.id) for item in column_refs}
    column_by_id = {int(item.id): item for item in catalog.columns}
    for parameter in confirmed_parameters:
        column = column_by_id.get(int(parameter.get("column_id") or 0))
        if column and int(column.id) not in referenced_column_ids:
            column_refs.append(column)
            referenced_column_ids.add(int(column.id))

    user_access = await service._load_user_access(user_id, workspace_id)
    user_access["semantic_access"] = await get_semantic_access_policy_service().load_runtime_access(
        workspace_id,
        catalog.datasource.id,
        user_access,
    )
    access_service = get_semantic_access_policy_service()
    for object_type, objects in (("table", table_refs), ("column", column_refs)):
        for obj in objects:
            decision = access_service.check_object_access(object_type, obj, user_access)
            if not decision.get("allowed"):
                label = getattr(obj, "business_name", None) or getattr(obj, "physical_name", "")
                errors.append(
                    _error(
                        "permission_denied",
                        f"当前账号无权使用{('表' if object_type == 'table' else '字段')}：{label}",
                        object_type=object_type,
                        object_id=int(obj.id),
                    )
                )

    if expression.find(__import__("sqlglot").exp.Star):
        referenced_table_ids = {int(item.id) for item in table_refs}
        for column in catalog.columns:
            if int(column.table_id) not in referenced_table_ids:
                continue
            decision = access_service.check_object_access("column", column, user_access)
            if not decision.get("allowed"):
                errors.append(
                    _error(
                        "wildcard_includes_hidden_column",
                        "SELECT * 会包含当前账号不可见的字段，请明确填写允许查询的字段",
                        column_id=int(column.id),
                    )
                )
                break

    if verified_template:
        row_scoped_tables = [
            table
            for table in table_refs
            if access_service.table_requires_row_filter(table, user_access)
        ]
        if row_scoped_tables:
            errors.append(
                _error(
                    "sql_example_row_scope_unsupported",
                    "复杂 SQL 示例涉及行级数据范围，无法在保持原 SQL 语义的同时安全执行",
                    table_ids=[int(item.id) for item in row_scoped_tables],
                )
            )

    referenced_ids = {int(item.id) for item in table_refs}
    if not verified_template and len(referenced_ids) > 1:
        relationship_pairs = [
            (int(item.left_table_id), int(item.right_table_id))
            for item in catalog.relationships
            if item.status == "confirmed" and item.is_queryable
            and int(item.left_table_id) in referenced_ids
            and int(item.right_table_id) in referenced_ids
        ]
        reachable = {next(iter(referenced_ids))}
        changed = True
        while changed:
            changed = False
            for left_id, right_id in relationship_pairs:
                if left_id in reachable and right_id not in reachable:
                    reachable.add(right_id)
                    changed = True
                elif right_id in reachable and left_id not in reachable:
                    reachable.add(left_id)
                    changed = True
        if reachable != referenced_ids:
            errors.append(
                _error(
                    "relationship_missing",
                    "示例涉及的表无法通过已确认的语义关联完整连接",
                    disconnected_table_ids=sorted(referenced_ids - reachable),
                )
            )

    intent = _intent_from_sql(
        expression,
        __import__("sqlglot").exp,
        table_refs,
        column_refs,
        aliases,
        catalog,
    )
    try:
        extracted = await service.extract_intent_hybrid(question, catalog)
        # The SQL-derived blueprint is authoritative. Natural-language
        # extraction may fill genuinely missing pieces, but must never replace
        # dimensions, metrics, tables, ordering, limit, or the time column
        # explicitly declared by a validated example SQL.
        if not intent.tables and extracted.tables:
            intent.tables = list(extracted.tables)
        if not intent.metrics and extracted.metrics:
            intent.metrics = extracted.metrics
        if not intent.dimensions and extracted.dimensions:
            intent.dimensions = extracted.dimensions
            intent.explicit_dimensions = extracted.explicit_dimensions
        if not intent.filters:
            intent.filters = [
                item
                for item in extracted.filters
                if str(item.get("value") or "").strip().lower() not in _GENERIC_VALUES
            ]
        if not intent.time_range:
            intent.time_range = extracted.time_range
        if not intent.order_by:
            intent.order_by = extracted.order_by
        if intent.query_type == "detail" and not intent.metrics and not intent.dimensions:
            intent.query_type = extracted.query_type
        intent.confidence = max(intent.confidence, extracted.confidence)
    except Exception:  # noqa: BLE001 - deterministic SQL blueprint remains available
        pass

    parameter_column_ids = {int(item.get("column_id") or 0) for item in confirmed_parameters}
    intent.filters = [
        item
        for item in intent.filters
        if not any(
            int(column.id) in parameter_column_ids
            for column in service._filter_columns_for_item(item, catalog, referenced_ids or None)
        )
    ]
    month_parameter = next(
        (item for item in confirmed_parameters if str(item.get("key") or "") == "month"),
        None,
    )
    if month_parameter and month_parameter.get("column_id"):
        intent.selected_time_column_id = int(month_parameter["column_id"])

    intent_payload = intent.model_dump(mode="json")
    if verified_template:
        intent_payload["execution_strategy"] = "verified_sql"

    if not errors and verified_template:
        preview_sql = _bounded_example_sql(expression, int(intent.limit or 100))
    elif not errors:
        try:
            plan = service.resolve_plan(intent, catalog, user_access)
            plan.sql = service.compile_sql(plan, catalog)
            service._validate_compiled_sql(plan.sql)
            preview_sql = plan.sql
        except SemanticQueryError as exc:
            errors.append(_error(exc.error_type, exc.message, details=exc.details))
        except Exception as exc:  # noqa: BLE001
            errors.append(_error("semantic_compile_failed", f"无法生成权限受控的查询计划：{exc}"))

    status = "valid" if not errors else "invalid"
    return SqlExampleValidationData(
        normalized_question=_normalize_question(question, confirmed_parameters),
        status=status,
        errors=errors,
        parameters=confirmed_parameters,
        intent=intent_payload if status == "valid" else {},
        schema_fingerprint=_catalog_validation_fingerprint(catalog),
        authorization_revision=int(
            (user_access.get("semantic_access") or {}).get("authorization_revision")
            or await _authorization_revision(workspace_id)
        ),
        model_version=SQL_EXAMPLE_VALIDATION_MODEL_VERSION,
        preview_sql=preview_sql,
    )


async def mark_example_stale(example: SqlExample, reason: str) -> None:
    db_manager = get_async_db_manager()
    errors = [_error("validation_stale", reason)]
    async with db_manager.session_scope() as session:
        await session.execute(
            update(SqlExampleModel)
            .where(
                SqlExampleModel.id == example.id,
                SqlExampleModel.workspace_id == example.workspace_id,
                SqlExampleModel.created_by == example.created_by,
            )
            .values(
                validation_status="stale",
                validation_errors=errors,
                is_active=False,
                vector_sync_status="pending_update",
                updated_at=datetime.now(),
            )
        )
    await update_sql_example_embedding(
        example_id=int(example.id or 0),
        question=example.question,
        sql=example.sql,
        workspace_id=example.workspace_id,
        owner_id=example.created_by,
        description=example.description or "",
        tables=example.tables or "",
        is_active=False,
        validation_status="stale",
        normalized_question=example.normalized_question,
    )


async def match_sql_example(
    *,
    question: str,
    workspace_id: str,
    user_id: str,
    catalog: Any,
    user_access: dict[str, Any],
) -> Optional[tuple[SqlExample, float]]:
    matches = await search_sql_examples_by_similarity(
        question=question,
        workspace_id=workspace_id,
        owner_id=user_id,
        n_results=3,
    )
    current_revision = int(
        (user_access.get("semantic_access") or {}).get("authorization_revision")
        or await _authorization_revision(workspace_id)
    )
    for example, score in matches:
        if example.validation_model_version != SQL_EXAMPLE_VALIDATION_MODEL_VERSION:
            await mark_example_stale(example, "示例校验规则已经更新，请重新校验")
            continue
        if (example.schema_fingerprint or "") != _catalog_validation_fingerprint(catalog):
            await mark_example_stale(example, "语义模型或数据库结构已经变化，请重新校验")
            continue
        if int(example.authorization_revision or 0) != current_revision:
            await mark_example_stale(example, "账号数据权限已经变化，请重新校验")
            continue
        return example, score
    return None


def extract_parameter_bindings(question: str, parameters: list[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    text = str(question or "").strip()
    bindings: dict[str, str] = {}
    missing: list[dict[str, Any]] = []
    for parameter in parameters:
        key = str(parameter.get("key") or "")
        label = str(parameter.get("label") or key)
        aliases = [label, *(str(item) for item in (parameter.get("aliases") or []))]
        value = ""
        if key == "month":
            # Parse date expressions before generic alias matching. Otherwise
            # the one-character alias “月” in “2026年5月各产品…” can incorrectly
            # capture the following business phrase as the month value.
            month_match = re.search(r"(\d{4}\s*年\s*\d{1,2}\s*月|\d{4}[-/]\d{1,2})", text)
            if month_match:
                value = month_match.group(1).replace(" ", "")
            else:
                value = next(
                    (item for item in ("本月", "当月", "这个月", "上月", "下月") if item in text),
                    "",
                )
        else:
            for alias in sorted({item for item in aliases if item}, key=len, reverse=True):
                patterns = (
                    rf"{re.escape(alias)}\s*(?:是|为|=|:|：)\s*([^，,。；;\s]+)",
                    rf"{re.escape(alias)}\s*([^，,。；;\s]{{2,30}}?)(?=本月|本年|下单|订单|采购|销售|发货|$)",
                )
                for pattern in patterns:
                    match = re.search(pattern, text, flags=re.IGNORECASE)
                    if match:
                        value = match.group(1).strip()
                        break
                if value:
                    break
        if key in {"customer_name", "supplier_name"} and value in {"本月", "当月", "这个月", "上月", "下月", "本年", "今年"}:
            value = ""
        if not value and key in {"customer_name", "supplier_name"}:
            prefix = re.match(
                r"^(?:查询|统计|请查|请统计)?\s*([^，,。；;\s]{2,30}?)"
                r"(?=本月|本年|这个月|当月|\d{4}\s*年\s*\d{1,2}\s*月|\d{4}[-/]\d{1,2})",
                text,
            )
            if prefix:
                value = prefix.group(1).strip()
                for generic in ("某个客户", "某客户", "客户", "某个供应商", "某供应商", "供应商"):
                    if value == generic:
                        value = ""
                        break
        if value and value.lower() not in _GENERIC_VALUES:
            bindings[key] = value
        elif parameter.get("required", True):
            missing.append(parameter)
    return bindings, missing


def _normalize_month_binding(value: str) -> str:
    """Normalize explicit user month values to the compiler's YYYY-MM format."""

    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
    if not match:
        match = re.fullmatch(r"(\d{4})[-/](\d{1,2})", text)
    if not match:
        return text
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return text
    return f"{year:04d}-{month:02d}"


def apply_example_intent(
    intent: Any,
    example: SqlExample,
    bindings: dict[str, str],
) -> Any:
    from app.services.semantic_query_service import IntentQuery

    hint = IntentQuery.model_validate(example.intent or {})
    if not intent.tables:
        intent.tables = list(hint.tables)
    else:
        intent.tables = list(dict.fromkeys([*hint.tables, *intent.tables]))
    if not intent.metrics:
        intent.metrics = list(hint.metrics)
    if not intent.dimensions:
        intent.dimensions = list(hint.dimensions)
        intent.explicit_dimensions = hint.explicit_dimensions
    if not intent.time_range:
        intent.time_range = hint.time_range
    if not intent.order_by:
        intent.order_by = list(hint.order_by)
    if intent.query_type == "detail" and hint.query_type != "detail":
        intent.query_type = hint.query_type
    intent.limit = min(intent.limit or hint.limit, hint.limit or intent.limit)
    existing_fields = {str(item.get("field") or "") for item in intent.filters}
    for item in hint.filters:
        if str(item.get("field") or "") not in existing_fields:
            intent.filters.append(dict(item))
    for parameter in example.parameters:
        key = str(parameter.get("key") or "")
        value = bindings.get(key)
        field = str(parameter.get("field") or "")
        if key == "month" and value:
            intent.time_range = _normalize_month_binding(value)
            if parameter.get("column_id"):
                intent.selected_time_column_id = int(parameter["column_id"])
            continue
        if value and field:
            intent.filters = [item for item in intent.filters if str(item.get("field") or "") != field]
            intent.filters.append(
                {
                    "field": field,
                    "operator": str(parameter.get("operator") or "eq"),
                    "value": value,
                }
            )
    intent.confidence = max(float(intent.confidence or 0), float(hint.confidence or 0), 0.85)
    intent.clarification_items = [
        item for item in intent.clarification_items if "指标" not in str(item) or not intent.metrics
    ]
    intent.matching_trace.append(
        {
            "type": "private_sql_example",
            "example_id": int(example.id or 0),
            "owner_id": example.created_by,
            "question": example.question,
        }
    )
    return intent


async def record_example_match(example_id: int, workspace_id: str, owner_id: str) -> None:
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        await session.execute(
            update(SqlExampleModel)
            .where(
                SqlExampleModel.id == example_id,
                SqlExampleModel.workspace_id == workspace_id,
                SqlExampleModel.created_by == owner_id,
                SqlExampleModel.validation_status == "valid",
            )
            .values(
                last_matched_at=datetime.now(),
                match_count=SqlExampleModel.match_count + 1,
            )
        )


async def claim_orphan_sql_example(
    *,
    example_id: int,
    workspace_id: str,
    owner_id: str,
) -> Optional[SqlExample]:
    """Assign only an orphaned migrated example; content remains immutable."""

    from app.models.auth.rbac import UserModel

    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        target_owner = (
            await session.execute(
                select(UserModel.id).where(
                    UserModel.id == owner_id,
                    UserModel.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if not target_owner:
            raise ValueError("目标账号不存在或不属于当前工作区")
        row = (
            await session.execute(
                select(SqlExampleModel).where(
                    SqlExampleModel.id == example_id,
                    SqlExampleModel.workspace_id == workspace_id,
                ).with_for_update()
            )
        ).scalar_one_or_none()
        if not row:
            return None
        existing_owner = (
            await session.execute(
                select(UserModel.id).where(
                    UserModel.id == row.created_by,
                    UserModel.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if existing_owner:
            raise ValueError("该示例已有有效归属账号，管理员不能修改其归属")
        row.created_by = owner_id
        row.group_id = None
        row.is_active = False
        row.validation_status = "stale"
        row.validation_errors = [_error("owner_claimed", "归属账号已更新，请新账号重新校验")]
        row.authorization_revision = 0
        row.validated_at = None
        row.vector_sync_status = "pending_update"
        row.updated_at = datetime.now()
        await session.flush()
        await session.refresh(row)
        result = SqlExample.from_orm(row)
    await update_sql_example_embedding(
        example_id=int(result.id or 0),
        question=result.question,
        sql=result.sql,
        workspace_id=result.workspace_id,
        owner_id=result.created_by,
        description=result.description or "",
        tables=result.tables or "",
        is_active=False,
        validation_status="stale",
        normalized_question=result.normalized_question,
    )
    return result


async def _execute_verified_sql_example(
    semantic_service: Any,
    *,
    example: SqlExample,
    score: float,
    question: str,
    user_id: str,
    workspace_id: str,
    session_id: str,
    catalog: Any,
    user_access: dict[str, Any],
    expected_limit: Optional[int],
):
    """Execute one fixed, validated SQL example without semantic shape loss."""

    from app.services.semantic_query_service import SemanticExecutionResult, SemanticQueryError

    started = time.perf_counter()
    try:
        expression, table_refs, column_refs, _aliases, parse_errors = _parse_sql_references(
            example.sql,
            catalog,
        )
    except ValueError as exc:
        raise SemanticQueryError(
            "sql_example_invalid",
            f"SQL 示例已失效：{exc}",
            retryable=False,
            safe_to_fallback=False,
        ) from exc
    if parse_errors:
        raise SemanticQueryError(
            "sql_example_invalid",
            "SQL 示例引用的语义对象已经失效，请重新校验该示例",
            retryable=False,
            safe_to_fallback=False,
            details={"validation_errors": parse_errors},
        )

    access_service = get_semantic_access_policy_service()
    denied: list[dict[str, Any]] = []
    for object_type, objects in (("table", table_refs), ("column", column_refs)):
        for obj in objects:
            decision = access_service.check_object_access(object_type, obj, user_access)
            if not decision.get("allowed"):
                denied.append(
                    {
                        "object_type": object_type,
                        "object_id": int(obj.id),
                        "label": getattr(obj, "business_name", None) or getattr(obj, "physical_name", ""),
                    }
                )
    if denied:
        raise SemanticQueryError(
            "permission_denied",
            "当前账号无权执行该 SQL 示例引用的全部表或字段",
            retryable=False,
            safe_to_fallback=False,
            details={"denied_objects": denied},
        )

    exp = __import__("sqlglot").exp
    if expression.find(exp.Star):
        referenced_table_ids = {int(item.id) for item in table_refs}
        hidden_columns = [
            column
            for column in catalog.columns
            if int(column.table_id) in referenced_table_ids
            and not access_service.check_object_access("column", column, user_access).get("allowed")
        ]
        if hidden_columns:
            raise SemanticQueryError(
                "permission_denied",
                "SQL 示例中的 SELECT * 会读取当前账号不可见的字段",
                retryable=False,
                safe_to_fallback=False,
                details={"hidden_column_ids": [int(item.id) for item in hidden_columns]},
            )

    row_scoped_tables = [
        table
        for table in table_refs
        if access_service.table_requires_row_filter(table, user_access)
    ]
    if row_scoped_tables:
        # Injecting a predicate into arbitrary nested/outer-join SQL can change
        # business semantics.  Fail closed until the query is represented by a
        # governed view or a semantic metric with compiler-managed row scope.
        raise SemanticQueryError(
            "sql_example_row_scope_unsupported",
            "该复杂 SQL 示例涉及行级数据范围，系统不能在不改变原查询语义的前提下安全执行",
            retryable=False,
            safe_to_fallback=False,
            details={
                "tables": [str(item.business_name or item.physical_name) for item in row_scoped_tables],
                "suggestion": "请将该查询封装为受控数据库视图，或改用支持行级权限的语义指标。",
            },
        )

    intent_limit = int((example.intent or {}).get("limit") or 100)
    max_rows = min(int(expected_limit or intent_limit), int(semantic_service.MAX_LIMIT))
    bounded_sql = _bounded_example_sql(expression, max_rows)

    config = await get_user_db_config_async(user_id)
    if not config or not config.is_active:
        config = await get_workspace_db_config_async(workspace_id)
    if not config or not config.is_active:
        config = await get_workspace_admin_db_config_async(workspace_id)
    if not config or not config.is_active:
        raise SemanticQueryError("datasource_missing", "用户或工作区未配置数据库连接")
    execution_url = config.get_readonly_connection_url()
    if not execution_url:
        raise SemanticQueryError(
            "readonly_datasource_missing",
            "复杂 SQL 示例必须使用只读数据库账号执行，请先配置只读连接",
            retryable=False,
            safe_to_fallback=False,
        )

    executor = ReadOnlyExecutor(user_id, execution_url)
    query_result = executor.execute_query(bounded_sql, timeout_sec=5, max_rows=max_rows)
    if query_result.error:
        raise SemanticQueryError(
            "readonly_execution_failed",
            query_result.error,
            safe_to_fallback=False,
        )

    rows = [tuple(row) for row in query_result.rows]
    columns = list(query_result.columns)
    data = [dict(zip(columns, row)) for row in rows]
    diagnostics = analyze_sql_result(
        question=question,
        columns=columns,
        rows=rows,
        expected_limit=expected_limit or max_rows,
    )
    result_text = append_diagnostics_text(
        _format_verified_result_text(columns, rows, query_result.row_count),
        diagnostics,
    )
    referenced_tables = list(
        dict.fromkeys(str(item.physical_name) for item in table_refs)
    )
    public_match = {
        "type": "sql_example_match",
        "severity": "info",
        "message": "已按当前账号审核通过的复杂 SQL 示例原样执行",
        "example_id": int(example.id or 0),
        "question": example.question,
        "similarity": round(float(score), 4),
        "parameter_keys": [],
        "execution_strategy": "verified_sql",
    }
    public_intent = dict(example.intent or {})
    plan = {
        "execution_strategy": "verified_sql_example",
        "example_id": int(example.id or 0),
        "datasource_id": int(catalog.datasource.id),
        "table_ids": [int(item.id) for item in table_refs],
        "column_ids": [int(item.id) for item in column_refs],
        "user_access": user_access,
        "sql": bounded_sql,
        "referenced_tables": referenced_tables,
        "diagnostics": diagnostics,
    }
    execution_ms = int((time.perf_counter() - started) * 1000)
    run_id = await semantic_service.record_query_run(
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        question=question,
        semantic_enabled=True,
        fallback_used=False,
        status="success",
        error_type=None,
        intent=public_intent,
        plan=plan,
        sql=bounded_sql,
        referenced_tables=referenced_tables,
        row_count=query_result.row_count,
        execution_time_ms=execution_ms,
    )
    await record_example_match(int(example.id or 0), workspace_id, user_id)
    return SemanticExecutionResult(
        success=True,
        sql=bounded_sql,
        intent=public_intent,
        plan=plan,
        data=data,
        columns=columns,
        row_count=query_result.row_count,
        result_text=result_text,
        referenced_tables=referenced_tables,
        execution_time_ms=execution_ms,
        run_id=run_id,
        diagnostics=[public_match, *diagnostics],
        sql_example_match=public_match,
    )


async def execute_verified_sql_example_if_matched(
    semantic_service: Any,
    *,
    question: str,
    user_id: str,
    workspace_id: str,
    session_id: str = "",
    expected_limit: Optional[int] = None,
):
    """Return a verified complex-example result, or ``None`` when not applicable."""

    catalog = await semantic_service.get_catalog(workspace_id)
    if not catalog:
        return None
    user_access = await semantic_service._load_user_access(user_id, workspace_id)
    user_access["semantic_access"] = await get_semantic_access_policy_service().load_runtime_access(
        workspace_id,
        catalog.datasource.id,
        user_access,
    )
    matched = await match_sql_example(
        question=question,
        workspace_id=workspace_id,
        user_id=user_id,
        catalog=catalog,
        user_access=user_access,
    )
    if not matched:
        return None
    example, score = matched
    if example.parameters or not _requires_verified_sql_execution(example.sql):
        return None
    return await _execute_verified_sql_example(
        semantic_service,
        example=example,
        score=score,
        question=question,
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        catalog=catalog,
        user_access=user_access,
        expected_limit=expected_limit,
    )


async def execute_semantic_query_with_private_examples(
    semantic_service: Any,
    *,
    question: str,
    context_hint: Optional[str],
    user_id: str,
    workspace_id: str,
    session_id: str = "",
    force_enabled: bool = False,
    expected_limit: Optional[int] = None,
    semantic_clarification: Optional[dict[str, Any]] = None,
):
    """Execute a private example without silently changing its query shape.

    Common analytical examples still use the semantic compiler.  Fixed complex
    examples use the verified-template path after current permissions are
    checked again.
    """

    from app.services.semantic_query_service import SemanticQueryError

    catalog = await semantic_service.get_catalog(workspace_id)
    matched: Optional[tuple[SqlExample, float]] = None
    bindings: dict[str, str] = {}
    if catalog:
        user_access = await semantic_service._load_user_access(user_id, workspace_id)
        user_access["semantic_access"] = await get_semantic_access_policy_service().load_runtime_access(
            workspace_id,
            catalog.datasource.id,
            user_access,
        )
        matched = await match_sql_example(
            question=question,
            workspace_id=workspace_id,
            user_id=user_id,
            catalog=catalog,
            user_access=user_access,
        )
        if matched:
            example, _score = matched
            bindings, missing = extract_parameter_bindings(question, example.parameters)
            free_text = ""
            if (
                isinstance(semantic_clarification, dict)
                and semantic_clarification.get("kind") == "sql_example_parameter"
            ):
                free_text = str(semantic_clarification.get("free_text") or "").strip()
            if free_text:
                parsed_bindings, still_missing = extract_parameter_bindings(free_text, missing)
                bindings.update(parsed_bindings)
                if len(missing) == 1 and not parsed_bindings:
                    missing_parameter = missing[0]
                    bindings[str(missing_parameter.get("key") or "")] = free_text
                    missing = []
                else:
                    missing = still_missing
            if missing:
                labels = "、".join(str(item.get("label") or item.get("key")) for item in missing)
                input_hint = (
                    f"请按“{missing[0].get('label')}：…，{missing[1].get('label')}：…”的格式补充。"
                    if len(missing) > 1
                    else f"请补充{labels}后继续查询。"
                )
                raise SemanticQueryError(
                    "semantic_clarification_required",
                    input_hint,
                    retryable=False,
                    safe_to_fallback=False,
                    details={
                        "clarification": {
                            "kind": "sql_example_parameter",
                            "message": input_hint,
                            "options": [],
                            "blocked_objects": [],
                            "intent_patch": {},
                            "selection_patch": {},
                            "original_intent": example.intent,
                            "parameters": [
                                {
                                    "key": item.get("key"),
                                    "label": item.get("label"),
                                    "data_type": item.get("data_type"),
                                }
                                for item in missing
                            ],
                        }
                    },
                )
            if not example.parameters and _requires_verified_sql_execution(example.sql):
                return await _execute_verified_sql_example(
                    semantic_service,
                    example=example,
                    score=_score,
                    question=question,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    catalog=catalog,
                    user_access=user_access,
                    expected_limit=expected_limit,
                )
            safe_intent = dict(example.intent or {})
            filters = [dict(item) for item in safe_intent.get("filters") or []]
            for parameter in example.parameters:
                parameter_key = str(parameter.get("key") or "")
                value = bindings.get(parameter_key)
                field = str(parameter.get("field") or "")
                if not value or not field:
                    continue
                if parameter_key == "month":
                    safe_intent["time_range"] = _normalize_month_binding(value)
                    if parameter.get("column_id"):
                        safe_intent["selected_time_column_id"] = int(parameter["column_id"])
                    continue
                filters = [item for item in filters if str(item.get("field") or "") != field]
                filters.append(
                    {
                        "field": field,
                        "operator": str(parameter.get("operator") or "eq"),
                        "value": value,
                    }
                )
            safe_intent["filters"] = filters

    result = await semantic_service.execute_semantic_query(
        question=question,
        context_hint=context_hint,
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        force_enabled=force_enabled,
        expected_limit=expected_limit,
        semantic_clarification=semantic_clarification,
        _validated_intent_hint=safe_intent if matched else None,
    )
    if matched:
        example, score = matched
        public_match = {
            "type": "sql_example_match",
            "severity": "info",
            "message": "已采用当前账号的 SQL 示例作为查询口径参考",
            "example_id": int(example.id or 0),
            "question": example.question,
            "similarity": round(float(score), 4),
            "parameter_keys": sorted(bindings),
        }
        result.diagnostics = [public_match, *(result.diagnostics or [])]
        if isinstance(result.plan, dict):
            result.plan["sql_example_match"] = public_match
        await record_example_match(int(example.id or 0), workspace_id, user_id)
    return result
