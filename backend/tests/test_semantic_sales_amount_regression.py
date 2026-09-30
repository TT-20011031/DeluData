import asyncio
import unittest

from app.models.config.semantic import (
    SemanticColumn,
    SemanticDatasource,
    SemanticMetric,
    SemanticTable,
)
from app.services.semantic_query_service import (
    IntentQuery,
    SemanticCatalog,
    SemanticQueryService,
)


def _catalog() -> SemanticCatalog:
    datasource = SemanticDatasource(
        id=1,
        workspace_id="w1",
        name="demo",
        host="localhost",
        port=3306,
        database="demo",
        semantic_sql_enabled=True,
    )
    table = SemanticTable(
        id=1,
        workspace_id="w1",
        datasource_id=1,
        physical_name="jf_sale_order",
        business_name="销售订单主表",
        status="confirmed",
    )
    columns = [
        SemanticColumn(
            id=1,
            workspace_id="w1",
            datasource_id=1,
            table_id=1,
            physical_table=table.physical_name,
            physical_name="total_price",
            data_type="decimal",
            business_name="金额",
            synonyms=["总金额"],
            status="confirmed",
        ),
        SemanticColumn(
            id=2,
            workspace_id="w1",
            datasource_id=1,
            table_id=1,
            physical_table=table.physical_name,
            physical_name="make_date",
            data_type="date",
            business_name="制单日期",
            status="confirmed",
        ),
        SemanticColumn(
            id=3,
            workspace_id="w1",
            datasource_id=1,
            table_id=1,
            physical_table=table.physical_name,
            physical_name="order_date",
            data_type="date",
            business_name="下单日期",
            status="confirmed",
        ),
        SemanticColumn(
            id=4,
            workspace_id="w1",
            datasource_id=1,
            table_id=1,
            physical_table=table.physical_name,
            physical_name="update_by",
            data_type="varchar",
            business_name="更新人",
            status="confirmed",
        ),
    ]
    metric = SemanticMetric(
        id=1,
        workspace_id="w1",
        datasource_id=1,
        name="sales_amount",
        business_name="销售金额",
        description="按下单日期汇总销售订单金额",
        formula="SUM({total_price})",
        aggregation="sum",
        table_id=1,
        column_id=1,
        time_column_id=3,
        default_grain="month",
        synonyms=["销售额、订单销售额、销售总额"],
        status="confirmed",
    )
    return SemanticCatalog(
        datasource=datasource,
        tables=[table],
        columns=columns,
        metrics=[metric],
        relationships=[],
    )


def _admin_access() -> dict:
    return {
        "user_id": "u1",
        "role_ids": [],
        "role_names": [],
        "is_admin": False,
        "allowed_tables": set(),
        "semantic_access": {
            "is_admin": True,
            "effects_by_asset": {},
            "policy_ids": [],
        },
    }


class SemanticSalesAmountRegressionTests(unittest.TestCase):
    def test_explicit_metric_does_not_inherit_planner_amount_dimensions(self):
        service = SemanticQueryService()
        catalog = _catalog()

        intent = asyncio.run(service.extract_intent_hybrid(
            "本月销售金额有多少",
            catalog,
            context_hint="查询数据库中本月的销售总金额",
        ))
        plan = service.resolve_plan(intent, catalog, _admin_access())
        sql = service.compile_sql(plan, catalog)

        self.assertEqual(intent.metrics, ["sales_amount"])
        self.assertEqual(intent.dimensions, [])
        self.assertEqual(plan.column_ids, [])
        self.assertNotIn("GROUP BY", sql)

    def test_metric_time_binding_beats_sales_table_default_time_column(self):
        service = SemanticQueryService()
        catalog = _catalog()
        intent = IntentQuery(
            query_type="aggregate",
            metrics=["sales_amount"],
            time_range="本月",
        )

        plan = service.resolve_plan(intent, catalog, _admin_access())
        sql = service.compile_sql(plan, catalog)

        self.assertIn(
            "t0.`order_date` >= DATE_FORMAT(CURRENT_DATE, '%Y-%m-01')",
            sql,
        )
        self.assertNotIn(
            "t0.`make_date` >= DATE_FORMAT(CURRENT_DATE, '%Y-%m-01')",
            sql,
        )

    def test_non_time_metric_binding_does_not_override_valid_table_time(self):
        service = SemanticQueryService()
        catalog = _catalog()
        catalog.metrics[0].time_column_id = 4
        catalog.__post_init__()
        intent = IntentQuery(
            query_type="aggregate",
            metrics=["sales_amount"],
            time_range="本月",
        )

        plan = service.resolve_plan(intent, catalog, _admin_access())
        sql = service.compile_sql(plan, catalog)

        self.assertIn(
            "t0.`make_date` >= DATE_FORMAT(CURRENT_DATE, '%Y-%m-01')",
            sql,
        )
        self.assertNotIn("t0.`update_by` >=", sql)

    def test_composite_metric_synonyms_are_individually_matchable(self):
        service = SemanticQueryService()
        catalog = _catalog()
        catalog.metrics[0].business_name = "订单本币金额"
        catalog.__post_init__()

        intent = service.extract_intent("本月销售额有多少", catalog)

        self.assertEqual(intent.metrics, ["sales_amount"])
        self.assertEqual(intent.clarification_items, [])


if __name__ == "__main__":
    unittest.main()
