"""SqlWorker table existence guard tests."""

from app.agents.sql_worker import (
    SqlWorker,
    _build_result_text,
    _find_missing_tables,
    _format_table_constraint_prompt,
    _parse_sql_table_refs,
    _redact_pseudo_source_literals,
    _remove_pseudo_source_columns,
)


def test_parse_sql_table_refs_handles_joins_and_qualified_names():
    sql = """
    SELECT p.name, SUM(t.qty)
    FROM `erp`.`produce_task` t
    JOIN product p ON p.id = t.product_id
    WHERE t.status = 'FROM fake_table'
    """

    assert _parse_sql_table_refs(sql) == {"produce_task", "product"}


def test_find_missing_tables_blocks_hallucinated_table():
    sql = "SELECT product_name FROM clean_produce_task ORDER BY qty DESC LIMIT 1"

    assert _find_missing_tables(sql, {"produce_task", "product"}) == [
        "clean_produce_task"
    ]


def test_find_missing_tables_allows_existing_tables():
    sql = "SELECT p.name FROM produce_task t JOIN product p ON p.id = t.product_id"

    assert _find_missing_tables(sql, {"produce_task", "product"}) == []


def test_format_table_constraint_prompt_lists_real_tables_only():
    prompt = _format_table_constraint_prompt("生产最多产品", {"produce_task", "product"})

    assert "数据库真实表约束" in prompt
    assert "produce_task" in prompt
    assert "product" in prompt
    assert "不要猜测" in prompt
    assert "不要生成" in prompt


def test_remove_pseudo_source_columns_drops_hallucinated_source_table():
    columns = ["表名", "产品名称", "生产数量"]
    rows = [("clean_produce_task", "芯片-dev-1.1", 654654)]

    clean_columns, clean_rows, removed = _remove_pseudo_source_columns(columns, rows)

    assert clean_columns == ["产品名称", "生产数量"]
    assert clean_rows == [["芯片-dev-1.1", 654654]]
    assert removed == ["表名"]


def test_result_text_does_not_include_removed_source_table():
    columns = ["产品名称", "生产数量"]
    rows = [["芯片-dev-1.1", 654654]]

    result_text = _build_result_text(columns, rows, row_count=1)

    assert "芯片-dev-1.1" in result_text
    assert "clean_produce_task" not in result_text


def test_synthesizer_context_prefers_actual_referenced_tables():
    output = SqlWorker().format_result_for_synthesizer(
        {
            "success": True,
            "sql": "SELECT 'clean_produce_task' AS `表名`, product_name FROM clean_dispatch_order_clean_pro",
            "rewrite": "查询生产数量 Top 5",
            "selected_tables": ["clean_produce_task"],
            "referenced_tables": ["clean_dispatch_order_clean_pro"],
            "df_key": "sql_clean_dispatch_order_clean_pro_r1",
            "row_count": 5,
            "result_text": "产品名称 | 生产数量\n芯片-dev-1.1 | 654654\n",
        }
    )

    assert "实际引用表: clean_dispatch_order_clean_pro" in output
    assert "clean_produce_task" not in output
    assert "选中的表" not in output
    assert "XiYan 选择器结果" in output


def test_redact_pseudo_source_literals_in_display_sql():
    sql = "SELECT 'clean_produce_task' AS `表名`, product_name FROM clean_dispatch_order_clean_pro"

    redacted = _redact_pseudo_source_literals(sql)

    assert "clean_produce_task" not in redacted
    assert "clean_dispatch_order_clean_pro" in redacted
