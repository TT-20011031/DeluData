from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.sql_example_service import (
    _detect_parameters,
    _intent_from_sql,
    _merge_confirmed_parameters,
    _normalize_month_binding,
    _normalize_question,
    _parse_sql_references,
    _sql_expression,
    extract_parameter_bindings,
)
from app.services.semantic_query_service import IntentQuery, SemanticQueryService


def _catalog():
    table = SimpleNamespace(
        id=1,
        physical_name="jf_sale_order",
        business_name="销售订单主表",
        synonyms=[],
    )
    customer = SimpleNamespace(
        id=11,
        table_id=1,
        physical_table="jf_sale_order",
        physical_name="customer_name",
        business_name="客户名称",
        data_type="varchar",
        synonyms=["客户"],
    )
    return SimpleNamespace(
        tables=[table],
        columns=[customer],
        datasource=SimpleNamespace(database="niao_test"),
    )


def _sales_catalog():
    order = SimpleNamespace(
        id=1,
        physical_name="clean_jf_sale_order",
        business_name="销售订单",
        synonyms=[],
    )
    detail = SimpleNamespace(
        id=2,
        physical_name="clean_jf_sale_order_1",
        business_name="销售订单明细",
        synonyms=[],
    )

    def column(column_id, table_id, physical_name, business_name, data_type="varchar"):
        return SimpleNamespace(
            id=column_id,
            table_id=table_id,
            physical_table=order.physical_name if table_id == 1 else detail.physical_name,
            physical_name=physical_name,
            business_name=business_name,
            data_type=data_type,
            synonyms=[],
        )

    columns = [
        column(11, 1, "sale_order_no", "销售订单号"),
        column(12, 1, "customer_name", "客户名称"),
        column(13, 1, "money_sum", "销售金额", "decimal"),
        column(14, 1, "make_date", "下单日期", "date"),
        column(21, 2, "main_code", "订单主编码"),
        column(22, 2, "product_name", "产品名称"),
        column(23, 2, "quantity", "销售数量", "decimal"),
    ]

    def metric(metric_id, name, business_name, aggregation, table_id, column_id, status="confirmed", is_queryable=True):
        return SimpleNamespace(
            id=metric_id,
            name=name,
            business_name=business_name,
            aggregation=aggregation,
            table_id=table_id,
            column_id=column_id,
            status=status,
            is_queryable=is_queryable,
            sync_state="current",
        )

    metrics = [
        metric(100, "disabled_order_count", "订单数量旧指标", "count_distinct", 1, 11, "disabled", False),
        metric(101, "sales_order_count", "订单数量", "count_distinct", 1, 11),
        metric(102, "sales_amount", "销售总金额", "sum", 1, 13),
        metric(103, "sales_detail_quantity", "销售数量", "sum", 2, 23),
    ]
    datasource = SimpleNamespace(database="niao_test")
    return SimpleNamespace(tables=[order, detail], columns=columns, metrics=metrics, datasource=datasource)


def test_readonly_parser_rejects_mutating_sql():
    with pytest.raises(ValueError, match="只读 SELECT"):
        _sql_expression("DELETE FROM jf_sale_order")


def test_readonly_parser_rejects_multiple_statements():
    with pytest.raises(ValueError, match="一条只读"):
        _sql_expression("SELECT 1; SELECT 2")


def test_readonly_parser_allows_trailing_semicolon_comment():
    expression, exp = _sql_expression("SELECT 1; -- 结果说明")

    assert expression.find(exp.Select) is not None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SLEEP(5)",
        "SELECT LOAD_FILE('/etc/passwd')",
        "SELECT customer_name FROM jf_sale_order FOR UPDATE",
        "SELECT customer_name INTO OUTFILE '/tmp/export' FROM jf_sale_order",
    ],
)
def test_readonly_parser_rejects_dangerous_selects(sql):
    with pytest.raises(ValueError, match="危险函数|文件写入或加锁"):
        _sql_expression(sql)


def test_detects_customer_parameter_and_normalizes_question():
    catalog = _catalog()
    expression, tables, _columns, aliases, errors = _parse_sql_references(
        "SELECT customer_name FROM jf_sale_order WHERE customer_name = 'XX'",
        catalog,
    )
    assert errors == []
    import sqlglot

    parameters = _detect_parameters(
        expression,
        sqlglot.exp,
        "XX客户本月销售订单下单情况",
        tables,
        aliases,
        catalog,
    )
    assert parameters[0]["key"] == "customer_name"
    assert parameters[0]["column_id"] == 11
    assert _normalize_question("XX客户本月销售订单下单情况", parameters).startswith("{{customer_name}}")
    assert _normalize_question("某个客户本月销售订单下单情况", parameters).startswith("{{customer_name}}")


def test_dynamic_customer_value_and_missing_value_are_distinguished():
    parameter = {
        "key": "customer_name",
        "label": "客户",
        "aliases": ["客户名称"],
        "required": True,
    }
    bindings, missing = extract_parameter_bindings("王益民本月销售订单下单情况", [parameter])
    assert bindings == {"customer_name": "王益民"}
    assert missing == []

    bindings, missing = extract_parameter_bindings("新台宏-B组2026年5月销售订单下单情况", [parameter])
    assert bindings == {"customer_name": "新台宏-B组"}
    assert missing == []

    bindings, missing = extract_parameter_bindings("某个客户本月销售订单下单情况", [parameter])
    assert bindings == {}
    assert [item["key"] for item in missing] == ["customer_name"]


def test_parameter_value_is_data_not_sql_substitution():
    parameter = {
        "key": "customer_name",
        "label": "客户",
        "aliases": ["客户名称"],
        "required": True,
    }
    value = "王益民'OR'1'='1--"
    bindings, missing = extract_parameter_bindings(f"客户：{value}", [parameter])
    assert missing == []
    assert bindings["customer_name"] == value


def test_multiple_missing_parameters_can_be_supplied_with_labels():
    parameters = [
        {"key": "customer_name", "label": "客户", "aliases": ["客户名称"], "required": True},
        {"key": "month", "label": "月份", "aliases": ["月"], "required": True},
    ]
    bindings, missing = extract_parameter_bindings("客户：王益民，月份：2026-08", parameters)
    assert missing == []
    assert bindings == {"customer_name": "王益民", "month": "2026-08"}


def test_month_binding_is_normalized_for_semantic_compilation():
    assert _normalize_month_binding("2026年5月") == "2026-05"
    assert _normalize_month_binding("2026/5") == "2026-05"
    assert _normalize_month_binding("本月") == "本月"


def test_month_binding_does_not_capture_words_after_single_character_alias():
    parameter = {
        "key": "month",
        "label": "月份",
        "aliases": ["月份", "月", "制单日期", "make_date"],
        "required": True,
    }

    bindings, missing = extract_parameter_bindings(
        "统计2026年5月各产品的销售数量排行（按销量降序）",
        [parameter],
    )

    assert missing == []
    assert bindings == {"month": "2026年5月"}


def test_revalidation_keeps_confirmed_parameter_and_adds_newly_detected_month():
    catalog = _sales_catalog()
    detected = [
        {
            "key": "customer_name",
            "label": "客户",
            "column_id": 11,
            "operator": "eq",
        },
        {
            "key": "month",
            "label": "月份",
            "data_type": "month",
            "table_id": 1,
            "column_id": 14,
            "field": "下单日期",
            "required": True,
            "aliases": ["月份", "下单日期"],
            "example_value": "2026年5月",
            "operator": "month_range",
        },
    ]
    supplied = [
        {
            "key": "customer_name",
            "label": "客户",
            "data_type": "text",
            "column_id": 11,
            "operator": "eq",
        }
    ]

    merged, errors = _merge_confirmed_parameters(detected, supplied, catalog)

    assert errors == []
    assert [(item["key"], item["column_id"], item["operator"]) for item in merged] == [
        ("customer_name", 11, "eq"),
        ("month", 14, "month_range"),
    ]


def test_sql_blueprint_preserves_product_dimension_metric_time_and_order():
    catalog = _sales_catalog()
    sql = """
        SELECT d.product_name, SUM(d.quantity) AS sales_quantity
        FROM clean_jf_sale_order_1 d
        JOIN clean_jf_sale_order o ON d.main_code = o.sale_order_no
        WHERE o.make_date >= '2026-05-01' AND o.make_date < '2026-06-01'
        GROUP BY d.product_name
        ORDER BY SUM(d.quantity) DESC
        LIMIT 10
    """
    expression, tables, columns, aliases, errors = _parse_sql_references(sql, catalog)
    assert errors == []

    import sqlglot

    intent = _intent_from_sql(expression, sqlglot.exp, tables, columns, aliases, catalog)
    assert intent.tables == ["销售订单明细", "销售订单"]
    assert intent.metrics == ["sales_detail_quantity"]
    assert intent.dimensions == ["产品名称"]
    assert intent.selected_time_column_id == 14
    assert intent.order_by == [{"field": "sales_detail_quantity", "direction": "desc"}]
    assert intent.limit == 10


def test_sql_blueprint_maps_all_aggregate_types_to_confirmed_metrics():
    catalog = _sales_catalog()
    sql = """
        SELECT o.customer_name,
               COUNT(DISTINCT o.sale_order_no) AS order_count,
               SUM(o.money_sum) AS total_amount
        FROM clean_jf_sale_order o
        WHERE o.make_date >= '2026-05-01' AND o.make_date < '2026-06-01'
          AND o.customer_name = 'XX'
        GROUP BY o.customer_name
    """
    expression, tables, columns, aliases, errors = _parse_sql_references(sql, catalog)
    assert errors == []

    import sqlglot

    intent = _intent_from_sql(expression, sqlglot.exp, tables, columns, aliases, catalog)
    assert intent.metrics == ["sales_order_count", "sales_amount"]
    assert intent.dimensions == ["客户名称"]
    assert intent.selected_time_column_id == 14

    parameters = _detect_parameters(
        expression,
        sqlglot.exp,
        "XX客户2026年5月销售订单下单情况",
        tables,
        aliases,
        catalog,
    )
    assert [(item["key"], item["operator"]) for item in parameters] == [
        ("customer_name", "eq"),
        ("month", "month_range"),
    ]
    assert parameters[1]["column_id"] == 14


def test_validated_example_shape_wins_over_model_inference():
    current = IntentQuery(
        query_type="topn",
        tables=["销售订单", "销售订单明细"],
        metrics=["sales_amount"],
        dimensions=["客户款号", "客户货号"],
        explicit_dimensions=["客户款号", "客户货号"],
        filters=[{"field": "客户名称", "operator": "contains", "value": "新台宏-B组"}],
        time_range="2026-05",
        order_by=[{"field": "sales_amount", "direction": "desc"}],
        limit=100,
        selected_time_column_id=99,
    )
    hint = IntentQuery(
        query_type="topn",
        tables=["销售订单明细", "销售订单"],
        metrics=["sales_detail_quantity"],
        dimensions=["产品名称"],
        explicit_dimensions=["产品名称"],
        filters=[],
        time_range="2026-05",
        order_by=[{"field": "sales_detail_quantity", "direction": "desc"}],
        limit=10,
        selected_time_column_id=14,
    )

    merged = SemanticQueryService._merge_validated_intent_hint(
        current,
        hint.model_dump(mode="json"),
    )

    assert merged.metrics == ["sales_detail_quantity"]
    assert merged.dimensions == ["产品名称"]
    assert merged.filters == []
    assert merged.limit == 10
    assert merged.selected_time_column_id == 14
