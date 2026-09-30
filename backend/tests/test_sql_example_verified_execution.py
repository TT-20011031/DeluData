from app.services.sql_example_service import (
    _bounded_example_sql,
    _requires_verified_sql_execution,
    _sql_expression,
)


UNSHIPPED_ORDER_SQL = """
SELECT
    a.task_no AS 订单号,
    b.quantity - COALESCE(xsd.out_store_num, 0) - COALESCE(fh.out_store_num, 0) AS 未发货数量
FROM jf_sale_order a
JOIN jf_sale_order_1 b ON a.sale_order_no = b.main_code
LEFT JOIN (
    SELECT source_code, SUM(out_store_num) AS out_store_num
    FROM jf_out_store_detail
    WHERE source_code LIKE 'XSDD%'
    GROUP BY source_code
) xsd ON b.sales_order_detail_no = xsd.source_code
LEFT JOIN (
    SELECT s.sorderno, SUM(d.out_store_num) AS out_store_num
    FROM jf_out_store_detail d
    JOIN jf_sales_shipmentlist s ON d.source_code = s.detail_code
    WHERE d.source_code LIKE 'FH%'
    GROUP BY s.sorderno
) fh ON b.sales_order_detail_no = fh.sorderno
WHERE b.quantity - COALESCE(xsd.out_store_num, 0) - COALESCE(fh.out_store_num, 0) > 0
ORDER BY a.task_no DESC;
"""


def test_complex_unshipped_order_example_requires_verified_sql_execution():
    assert _requires_verified_sql_execution(UNSHIPPED_ORDER_SQL) is True


def test_verified_sql_keeps_business_predicates_and_adds_top_level_limit():
    expression, _exp = _sql_expression(UNSHIPPED_ORDER_SQL)

    rendered = _bounded_example_sql(expression, 100)

    normalized = rendered.upper()
    assert "XSDD%" in rendered
    assert "FH%" in rendered
    assert normalized.count("SUM(") == 2
    assert normalized.count("COALESCE(") >= 4
    assert "> 0" in rendered
    assert "ORDER BY" in normalized
    assert normalized.endswith("LIMIT 100")


def test_plain_projection_without_predicates_stays_on_semantic_compiler():
    assert _requires_verified_sql_execution(
        "SELECT task_no, customer_name FROM jf_sale_order"
    ) is False
