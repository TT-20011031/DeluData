import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.sql_worker import (
    build_sql_task_context,
    _validate_single_select_sql,
    validate_inventory_turnover_sql,
)
import app.supervisor.nodes.direct_execute as direct_execute_module
import app.supervisor.nodes.executor as executor_module
import app.tools.sql_tool as sql_tool_module


def test_build_sql_task_context_preserves_turnover_followup_context():
    context = build_sql_task_context(
        task_description="库存不为0的周转率最差的三个商品",
        summary="上一轮查询了近180天库存周转率最差商品。",
        messages=[
            HumanMessage(content="近180天内库存周转率最差的三个商品"),
            AIMessage(content="使用出库总量 / 当前库存计算库存周转率。"),
            HumanMessage(content="库存不为0的周转率最差的三个商品"),
        ],
        current_focus_result={
            "query": "近180天内库存周转率最差的三个商品",
            "columns": ["货号", "出库总量", "当前库存", "库存周转率"],
            "summary": "数据引用: [sql_clean_jf_out_store_detail_clean_jf_now_details_r1] (共 3 行)",
        },
        execution_results=[
            {
                "worker": "sql_worker",
                "result": (
                    "SQL: SELECT ... FROM clean_jf_out_store_detail o "
                    "JOIN clean_jf_now_details n ON ... ORDER BY 库存周转率 ASC LIMIT 3\n\n"
                    "改写后的问题: 查询近180天库存周转率最差的三个商品\n"
                    "实际引用表: clean_jf_out_store_detail, clean_jf_now_details"
                ),
            }
        ],
    )

    assert "近180天" in context
    assert "出库总量 / 当前库存" in context
    assert "当前库存 > 0" in context
    assert "不能把排序指标改成“当前库存最少”" in context
    assert "clean_jf_out_store_detail" in context
    assert "clean_jf_now_details" in context


def test_validate_inventory_turnover_sql_accepts_positive_inventory_ascending_rate():
    sql = """
    SELECT n.goods_no, SUM(o.out_qty) AS 出库总量, n.number AS 当前库存,
           SUM(o.out_qty) / n.number AS 库存周转率
    FROM clean_jf_out_store_detail o
    JOIN clean_jf_now_details n ON o.goods_no = n.goods_no
    WHERE o.out_date >= DATE_SUB(CURRENT_DATE, INTERVAL 180 DAY)
      AND n.number > 0
    GROUP BY n.goods_no, n.number
    ORDER BY 库存周转率 ASC
    LIMIT 3
    """

    assert validate_inventory_turnover_sql("近180天库存周转率最差的三个商品", sql) == []


def test_validate_inventory_turnover_sql_rejects_inventory_quantity_sort():
    sql = """
    SELECT goods_no, number AS 当前库存
    FROM clean_jf_now_details
    WHERE number > 0
    ORDER BY number ASC
    LIMIT 3
    """

    issues = validate_inventory_turnover_sql("库存不为0的周转率最差的三个商品", sql)

    assert any("周转率计算" in issue for issue in issues)
    assert any("ORDER BY 未按库存周转率排序" in issue for issue in issues)
    assert any("库存数量" in issue for issue in issues)


def test_validate_inventory_turnover_sql_rejects_zero_inventory_null_ranking():
    sql = """
    SELECT goods_no, SUM(out_qty) / NULLIF(number, 0) AS 库存周转率
    FROM clean_jf_now_details
    GROUP BY goods_no, number
    ORDER BY 库存周转率 ASC
    LIMIT 3
    """

    issues = validate_inventory_turnover_sql("近180天库存周转率最差的三个商品", sql)

    assert any("当前库存 > 0" in issue for issue in issues)


def test_validate_single_select_sql_blocks_unsafe_legacy_sql():
    assert _validate_single_select_sql("SELECT * FROM orders LIMIT 10") is None
    assert "只允许执行 SELECT" in _validate_single_select_sql("DELETE FROM orders")
    assert "多语句" in _validate_single_select_sql("SELECT * FROM orders; SELECT 1")


@pytest.mark.asyncio
async def test_sql_tool_passes_followup_context_to_worker(monkeypatch):
    captured = {}

    class FakeWorker:
        async def execute_task(self, **kwargs):
            captured.update(kwargs)
            return {"success": True, "row_count": 0, "artifacts": {}, "data": [], "columns": []}

        def format_result_for_synthesizer(self, result):
            return "ok"

    monkeypatch.setattr("app.agents.sql_worker.get_sql_worker", lambda: FakeWorker())

    await sql_tool_module.run_sql_task(
        query="库存不为0的周转率最差的三个商品",
        user_id="u-1",
        messages=[HumanMessage(content="近180天库存周转率最差的三个商品")],
        summary="上一轮是近180天库存周转率。",
        current_focus_result={"columns": ["库存周转率"]},
        execution_results=[{"worker": "sql_worker", "result": "SQL: SELECT 1"}],
        round_index=2,
    )

    assert captured["messages"]
    assert captured["summary"] == "上一轮是近180天库存周转率。"
    assert captured["current_focus_result"]["columns"] == ["库存周转率"]
    assert captured["execution_results"][0]["worker"] == "sql_worker"


@pytest.mark.asyncio
async def test_direct_sql_worker_passes_followup_context(monkeypatch):
    captured = {}

    class FakeWorker:
        async def execute_task(self, **kwargs):
            captured.update(kwargs)
            return {"success": True, "row_count": 0, "artifacts": {}, "data": [], "columns": []}

        def format_result_for_synthesizer(self, result):
            return "ok"

    monkeypatch.setattr(direct_execute_module, "get_sql_worker", lambda: FakeWorker())

    result = await direct_execute_module._execute_worker(
        worker_type="sql_worker",
        query="库存不为0的周转率最差的三个商品",
        user_context={"user_id": "u-1"},
        session_id="s-1",
        parent_step_id="direct_1",
        memory_dfs={},
        round_index=2,
        messages=[HumanMessage(content="近180天库存周转率最差的三个商品")],
        summary="上一轮是近180天库存周转率。",
        current_focus_result={"columns": ["库存周转率"]},
        execution_results=[{"worker": "sql_worker", "result": "SQL: SELECT 1"}],
    )

    assert result["output"] == "ok"
    assert captured["summary"] == "上一轮是近180天库存周转率。"
    assert captured["current_focus_result"]["columns"] == ["库存周转率"]
    assert captured["execution_results"][0]["worker"] == "sql_worker"


@pytest.mark.asyncio
async def test_executor_passes_followup_context_to_sql_tool(monkeypatch):
    captured = {}

    class FakeTool:
        async def ainvoke(self, tool_args):
            captured.update(tool_args)
            return {
                "output": "ok",
                "artifacts": {},
                "quality_signal": {"verdict": "pass", "reason_code": "sql_ok"},
                "meta": {"worker_round": 2},
            }

    monkeypatch.setattr("app.tools.registry.get_tool", lambda worker: FakeTool())

    result = await executor_module.execute_worker_task(
        worker="sql_worker",
        description="库存不为0的周转率最差的三个商品",
        user_context={"user_id": "u-1"},
        session_id="s-1",
        parent_step_id="1",
        messages=[HumanMessage(content="近180天库存周转率最差的三个商品")],
        memory_dfs={},
        round_index=2,
        execution_results=[{"worker": "sql_worker", "result": "SQL: SELECT 1"}],
        summary="上一轮是近180天库存周转率。",
        current_focus_result={"columns": ["库存周转率"]},
    )

    assert result["result"] == "ok"
    assert captured["summary"] == "上一轮是近180天库存周转率。"
    assert captured["current_focus_result"]["columns"] == ["库存周转率"]
    assert captured["execution_results"][0]["worker"] == "sql_worker"
