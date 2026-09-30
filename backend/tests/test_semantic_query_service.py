from __future__ import annotations

import asyncio
import pytest
from importlib.util import find_spec

_DEPS_AVAILABLE = find_spec("chromadb") is not None and find_spec("sqlglot") is not None

pytestmark = pytest.mark.skipif(
    not _DEPS_AVAILABLE,
    reason="semantic query service tests require full backend query dependencies",
)

if _DEPS_AVAILABLE:
    from app.models.config.semantic import (  # noqa: E402
        SemanticColumn,
        SemanticDatasource,
        SemanticMetric,
        SemanticRelationship,
        SemanticTable,
    )
    from app.services.semantic_query_service import (  # noqa: E402
        IntentQuery,
        SemanticCatalog,
        SemanticQueryError,
        SemanticQueryService,
        _is_sensitive_column_name,
        _active_scan_objects,
        _suggest_metrics_for_columns,
        _suggest_relationships_for_columns,
        _suggest_business_name,
        _should_auto_disable_table,
    )


def _catalog(*, sensitive_customer: bool = False) -> SemanticCatalog:
    datasource = SemanticDatasource(
        id=1,
        workspace_id="w1",
        name="demo",
        host="localhost",
        port=3306,
        database="demo",
        semantic_sql_enabled=True,
    )
    orders = SemanticTable(
        id=1,
        workspace_id="w1",
        datasource_id=1,
        physical_name="orders",
        business_name="订单",
        status="confirmed",
    )
    customer = SemanticColumn(
        id=1,
        workspace_id="w1",
        datasource_id=1,
        table_id=1,
        physical_table="orders",
        physical_name="customer_name",
        business_name="客户",
        status="confirmed",
        is_sensitive=sensitive_customer,
    )
    amount = SemanticColumn(
        id=2,
        workspace_id="w1",
        datasource_id=1,
        table_id=1,
        physical_table="orders",
        physical_name="amount",
        business_name="销售额",
        status="confirmed",
    )
    order_date = SemanticColumn(
        id=3,
        workspace_id="w1",
        datasource_id=1,
        table_id=1,
        physical_table="orders",
        physical_name="order_date",
        business_name="下单日期",
        status="confirmed",
    )
    metric = SemanticMetric(
        id=1,
        workspace_id="w1",
        datasource_id=1,
        name="sales_amount",
        business_name="销售额",
        formula="SUM({销售额})",
        table_id=1,
        time_column_id=3,
        status="confirmed",
    )
    return SemanticCatalog(
        datasource=datasource,
        tables=[orders],
        columns=[customer, amount, order_date],
        metrics=[metric],
        relationships=[],
    )


def _sales_catalog() -> SemanticCatalog:
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
        physical_name="clean_jf_sale_order",
        business_name="销售订单主表",
        synonyms=["销售订单表", "销售订单"],
        status="confirmed",
    )
    columns = [
        SemanticColumn(id=1, workspace_id="w1", datasource_id=1, table_id=1, physical_table=table.physical_name, physical_name="sale_order_no", data_type="varchar", business_name="订单号", synonyms=["销售订单号"], status="confirmed"),
        SemanticColumn(id=2, workspace_id="w1", datasource_id=1, table_id=1, physical_table=table.physical_name, physical_name="customer_name", data_type="varchar", business_name="客户名称", synonyms=["客户"], status="confirmed"),
        SemanticColumn(id=3, workspace_id="w1", datasource_id=1, table_id=1, physical_table=table.physical_name, physical_name="money_sum", data_type="decimal", business_name="含税总金额", synonyms=["销售金额", "总金额"], status="confirmed"),
        SemanticColumn(id=4, workspace_id="w1", datasource_id=1, table_id=1, physical_table=table.physical_name, physical_name="make_date", data_type="date", business_name="制单日期", synonyms=["订单日期"], status="confirmed"),
        SemanticColumn(id=5, workspace_id="w1", datasource_id=1, table_id=1, physical_table=table.physical_name, physical_name="order_last_date", data_type="date", business_name="交货日期", status="confirmed"),
        SemanticColumn(id=6, workspace_id="w1", datasource_id=1, table_id=1, physical_table=table.physical_name, physical_name="status", data_type="varchar", business_name="完成状态", synonyms=["订单状态"], status="confirmed"),
    ]
    metrics = [
        SemanticMetric(
            id=1,
            workspace_id="w1",
            datasource_id=1,
            name="sales_amount",
            business_name="销售金额",
            formula="SUM({money_sum})",
            table_id=1,
            column_id=3,
            time_column_id=4,
            default_grain="month",
            synonyms=["销售额", "总金额"],
            status="confirmed",
        ),
        SemanticMetric(
            id=2,
            workspace_id="w1",
            datasource_id=1,
            name="sales_order_count",
            business_name="销售订单数",
            formula="COUNT(DISTINCT {sale_order_no})",
            table_id=1,
            column_id=1,
            time_column_id=4,
            default_grain="month",
            synonyms=["订单数量"],
            status="confirmed",
        ),
    ]
    return SemanticCatalog(datasource=datasource, tables=[table], columns=columns, metrics=metrics, relationships=[])


def _planner_conflict_catalog() -> SemanticCatalog:
    datasource = SemanticDatasource(id=1, workspace_id="w1", name="demo", host="localhost", port=3306, database="demo", semantic_sql_enabled=True)
    detail = SemanticTable(id=27, workspace_id="w1", datasource_id=1, physical_name="clean_jf_order_detail", business_name="订单详情表", status="confirmed")
    sale = SemanticTable(id=33, workspace_id="w1", datasource_id=1, physical_name="clean_jf_sale_order", business_name="销售订单表", status="confirmed")
    proofing = SemanticTable(id=48, workspace_id="w1", datasource_id=1, physical_name="clean_make_order_proofing_detail", business_name="打样办单明细表", status="confirmed")
    columns = [
        SemanticColumn(id=2701, workspace_id="w1", datasource_id=1, table_id=27, physical_table=detail.physical_name, physical_name="detail_amount", data_type="decimal", business_name="订单明细金额", synonyms=["销售金额"], status="confirmed"),
        SemanticColumn(id=3301, workspace_id="w1", datasource_id=1, table_id=33, physical_table=sale.physical_name, physical_name="money_sum", data_type="decimal", business_name="含税总金额", synonyms=["销售金额"], status="confirmed"),
        SemanticColumn(id=3302, workspace_id="w1", datasource_id=1, table_id=33, physical_table=sale.physical_name, physical_name="customer_name", data_type="varchar", business_name="客户名称", synonyms=["客户"], status="confirmed"),
        SemanticColumn(id=3303, workspace_id="w1", datasource_id=1, table_id=33, physical_table=sale.physical_name, physical_name="make_date", data_type="date", business_name="制单日期", synonyms=["订单日期"], status="confirmed"),
        SemanticColumn(id=4801, workspace_id="w1", datasource_id=1, table_id=48, physical_table=proofing.physical_name, physical_name="proofing_amount", data_type="decimal", business_name="打样金额", synonyms=["金额"], status="confirmed"),
        SemanticColumn(id=4802, workspace_id="w1", datasource_id=1, table_id=48, physical_table=proofing.physical_name, physical_name="customer_name", data_type="varchar", business_name="客户名称", synonyms=["客户"], status="confirmed"),
    ]
    metrics = [
        SemanticMetric(id=2701, workspace_id="w1", datasource_id=1, name="sales_detail_amount", business_name="订单明细金额", formula="SUM({detail_amount})", table_id=27, column_id=2701, synonyms=["销售金额"], status="confirmed", confidence=1),
        SemanticMetric(id=3301, workspace_id="w1", datasource_id=1, name="sales_amount", business_name="销售金额", formula="SUM({money_sum})", table_id=33, column_id=3301, time_column_id=3303, default_grain="month", status="confirmed", confidence=1),
        SemanticMetric(id=4801, workspace_id="w1", datasource_id=1, name="proofing_amount", business_name="打样金额", formula="SUM({proofing_amount})", table_id=48, column_id=4801, synonyms=["金额"], status="confirmed", confidence=1),
    ]
    return SemanticCatalog(datasource=datasource, tables=[detail, sale, proofing], columns=columns, metrics=metrics, relationships=[])


def _regression_catalog() -> SemanticCatalog:
    datasource = SemanticDatasource(
        id=1,
        workspace_id="w1",
        name="demo",
        host="localhost",
        port=3306,
        database="demo",
        semantic_sql_enabled=True,
    )
    inventory = SemanticTable(id=1, workspace_id="w1", datasource_id=1, physical_name="clean_jf_now_details", business_name="当前库存明细表", synonyms=["当前库存"], status="confirmed")
    task = SemanticTable(id=2, workspace_id="w1", datasource_id=1, physical_name="clean_produce_task", business_name="生产任务表", synonyms=["生产任务"], status="confirmed")
    inspection = SemanticTable(id=3, workspace_id="w1", datasource_id=1, physical_name="clean_dz_inspection_work", business_name="设备点检记录表", synonyms=["设备点检"], status="confirmed")
    proofing = SemanticTable(id=4, workspace_id="w1", datasource_id=1, physical_name="clean_make_order_proofing", business_name="打样办单主表", synonyms=["打样办单"], status="confirmed")
    columns = [
        SemanticColumn(id=1, workspace_id="w1", datasource_id=1, table_id=1, physical_table=inventory.physical_name, physical_name="mname", data_type="varchar", business_name="物料名称", synonyms=["品名"], status="confirmed"),
        SemanticColumn(id=2, workspace_id="w1", datasource_id=1, table_id=1, physical_table=inventory.physical_name, physical_name="item_name", data_type="varchar", business_name="品名", status="confirmed"),
        SemanticColumn(id=3, workspace_id="w1", datasource_id=1, table_id=1, physical_table=inventory.physical_name, physical_name="mtype", data_type="varchar", business_name="规格", status="confirmed"),
        SemanticColumn(id=4, workspace_id="w1", datasource_id=1, table_id=1, physical_table=inventory.physical_name, physical_name="colour", data_type="varchar", business_name="颜色", status="confirmed"),
        SemanticColumn(id=5, workspace_id="w1", datasource_id=1, table_id=1, physical_table=inventory.physical_name, physical_name="colour_no", data_type="varchar", business_name="色号", status="confirmed"),
        SemanticColumn(id=6, workspace_id="w1", datasource_id=1, table_id=1, physical_table=inventory.physical_name, physical_name="number", data_type="decimal", business_name="当前库存数量", status="confirmed"),
        SemanticColumn(id=10, workspace_id="w1", datasource_id=1, table_id=2, physical_table=task.physical_name, physical_name="document_no", data_type="varchar", business_name="生产任务单号", status="confirmed"),
        SemanticColumn(id=11, workspace_id="w1", datasource_id=1, table_id=2, physical_table=task.physical_name, physical_name="plan_finish_date", data_type="date", business_name="计划完工日期", status="confirmed"),
        SemanticColumn(id=12, workspace_id="w1", datasource_id=1, table_id=2, physical_table=task.physical_name, physical_name="finish_status", data_type="varchar", business_name="完工状态", synonyms=["完成状态"], status="confirmed"),
        SemanticColumn(id=20, workspace_id="w1", datasource_id=1, table_id=3, physical_table=inspection.physical_name, physical_name="billno", data_type="varchar", business_name="点检单号", status="confirmed"),
        SemanticColumn(id=21, workspace_id="w1", datasource_id=1, table_id=3, physical_table=inspection.physical_name, physical_name="workshop_name", data_type="varchar", business_name="车间", status="confirmed"),
        SemanticColumn(id=22, workspace_id="w1", datasource_id=1, table_id=3, physical_table=inspection.physical_name, physical_name="device_name", data_type="varchar", business_name="设备名称", synonyms=["设备"], status="confirmed"),
        SemanticColumn(id=23, workspace_id="w1", datasource_id=1, table_id=3, physical_table=inspection.physical_name, physical_name="inspection_result", data_type="varchar", business_name="点检结果", status="confirmed"),
        SemanticColumn(id=24, workspace_id="w1", datasource_id=1, table_id=3, physical_table=inspection.physical_name, physical_name="is_repair", data_type="varchar", business_name="是否报修", status="confirmed"),
        SemanticColumn(id=25, workspace_id="w1", datasource_id=1, table_id=3, physical_table=inspection.physical_name, physical_name="inspection_finish_time", data_type="datetime", business_name="点检完成时间", status="confirmed"),
        SemanticColumn(id=30, workspace_id="w1", datasource_id=1, table_id=4, physical_table=proofing.physical_name, physical_name="make_order_no", data_type="varchar", business_name="办单单号", status="confirmed"),
        SemanticColumn(id=31, workspace_id="w1", datasource_id=1, table_id=4, physical_table=proofing.physical_name, physical_name="customer_name", data_type="varchar", business_name="客户名称", status="confirmed"),
        SemanticColumn(id=32, workspace_id="w1", datasource_id=1, table_id=4, physical_table=proofing.physical_name, physical_name="require_date", data_type="date", business_name="要求交期", status="confirmed"),
        SemanticColumn(id=33, workspace_id="w1", datasource_id=1, table_id=4, physical_table=proofing.physical_name, physical_name="urgent_order", data_type="varchar", business_name="急单标记", synonyms=["急单"], status="confirmed"),
        SemanticColumn(id=34, workspace_id="w1", datasource_id=1, table_id=4, physical_table=proofing.physical_name, physical_name="finish_status", data_type="varchar", business_name="完成状态", status="confirmed"),
    ]
    metrics = [
        SemanticMetric(id=1, workspace_id="w1", datasource_id=1, name="current_inventory_quantity", business_name="当前库存数量", formula="SUM({number})", table_id=1, column_id=6, synonyms=["库存数量"], status="confirmed"),
        SemanticMetric(id=2, workspace_id="w1", datasource_id=1, name="inspection_exception_count", business_name="点检异常记录数", formula="COUNT(DISTINCT {billno})", table_id=3, column_id=20, time_column_id=25, synonyms=["异常记录数"], status="confirmed"),
    ]
    return SemanticCatalog(datasource=datasource, tables=[inventory, task, inspection, proofing], columns=columns, metrics=metrics, relationships=[])


def _follow_catalog(*, sensitive_person: bool = False, ambiguous_time: bool = False) -> SemanticCatalog:
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
        physical_name="clean_clue_follow_records",
        business_name="订单跟进记录表",
        description="存储客户或线索的跟进详情",
        synonyms=["客户跟进记录", "销售跟进日志"],
        status="confirmed",
    )
    columns = [
        SemanticColumn(
            id=1,
            workspace_id="w1",
            datasource_id=1,
            table_id=1,
            physical_table="clean_clue_follow_records",
            physical_name="id",
            business_name="记录ID",
            data_type="int",
            status="confirmed",
        ),
        SemanticColumn(
            id=2,
            workspace_id="w1",
            datasource_id=1,
            table_id=1,
            physical_table="clean_clue_follow_records",
            physical_name="follow_person",
            business_name="跟进人",
            data_type="varchar",
            synonyms=["负责人"],
            status="confirmed",
            is_sensitive=sensitive_person,
        ),
        SemanticColumn(
            id=3,
            workspace_id="w1",
            datasource_id=1,
            table_id=1,
            physical_table="clean_clue_follow_records",
            physical_name="follow_time",
            business_name="跟进时间",
            data_type="datetime",
            synonyms=["联系时间"],
            status="confirmed",
        ),
        SemanticColumn(
            id=4,
            workspace_id="w1",
            datasource_id=1,
            table_id=1,
            physical_table="clean_clue_follow_records",
            physical_name="follow_content",
            business_name="跟进内容",
            data_type="text",
            status="confirmed",
        ),
    ]
    if ambiguous_time:
        columns.append(
            SemanticColumn(
                id=5,
                workspace_id="w1",
                datasource_id=1,
                table_id=1,
                physical_table="clean_clue_follow_records",
                physical_name="next_visit_time",
                business_name="下次访问时间",
                data_type="datetime",
                status="confirmed",
            )
        )
    return SemanticCatalog(
        datasource=datasource,
        tables=[table],
        columns=columns,
        metrics=[],
        relationships=[],
    )


def _follow_conflict_catalog() -> SemanticCatalog:
    catalog = _follow_catalog()
    catalog.tables[0].business_name = "\u8ba2\u5355\u8ddf\u8fdb\u8bb0\u5f55\u8868"
    catalog.tables[0].synonyms = ["\u8ddf\u8fdb\u8bb0\u5f55", "\u8ba2\u5355\u8ddf\u8fdb"]
    follow_names = {
        "id": "\u8bb0\u5f55ID",
        "follow_person": "\u8ddf\u8fdb\u4eba",
        "follow_time": "\u8ddf\u8fdb\u65f6\u95f4",
        "follow_content": "\u8ddf\u8fdb\u5185\u5bb9",
        "follow_method": "\u8ddf\u8fdb\u65b9\u5f0f",
        "follow_record": "\u8ddf\u8fdb\u8bb0\u5f55\u8be6\u60c5",
        "main_code": "\u5173\u8054\u4e3b\u7801",
        "flag": "\u4e1a\u52a1\u7c7b\u578b\u6807\u8bc6",
        "next_visit_time": "\u4e0b\u6b21\u8bbf\u95ee\u65f6\u95f4",
    }
    for column in catalog.columns:
        if column.physical_name in follow_names:
            column.business_name = follow_names[column.physical_name]
            if column.physical_name == "follow_person":
                column.synonyms = ["\u8d1f\u8d23\u4eba"]
    dispatch = SemanticTable(
        id=2,
        workspace_id="w1",
        datasource_id=1,
        physical_name="clean_dispatch_order",
        business_name="\u6d3e\u5de5\u8ba2\u5355",
        synonyms=["\u8ba2\u5355"],
        status="confirmed",
    )
    sales = SemanticTable(
        id=3,
        workspace_id="w1",
        datasource_id=1,
        physical_name="clean_jf_sale_order",
        business_name="\u9500\u552e\u8ba2\u5355\u8868",
        synonyms=["\u8ba2\u5355", "\u9500\u552e\u8ba2\u5355"],
        status="confirmed",
    )
    catalog.tables.extend([dispatch, sales])
    catalog.columns.extend(
        [
            SemanticColumn(id=20, workspace_id="w1", datasource_id=1, table_id=2, physical_table=dispatch.physical_name, physical_name="document_no", data_type="varchar", business_name="\u8ba2\u5355\u53f7", status="confirmed"),
            SemanticColumn(id=30, workspace_id="w1", datasource_id=1, table_id=3, physical_table=sales.physical_name, physical_name="sale_order_no", data_type="varchar", business_name="\u8ba2\u5355\u53f7", status="confirmed"),
            SemanticColumn(id=31, workspace_id="w1", datasource_id=1, table_id=3, physical_table=sales.physical_name, physical_name="customer_name", data_type="varchar", business_name="\u5ba2\u6237\u540d\u79f0", status="confirmed"),
        ]
    )
    catalog.__post_init__()
    return catalog


def _turnover_catalog() -> SemanticCatalog:
    datasource = SemanticDatasource(
        id=1,
        workspace_id="w1",
        name="demo",
        host="localhost",
        port=3306,
        database="demo",
        semantic_sql_enabled=True,
    )
    inventory = SemanticTable(
        id=1,
        workspace_id="w1",
        datasource_id=1,
        physical_name="clean_jf_now_details",
        business_name="\u5f53\u524d\u5e93\u5b58\u660e\u7ec6\u8868",
        synonyms=["\u5f53\u524d\u5e93\u5b58", "\u5e93\u5b58"],
        status="confirmed",
    )
    outbound = SemanticTable(
        id=2,
        workspace_id="w1",
        datasource_id=1,
        physical_name="clean_jf_out_store_detail",
        business_name="\u51fa\u5e93\u660e\u7ec6\u8868",
        synonyms=["\u51fa\u5e93"],
        status="confirmed",
    )
    columns = [
        SemanticColumn(id=1, workspace_id="w1", datasource_id=1, table_id=1, physical_table=inventory.physical_name, physical_name="item_name", data_type="varchar", business_name="\u54c1\u540d", synonyms=["\u5546\u54c1", "\u7269\u6599"], status="confirmed"),
        SemanticColumn(id=2, workspace_id="w1", datasource_id=1, table_id=1, physical_table=inventory.physical_name, physical_name="number", data_type="decimal", business_name="\u5f53\u524d\u5e93\u5b58\u6570\u91cf", status="confirmed"),
        SemanticColumn(id=3, workspace_id="w1", datasource_id=1, table_id=2, physical_table=outbound.physical_name, physical_name="item_name", data_type="varchar", business_name="\u54c1\u540d", synonyms=["\u5546\u54c1", "\u7269\u6599"], status="confirmed"),
        SemanticColumn(id=4, workspace_id="w1", datasource_id=1, table_id=2, physical_table=outbound.physical_name, physical_name="out_store_num", data_type="decimal", business_name="\u51fa\u5e93\u6570\u91cf", status="confirmed"),
        SemanticColumn(id=5, workspace_id="w1", datasource_id=1, table_id=2, physical_table=outbound.physical_name, physical_name="create_time", data_type="datetime", business_name="\u521b\u5efa\u65f6\u95f4", status="confirmed"),
    ]
    metrics = [
        SemanticMetric(id=1, workspace_id="w1", datasource_id=1, name="current_inventory_quantity", business_name="\u5f53\u524d\u5e93\u5b58\u6570\u91cf", formula="SUM({number})", table_id=1, column_id=2, synonyms=["\u5e93\u5b58\u6570\u91cf"], status="confirmed"),
        SemanticMetric(id=2, workspace_id="w1", datasource_id=1, name="outbound_quantity", business_name="\u51fa\u5e93\u6570\u91cf", formula="SUM({out_store_num})", table_id=2, column_id=4, time_column_id=5, synonyms=["\u51fa\u5e93\u603b\u91cf"], status="confirmed"),
    ]
    return SemanticCatalog(datasource=datasource, tables=[inventory, outbound], columns=columns, metrics=metrics, relationships=[])


def _access(**overrides):
    effects_by_asset = {
        f"table:{table_id}": [
            {
                "policy_id": 1,
                "effect_type": "visible",
                "asset_type": "table",
                "asset_id": table_id,
                "subject_type": "all",
                "subject_id": "*",
                "condition_json": {"row_scope": {"type": "all"}},
            }
        ]
        for table_id in range(1, 20)
    }
    base = {
        "user_id": "u1",
        "role_ids": [],
        "role_names": [],
        "is_admin": False,
        "allowed_tables": set(),
        "semantic_access": {"effects_by_asset": effects_by_asset, "policy_ids": [1]},
    }
    base.update(overrides)
    return base


def _admin_access():
    access = _access()
    access["semantic_access"]["is_admin"] = True
    return access


def _access_with_restricted_metric(metric_id: int = 1):
    access = _access()
    access["semantic_access"]["effects_by_asset"][f"metric:{metric_id}"] = [
        {
            "policy_id": 10,
            "effect_type": "hidden",
            "asset_type": "metric",
            "asset_id": metric_id,
            "subject_type": "user",
            "subject_id": "u1",
        }
    ]
    access["semantic_access"]["policy_ids"].append(10)
    return access


def _access_with_follow_person_row_scope(value: str):
    access = _access()
    access["semantic_access"]["effects_by_asset"]["table:1"] = [
        {
            "policy_id": 20,
            "effect_type": "visible",
            "asset_type": "table",
            "asset_id": 1,
            "subject_type": "user",
            "subject_id": "u1",
            "condition_json": {"row_scope": {"type": "custom"}},
        },
        {
            "policy_id": 20,
            "effect_type": "row_filter",
            "asset_type": "table",
            "asset_id": 1,
            "subject_type": "user",
            "subject_id": "u1",
            "condition_json": {"column_id": 2, "operator": "=", "value": value},
        },
    ]
    access["semantic_access"]["policy_ids"].append(20)
    return access


def test_row_scope_conflict_reports_requested_person_as_unauthorized():
    service = SemanticQueryService()
    catalog = _follow_catalog()
    intent = IntentQuery(
        query_type="detail",
        tables=["订单跟进记录表"],
        dimensions=["跟进人", "跟进时间", "跟进内容"],
        filters=[{"field": "跟进人", "operator": "contains", "value": "张蒙"}],
        time_range="2025-03",
    )

    with pytest.raises(SemanticQueryError) as exc_info:
        service.resolve_plan(intent, catalog, _access_with_follow_person_row_scope("胡国粉"))

    assert exc_info.value.error_type == "permission_denied"
    assert exc_info.value.safe_to_fallback is False
    assert exc_info.value.message == "当前用户无权查询“张蒙”的订单跟进记录。"
    assert exc_info.value.details["reason"] == "row_scope_conflict"


@pytest.mark.parametrize(
    "question",
    [
        "张蒙2025年3月的订单跟进记录",
        "查询数据库中张蒙在2025年3月的订单跟进记录",
        "查询数据库内张蒙2025年3月的订单跟进记录",
        "从数据库里获取张蒙2025年3月的订单跟进记录",
        "[Router修复] sql execution failed，修复后重试：查询数据库中张蒙在2025年3月的订单跟进记录",
        "查询数据库，检索员工名为'张蒙'且订单跟进日期在2025年3月的所有订单跟进记录",
        "从数据库中检索张蒙在2025年3月的所有订单跟进记录",
        """用户当前问题:
查询数据库中张蒙在2025年3月的订单跟进记录

请先把当前问题理解为一次可能带有追问、省略和新增筛选条件的数据查询。

历史摘要:
无

最近对话:
- [用户] 张蒙2025年3月的订单跟进记录

完整 SQL 查询需求:
查询数据库中张蒙在2025年3月的订单跟进记录
""",
        "于敏2025年3月的订单跟进记录",
        "从数据库中获取于敏2025年3月的订单跟进记录",
    ],
)
def test_followup_person_filter_is_extracted_without_llm(question):
    intent = SemanticQueryService().extract_intent(question, _follow_catalog())

    assert intent.filters == [
        {
            "field": "跟进人",
            "operator": "contains",
            "value": "于敏" if "于敏" in question else "张蒙",
        }
    ]


@pytest.mark.parametrize("hidden_column_names", [("follow_method",), ("follow_method", "follow_content")])
def test_general_followup_query_silently_omits_implicit_hidden_columns(hidden_column_names):
    service = SemanticQueryService()
    catalog = _follow_catalog()
    catalog.columns.append(
        SemanticColumn(
            id=5,
            workspace_id="w1",
            datasource_id=1,
            table_id=1,
            physical_table="clean_clue_follow_records",
            physical_name="follow_method",
            business_name="跟进方式",
            data_type="varchar",
            status="confirmed",
        )
    )
    catalog.__post_init__()
    access = _access()
    physical_to_id = {column.physical_name: column.id for column in catalog.columns}
    for name in hidden_column_names:
        column_id = physical_to_id[name]
        access["semantic_access"]["effects_by_asset"][f"column:{column_id}"] = [
            {
                "policy_id": 30 + column_id,
                "effect_type": "hidden",
                "asset_type": "column",
                "asset_id": column_id,
                "subject_type": "user",
                "subject_id": "u1",
            }
        ]
        access["semantic_access"]["policy_ids"].append(30 + column_id)

    intent = service.extract_intent("张蒙2025年3月的订单跟进记录", catalog)
    plan = service.resolve_plan(intent, catalog, access)
    sql = service.compile_sql(plan, catalog)

    assert "follow_method" not in sql
    assert ("follow_content" not in sql) == ("follow_content" in hidden_column_names)
    assert "follow_person" in sql
    assert "%张蒙%" in sql


def test_explicit_hidden_followup_column_still_requires_rewrite_confirmation():
    service = SemanticQueryService()
    catalog = _follow_catalog()
    catalog.columns.append(
        SemanticColumn(
            id=5,
            workspace_id="w1",
            datasource_id=1,
            table_id=1,
            physical_table="clean_clue_follow_records",
            physical_name="follow_method",
            business_name="跟进方式",
            data_type="varchar",
            status="confirmed",
        )
    )
    catalog.__post_init__()
    access = _access()
    access["semantic_access"]["effects_by_asset"]["column:5"] = [
        {
            "policy_id": 35,
            "effect_type": "hidden",
            "asset_type": "column",
            "asset_id": 5,
            "subject_type": "user",
            "subject_id": "u1",
        }
    ]
    access["semantic_access"]["policy_ids"].append(35)
    intent = service.extract_intent(
        "张蒙2025年3月的订单跟进记录，请显示跟进方式",
        catalog,
    )

    with pytest.raises(SemanticQueryError) as exc_info:
        service.resolve_plan(intent, catalog, access)

    assert exc_info.value.error_type == "permission_rewrite_required"


def test_detail_plan_omits_implicit_hidden_dimension_for_any_table():
    service = SemanticQueryService()
    catalog = _catalog()
    access = _access()
    access["semantic_access"]["effects_by_asset"]["column:1"] = [
        {
            "policy_id": 41,
            "effect_type": "hidden",
            "asset_type": "column",
            "asset_id": 1,
            "subject_type": "user",
            "subject_id": "u1",
        }
    ]
    access["semantic_access"]["policy_ids"].append(41)
    intent = IntentQuery(
        query_type="detail",
        tables=["订单"],
        dimensions=["客户", "销售额"],
        explicit_dimensions=[],
    )

    plan = service.resolve_plan(intent, catalog, access)
    sql = service.compile_sql(plan, catalog)

    assert "customer_name" not in sql
    assert "amount" in sql
    assert any(
        action["action"] == "omit_implicit_hidden" and action["object_id"] == 1
        for action in plan.permission_actions
    )


def test_context_envelope_does_not_change_detail_query_into_count_query():
    intent = SemanticQueryService().extract_intent(
        """用户当前问题:
张蒙2025年3月的订单跟进记录

请先把当前问题理解为一次数据查询。

完整 SQL 查询需求:
查询张蒙在2025年3月的订单跟进记录
""",
        _follow_catalog(),
    )

    assert intent.query_type == "detail"
    assert intent.metrics == []


def test_scan_classifies_physical_metadata_without_confirmation():
    assert _should_auto_disable_table("clean_clue_follow_records_bak20260511")
    assert _should_auto_disable_table("tmp_orders")
    assert not _should_auto_disable_table("clean_dispatch_order")
    assert _is_sensitive_column_name("mobile_phone")
    assert _is_sensitive_column_name("身份证号")
    assert not _is_sensitive_column_name("order_status")


def test_rule_business_suggestion_translates_common_identifiers():
    table = _suggest_business_name("clean_clue_follow_records", object_type="table")
    column = _suggest_business_name("follow_time", object_type="column")

    assert table["business_name"] == "线索跟进记录"
    assert column["business_name"] == "跟进时间"
    assert "follow_time" in column["synonyms"]


def test_semantic_scan_metric_rules_skip_dimensions_and_generate_business_measures():
    columns = [
        SemanticColumn(id=1, workspace_id="w1", datasource_id=1, table_id=1, physical_table="orders", physical_name="id", data_type="varchar", business_name="ID"),
        SemanticColumn(id=2, workspace_id="w1", datasource_id=1, table_id=1, physical_table="orders", physical_name="serial_number", data_type="int", business_name="serial"),
        SemanticColumn(id=3, workspace_id="w1", datasource_id=1, table_id=1, physical_table="orders", physical_name="category", data_type="int", business_name="category"),
        SemanticColumn(id=4, workspace_id="w1", datasource_id=1, table_id=1, physical_table="orders", physical_name="total_price", data_type="decimal", business_name="total price"),
        SemanticColumn(id=5, workspace_id="w1", datasource_id=1, table_id=1, physical_table="orders", physical_name="quantity", data_type="decimal", business_name="quantity"),
        SemanticColumn(id=6, workspace_id="w1", datasource_id=1, table_id=1, physical_table="orders", physical_name="single_salary", data_type="decimal", business_name="single salary"),
        SemanticColumn(id=7, workspace_id="w1", datasource_id=1, table_id=1, physical_table="orders", physical_name="order_number", data_type="varchar", business_name="order number"),
    ]

    suggestions = _suggest_metrics_for_columns({1: columns})
    formulas = [item.formula for item in suggestions]

    assert not any("serial_number" in formula for formula in formulas)
    assert not any("category" in formula for formula in formulas)
    assert "SUM({total_price})" in formulas
    assert "SUM({quantity})" in formulas
    assert "AVG({single_salary})" in formulas
    assert "COUNT(DISTINCT {order_number})" in formulas
    assert "SUM({total_price}) / NULLIF(SUM({quantity}), 0)" in formulas


def test_semantic_scan_metric_rules_generate_unqualified_rate():
    columns = [
        SemanticColumn(id=1, workspace_id="w1", datasource_id=1, table_id=1, physical_table="inspection", physical_name="unqualified_number", data_type="decimal", business_name="unqualified"),
        SemanticColumn(id=2, workspace_id="w1", datasource_id=1, table_id=1, physical_table="inspection", physical_name="accept_number", data_type="decimal", business_name="accept"),
    ]

    suggestions = _suggest_metrics_for_columns({1: columns})

    assert any(item.business_name == "不合格率" and item.status == "confirmed" for item in suggestions)
    assert any(item.formula == "SUM({unqualified_number}) / NULLIF(SUM({accept_number}), 0)" for item in suggestions)


def test_semantic_scan_suggestions_skip_disabled_tables():
    active = SemanticTable(id=1, workspace_id="w1", datasource_id=1, physical_name="orders", business_name="orders", status="confirmed", is_queryable=True)
    disabled = SemanticTable(id=2, workspace_id="w1", datasource_id=1, physical_name="orders_bak20260511", business_name="orders bak", status="disabled", is_queryable=False)
    active_column = SemanticColumn(id=1, workspace_id="w1", datasource_id=1, table_id=1, physical_table="orders", physical_name="amount", data_type="decimal", business_name="amount", status="confirmed", is_queryable=True)
    disabled_column = SemanticColumn(id=2, workspace_id="w1", datasource_id=1, table_id=2, physical_table="orders_bak20260511", physical_name="amount", data_type="decimal", business_name="amount", status="confirmed", is_queryable=True)

    active_tables, columns_by_table = _active_scan_objects([active, disabled], [active_column, disabled_column])
    suggestions = _suggest_metrics_for_columns(columns_by_table)

    assert active_tables == [active]
    assert set(columns_by_table) == {1}
    assert all("orders_bak20260511" not in item.name for item in suggestions)
    assert any(item.formula == "SUM({amount})" for item in suggestions)


def test_semantic_scan_relationship_rules_confirm_parent_detail_only():
    parent = SemanticTable(id=1, workspace_id="w1", datasource_id=1, physical_name="clean_make_order_proofing", business_name="proofing")
    detail = SemanticTable(id=2, workspace_id="w1", datasource_id=1, physical_name="clean_make_order_proofing_detail", business_name="proofing detail")
    customer = SemanticTable(id=3, workspace_id="w1", datasource_id=1, physical_name="clean_jf_sales_customer", business_name="customer")
    order = SemanticTable(id=4, workspace_id="w1", datasource_id=1, physical_name="clean_jf_sale_order", business_name="order")
    columns_by_table = {
        1: [SemanticColumn(id=10, workspace_id="w1", datasource_id=1, table_id=1, physical_table=parent.physical_name, physical_name="make_order_no", data_type="varchar", business_name="make order no")],
        2: [SemanticColumn(id=20, workspace_id="w1", datasource_id=1, table_id=2, physical_table=detail.physical_name, physical_name="main_code", data_type="varchar", business_name="main code")],
        3: [SemanticColumn(id=30, workspace_id="w1", datasource_id=1, table_id=3, physical_table=customer.physical_name, physical_name="customer_code", data_type="varchar", business_name="customer code")],
        4: [SemanticColumn(id=40, workspace_id="w1", datasource_id=1, table_id=4, physical_table=order.physical_name, physical_name="customer_code", data_type="varchar", business_name="customer code")],
    }

    suggestions = _suggest_relationships_for_columns([parent, detail, customer, order], columns_by_table)
    parent_detail = [item for item in suggestions if item.left_table_id == 2 and item.right_table_id == 1]
    public_code = [item for item in suggestions if item.left_table_id == 4 and item.right_table_id == 3]

    assert parent_detail and parent_detail[0].status == "confirmed"
    assert parent_detail[0].confidence >= 0.9
    assert public_code and public_code[0].status == "suggested"


def test_semantic_metric_lookup_and_llm_candidates_ignore_disabled_table_metrics():
    service = SemanticQueryService()
    catalog = _catalog()
    disabled_table = SemanticTable(
        id=2,
        workspace_id="w1",
        datasource_id=1,
        physical_name="orders_bak20260511",
        business_name="订单备份",
        status="disabled",
        is_queryable=False,
    )
    disabled_metric = SemanticMetric(
        id=2,
        workspace_id="w1",
        datasource_id=1,
        name="backup_sales_amount",
        business_name="备份销售额",
        formula="SUM({amount})",
        table_id=2,
        status="confirmed",
        is_queryable=True,
        synonyms=["销售额"],
    )
    catalog.tables.append(disabled_table)
    catalog.metrics.append(disabled_metric)
    catalog.__post_init__()

    candidates = service._semantic_candidates_for_llm("查询销售额", catalog)

    assert service._find_metric("backup_sales_amount", catalog) is None
    assert service._find_metric("备份销售额", catalog) is None
    assert {item["name"] for item in candidates["metrics"]} == {"sales_amount"}


def test_semantic_dimension_resolution_prefers_metric_table_scope():
    service = SemanticQueryService()
    catalog = _catalog()
    other_table = SemanticTable(
        id=2,
        workspace_id="w1",
        datasource_id=1,
        physical_name="receipts",
        business_name="来料接收",
        status="confirmed",
    )
    other_customer = SemanticColumn(
        id=4,
        workspace_id="w1",
        datasource_id=1,
        table_id=2,
        physical_table="receipts",
        physical_name="customer_name",
        business_name="客户",
        status="confirmed",
    )
    catalog.tables.append(other_table)
    catalog.columns.append(other_customer)
    catalog.__post_init__()
    intent = IntentQuery(
        query_type="topn",
        metrics=["sales_amount"],
        dimensions=["客户"],
        order_by=[{"field": "sales_amount", "direction": "desc"}],
        limit=10,
    )

    plan = service.resolve_plan(intent, catalog, _access())

    assert plan.table_ids == [1]
    assert plan.column_ids == [1]


def test_semantic_topn_compiles_business_formula_to_physical_column():
    service = SemanticQueryService()
    catalog = _catalog()
    intent = IntentQuery(
        query_type="topn",
        metrics=["sales_amount"],
        dimensions=["客户"],
        order_by=[{"field": "sales_amount", "direction": "desc"}],
        limit=10,
    )

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert "t0.`amount`" in sql
    assert "`销售额`" in sql
    assert "GROUP BY t0.`customer_name`" in sql
    assert "ORDER BY `销售额` DESC" in sql
    assert "LIMIT 10" in sql
    assert plan.column_ids == [1]
    assert set(plan.permission_column_ids) == {1, 2, 3}
    service._validate_compiled_sql(sql)


def test_semantic_limit_phrase_does_not_force_topn():
    service = SemanticQueryService()
    catalog = _sales_catalog()
    intent = service.extract_intent("查询最近 30 天销售订单，最多 50 条", catalog)

    assert intent.query_type == "detail"
    assert intent.limit == 50
    assert intent.clarification_items == []


def test_semantic_trend_compiles_time_bucket_and_range():
    service = SemanticQueryService()
    catalog = _catalog()
    intent = service.extract_intent("最近3个月销售额趋势", catalog)

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert intent.query_type == "trend"
    assert "DATE_FORMAT(t0.`order_date`, '%Y-%m')" in sql
    assert "DATE_SUB(CURRENT_DATE, INTERVAL '3' MONTH)" in sql
    assert "t0.`amount`" in sql
    assert plan.column_ids == []
    assert set(plan.permission_column_ids) == {2, 3}
    service._validate_compiled_sql(sql)


def test_semantic_month_range_uses_metric_time_column():
    service = SemanticQueryService()
    catalog = _sales_catalog()
    intent = service.extract_intent("按月统计 2025 年 3 月到 2026 年 5 月的销售金额趋势", catalog)

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert intent.time_range == "2025-03..2026-05"
    assert "DATE_FORMAT(t0.`make_date`, '%Y-%m')" in sql
    assert "t0.`make_date` >= '2025-03-01'" in sql
    assert "t0.`make_date` < '2026-06-01'" in sql
    service._validate_compiled_sql(sql)


def test_semantic_compare_compiles_wide_month_delta():
    service = SemanticQueryService()
    catalog = _sales_catalog()
    intent = service.extract_intent("对比 2026 年 4 月和 5 月各客户的销售金额变化", catalog)

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert intent.query_type == "compare"
    assert intent.time_range == "2026-04..2026-05"
    assert "2026-04销售金额" in sql
    assert "2026-05销售金额" in sql
    assert "变化金额" in sql
    assert "变化率" in sql
    assert "GROUP BY t0.`customer_name`" in sql
    service._validate_compiled_sql(sql)


@pytest.mark.parametrize(
    ("question", "planner_rewrite", "expected_type"),
    [
        (
            "按月统计 2025 年 3 月到 2026 年 5 月的销售金额趋势",
            "查询并统计2025年3月到2026年5月各月的销售金额汇总数据",
            "trend",
        ),
        (
            "对比 2026 年 4 月和 5 月各客户的销售金额变化",
            "查询并统计2026年4月和5月各客户的销售金额，计算两个月的变化量及变化率",
            "compare",
        ),
    ],
)
def test_original_question_keeps_intent_and_metric_table_when_planner_rewrites(
    question: str,
    planner_rewrite: str,
    expected_type: str,
):
    service = SemanticQueryService()
    catalog = _planner_conflict_catalog()

    intent = asyncio.run(service.extract_intent_hybrid(question, catalog, context_hint=planner_rewrite))
    plan = service.resolve_plan(intent, catalog, _admin_access())

    assert intent.query_type == expected_type
    assert intent.metrics == ["sales_amount"]
    assert plan.table_ids == [33]
    assert plan.referenced_tables == ["clean_jf_sale_order"]
    assert intent.matching_trace
    assert "matching_trace" not in intent.model_dump(mode="json")


def test_context_hint_only_fills_missing_followup_fields():
    service = SemanticQueryService()
    catalog = _planner_conflict_catalog()

    intent = asyncio.run(service.extract_intent_hybrid(
        "那 2026 年 5 月呢",
        catalog,
        context_hint="按月统计销售金额趋势，时间为 2025 年 3 月到 2026 年 4 月",
    ))

    assert intent.query_type == "trend"
    assert intent.metrics == ["sales_amount"]
    assert intent.time_range == "2026-05"


def test_dimension_followup_uses_context_metric_without_cross_table_expansion():
    service = SemanticQueryService()
    catalog = _planner_conflict_catalog()

    intent = asyncio.run(service.extract_intent_hybrid(
        "再按客户呢",
        catalog,
        context_hint="统计 2026 年 5 月销售金额",
    ))
    plan = service.resolve_plan(intent, catalog, _admin_access())

    assert intent.metrics == ["sales_amount"]
    assert intent.dimensions == ["客户名称"]
    assert plan.table_ids == [33]


def test_business_name_metric_beats_synonym_and_generic_amount_does_not_expand_tables():
    service = SemanticQueryService()
    catalog = _planner_conflict_catalog()

    intent = service.extract_intent("统计销售金额", catalog)
    plan = service.resolve_plan(intent, catalog, _admin_access())

    assert intent.metrics == ["sales_amount"]
    assert plan.table_ids == [33]


def test_matching_diagnostics_report_generic_and_cross_table_terms_without_mutation():
    service = SemanticQueryService()
    catalog = _planner_conflict_catalog()
    before = [list(metric.synonyms) for metric in catalog.metrics]

    diagnostics = service.build_matching_diagnostics(catalog)

    assert any(item["type"] == "generic_term" and item["term"] == "金额" for item in diagnostics)
    assert any(item["type"] == "duplicate_term" and item["term"] == "销售金额" for item in diagnostics)
    assert [list(metric.synonyms) for metric in catalog.metrics] == before


def test_equal_rank_cross_table_metrics_require_clarification():
    service = SemanticQueryService()
    catalog = _planner_conflict_catalog()
    catalog.metrics[0].business_name = "销售金额"
    catalog.metrics[0].synonyms = []
    catalog.__post_init__()

    with pytest.raises(SemanticQueryError) as exc_info:
        service.extract_intent("统计销售金额", catalog)

    assert exc_info.value.error_type == "metric_ambiguous"
    assert set(exc_info.value.details["candidate_metric_ids"]) == {2701, 3301}


def test_metric_clarification_is_applied_during_extraction_and_preserves_other_metrics():
    service = SemanticQueryService()
    catalog = _sales_catalog()
    detail_table = SemanticTable(
        id=2,
        workspace_id="w1",
        datasource_id=1,
        physical_name="clean_jf_order_detail",
        business_name="订单详情表",
        status="confirmed",
    )
    catalog.tables.append(detail_table)
    catalog.columns.extend(
        [
            SemanticColumn(id=20, workspace_id="w1", datasource_id=1, table_id=2, physical_table=detail_table.physical_name, physical_name="quantity", data_type="decimal", business_name="销售数量", status="confirmed"),
            SemanticColumn(id=21, workspace_id="w1", datasource_id=1, table_id=2, physical_table=detail_table.physical_name, physical_name="detail_id", data_type="varchar", business_name="明细ID", status="confirmed"),
            SemanticColumn(id=22, workspace_id="w1", datasource_id=1, table_id=2, physical_table=detail_table.physical_name, physical_name="unit_price", data_type="decimal", business_name="销售单价", status="confirmed"),
        ]
    )
    catalog.metrics.extend(
        [
            SemanticMetric(id=20, workspace_id="w1", datasource_id=1, name="sales_detail_quantity", business_name="销售数量", formula="SUM({quantity})", table_id=2, column_id=20, synonyms=["订单数量"], status="confirmed"),
            SemanticMetric(id=21, workspace_id="w1", datasource_id=1, name="sales_order_line_count", business_name="销售明细行数", formula="COUNT(DISTINCT {detail_id})", table_id=2, column_id=21, synonyms=["明细数量"], status="confirmed"),
            SemanticMetric(id=22, workspace_id="w1", datasource_id=1, name="avg_unit_price_clean_jf_sale_order_1", business_name="平均单价", formula="AVG({unit_price})", table_id=2, column_id=22, status="confirmed"),
            SemanticMetric(id=23, workspace_id="w1", datasource_id=1, name="avg_order_amount", business_name="平均单价", formula="AVG({money_sum})", table_id=1, column_id=3, status="confirmed"),
        ]
    )
    catalog.__post_init__()
    question = "统计 2026 年 5 月销售订单总金额、订单数量、明细数量和平均单价"

    with pytest.raises(SemanticQueryError) as exc_info:
        service.extract_intent(question, catalog)

    clarification = service._clarification_from_error(
        exc_info.value,
        catalog,
        IntentQuery(),
        _admin_access(),
    )
    assert clarification is not None
    option = next(item for item in clarification.options if item.id == "metric:2")

    first_selection = {"selection_patch": option.selection_patch}
    with pytest.raises(SemanticQueryError) as second_exc_info:
        asyncio.run(
            service.extract_intent_hybrid(
                question,
                catalog,
                semantic_clarification=first_selection,
            )
        )
    assert second_exc_info.value.error_type == "metric_ambiguous"
    assert second_exc_info.value.details["matched_term"] == "平均单价"
    second_clarification = service._clarification_from_error(
        second_exc_info.value,
        catalog,
        IntentQuery(),
        _admin_access(),
    )
    assert second_clarification is not None
    second_clarification = service._carry_forward_metric_selections(
        second_clarification,
        first_selection,
    )
    avg_option = next(item for item in second_clarification.options if item.id == "metric:22")
    combined_selection = {"selection_patch": avg_option.selection_patch}

    selected = asyncio.run(
        service.extract_intent_hybrid(
            question,
            catalog,
            semantic_clarification=combined_selection,
        )
    )
    rewritten = service._apply_semantic_clarification(
        selected,
        catalog,
        combined_selection,
    )

    assert rewritten.query_type == "aggregate"
    assert rewritten.time_range == "2026-05"
    assert rewritten.metrics == [
        "sales_amount",
        "sales_order_count",
        "sales_order_line_count",
        "avg_unit_price_clean_jf_sale_order_1",
    ]

    detail_option = next(item for item in clarification.options if item.id == "metric:20")
    detail_first_selection = {"selection_patch": detail_option.selection_patch}
    with pytest.raises(SemanticQueryError) as detail_second_exc_info:
        asyncio.run(
            service.extract_intent_hybrid(
                question,
                catalog,
                semantic_clarification=detail_first_selection,
            )
        )
    detail_second_clarification = service._clarification_from_error(
        detail_second_exc_info.value,
        catalog,
        IntentQuery(),
        _admin_access(),
    )
    assert detail_second_clarification is not None
    detail_second_clarification = service._carry_forward_metric_selections(
        detail_second_clarification,
        detail_first_selection,
    )
    detail_avg_option = next(
        item for item in detail_second_clarification.options
        if item.id == "metric:22"
    )
    detail_combined_selection = {"selection_patch": detail_avg_option.selection_patch}
    detail_selected = asyncio.run(
        service.extract_intent_hybrid(
            question,
            catalog,
            semantic_clarification=detail_combined_selection,
        )
    )
    detail_rewritten = service._apply_semantic_clarification(
        detail_selected,
        catalog,
        detail_combined_selection,
    )
    assert detail_rewritten.metrics == [
        "sales_amount",
        "sales_detail_quantity",
        "sales_order_line_count",
        "avg_unit_price_clean_jf_sale_order_1",
    ]


def test_comma_separated_table_synonyms_are_individually_matchable():
    service = SemanticQueryService()
    catalog = _sales_catalog()
    sales_table = catalog.tables[0]
    sales_table.business_name = "销售订单表"
    sales_table.synonyms = ["销售订单主表，销售订单表", "销售订单", "订单主表"]
    catalog.tables.append(
        SemanticTable(
            id=21,
            workspace_id="w1",
            datasource_id=1,
            physical_name="clean_weaving_plan_detail",
            business_name="织造计划明细表",
            status="confirmed",
        )
    )
    catalog.columns.append(
        SemanticColumn(
            id=270,
            workspace_id="w1",
            datasource_id=1,
            table_id=21,
            physical_table="clean_weaving_plan_detail",
            physical_name="order_number",
            data_type="varchar",
            business_name="订单号",
            status="confirmed",
        )
    )
    catalog.__post_init__()
    intent = IntentQuery(
        query_type="detail",
        tables=["销售订单主表"],
        dimensions=["订单号", "客户名称", "制单日期", "交货日期", "完成状态"],
        explicit_dimensions=["订单号", "客户名称", "制单日期", "交货日期", "完成状态"],
        limit=50,
    )

    plan = service.resolve_plan(intent, catalog, _admin_access())

    assert plan.table_ids == [1]
    assert plan.column_ids == [1, 2, 4, 5, 6]


def test_field_clarification_selection_preserves_intent_and_resolves_selected_column():
    service = SemanticQueryService()
    catalog = _sales_catalog()
    sales_table = catalog.tables[0]
    sales_table.business_name = "销售订单表"
    sales_table.synonyms = ["销售订单主表，销售订单表", "销售订单", "订单主表"]
    weaving_table = SemanticTable(
        id=21,
        workspace_id="w1",
        datasource_id=1,
        physical_name="clean_weaving_plan_detail",
        business_name="织造计划明细表",
        status="confirmed",
    )
    catalog.tables.append(weaving_table)
    catalog.columns.append(
        SemanticColumn(
            id=270,
            workspace_id="w1",
            datasource_id=1,
            table_id=21,
            physical_table=weaving_table.physical_name,
            physical_name="order_number",
            data_type="varchar",
            business_name="订单号",
            status="confirmed",
        )
    )
    catalog.__post_init__()
    intent = IntentQuery(
        query_type="detail",
        tables=["未识别订单数据"],
        dimensions=["订单号", "客户名称", "制单日期", "交货日期", "完成状态"],
        explicit_dimensions=["订单号", "客户名称", "制单日期", "交货日期", "完成状态"],
        time_range="最近30天",
        limit=50,
    )

    with pytest.raises(SemanticQueryError) as exc_info:
        service.resolve_plan(intent, catalog, _admin_access())

    clarification = service._clarification_from_error(
        exc_info.value,
        catalog,
        intent,
        _admin_access(),
    )
    assert clarification is not None
    option = next(item for item in clarification.options if item.id == "column:1")

    rewritten = service._apply_semantic_clarification(
        intent,
        catalog,
        {"selection_patch": option.selection_patch},
    )
    plan = service.resolve_plan(rewritten, catalog, _admin_access())

    assert rewritten.dimensions == [
        "sale_order_no",
        "客户名称",
        "制单日期",
        "交货日期",
        "完成状态",
    ]
    assert plan.table_ids == [1]
    assert plan.column_ids == [1, 2, 4, 5, 6]

    legacy_rewritten = service._apply_semantic_clarification(
        intent,
        catalog,
        {
            "selection_patch": {
                "replace_column": {
                    "to_id": 1,
                    "to_name": "sale_order_no",
                    "to_business_name": "订单号",
                },
                "intent_patch": {"dimensions": ["订单号"]},
            }
        },
    )
    legacy_plan = service.resolve_plan(legacy_rewritten, catalog, _admin_access())
    assert legacy_rewritten.dimensions == rewritten.dimensions
    assert legacy_plan.table_ids == [1]
    assert legacy_plan.column_ids == [1, 2, 4, 5, 6]

    weaving_option = next(item for item in clarification.options if item.id == "column:270")
    weaving_intent = service._apply_semantic_clarification(
        intent,
        catalog,
        {"selection_patch": weaving_option.selection_patch},
    )
    with pytest.raises(SemanticQueryError) as next_exc_info:
        service.resolve_plan(weaving_intent, catalog, _admin_access())
    assert next_exc_info.value.error_type != "field_ambiguous"


def test_semantic_sales_overdue_unfinished_adds_business_filters():
    service = SemanticQueryService()
    catalog = _sales_catalog()
    intent = service.extract_intent("查询交货日期已经过期但订单状态未完成的销售订单", catalog)

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert {"field": "交货日期", "operator": "lt_today"} in intent.filters
    assert {"field": "完成状态", "operator": "in", "values": ["1", "2"]} in intent.filters
    assert "t0.`order_last_date` < CURRENT_DATE" in sql
    assert "t0.`status` IN ('1', '2')" in sql
    service._validate_compiled_sql(sql)


def test_semantic_production_overdue_unfinished_adds_business_filters():
    service = SemanticQueryService()
    catalog = _regression_catalog()
    intent = service.extract_intent("查询计划完工日期已过但完工状态未完成的生产任务", catalog)

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert "clean_produce_task" in plan.referenced_tables
    assert "t0.`plan_finish_date` < CURRENT_DATE" in sql
    assert "t0.`finish_status` IN ('1', '2')" in sql
    service._validate_compiled_sql(sql)


def test_semantic_inspection_exception_compiles_or_filter():
    service = SemanticQueryService()
    catalog = _regression_catalog()
    intent = service.extract_intent("查询设备点检中需要报修或点检结果异常的记录，按车间和设备统计", catalog)

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert intent.metrics == ["inspection_exception_count"]
    assert "(t0.`is_repair` = '1' OR t0.`inspection_result` <> '1')" in sql
    assert "GROUP BY t0.`workshop_name`, t0.`device_name`" in sql
    service._validate_compiled_sql(sql)


def test_semantic_inventory_prefers_exact_business_name_dimension():
    service = SemanticQueryService()
    catalog = _regression_catalog()
    intent = service.extract_intent("查询当前库存数量最低的 20 个物料，显示品名、规格、颜色、色号、库存数量", catalog)

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert 2 in plan.column_ids
    assert 1 not in plan.column_ids
    assert "t0.`item_name` AS `品名`" in sql
    assert "ORDER BY `当前库存数量` ASC" in sql
    service._validate_compiled_sql(sql)


def test_semantic_urgent_proofing_uses_main_table_filters():
    service = SemanticQueryService()
    catalog = _regression_catalog()
    intent = service.extract_intent("查询急单打样办单的完成情况，显示客户、办单单号、要求交期、完成状态", catalog)

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert plan.referenced_tables == ["clean_make_order_proofing"]
    assert "t0.`urgent_order` = '1'" in sql
    assert "t0.`require_date` AS `要求交期`" in sql
    service._validate_compiled_sql(sql)


def test_semantic_detail_extracts_table_person_and_absolute_month():
    service = SemanticQueryService()
    catalog = _follow_catalog()
    intent = service.extract_intent("查询订单跟进记录表中跟进人张蒙在2025年3月的跟进记录", catalog)

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert intent.tables == ["订单跟进记录表"]
    assert intent.time_range == "2025-03"
    assert intent.filters == [{"field": "跟进人", "operator": "contains", "value": "张蒙"}]
    assert plan.table_ids == [1]
    assert set(plan.permission_column_ids) == {2, 3, 4}
    assert "FROM `clean_clue_follow_records`" in sql
    assert "t0.`follow_person` LIKE '%张蒙%'" in sql
    assert "t0.`follow_time` >= '2025-03-01'" in sql
    assert "t0.`follow_time` < '2025-04-01'" in sql
    service._validate_compiled_sql(sql)


def test_semantic_detail_extracts_person_after_planner_rewrite_prefix():
    service = SemanticQueryService()
    catalog = _follow_catalog()
    intent = service.extract_intent("查询数据库获取张蒙在2025年3月的所有订单跟进记录", catalog)

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert intent.tables == ["订单跟进记录表"]
    assert intent.filters == [{"field": "跟进人", "operator": "contains", "value": "张蒙"}]
    assert "LIKE '%张蒙%'" in sql
    assert "取张蒙在" not in sql
    service._validate_compiled_sql(sql)


def test_semantic_follow_record_rewrite_stays_single_table_when_order_fields_appear():
    service = SemanticQueryService()
    catalog = _follow_conflict_catalog()
    question = (
        "\u7528\u6237\u5f53\u524d\u95ee\u9898:\n"
        "\u67e5\u8be2\u5f20\u8499\u57282025\u5e743\u6708\u7684\u6240\u6709\u8ba2\u5355\u8ddf\u8fdb\u8bb0\u5f55\uff0c"
        "\u5305\u62ec\u8ba2\u5355\u53f7\u3001\u5ba2\u6237\u540d\u79f0\u3001\u8ddf\u8fdb\u65f6\u95f4\u3001"
        "\u8ddf\u8fdb\u72b6\u6001\u53ca\u5907\u6ce8\u4fe1\u606f\u3002\n"
        "\u6700\u8fd1\u5bf9\u8bdd:\n- [\u7528\u6237] "
        "\u5206\u6790\u5f20\u84992025\u5e743\u6708\u7684\u8ba2\u5355\u8ddf\u8fdb\u60c5\u51b5"
    )

    intent = service.extract_intent(question, catalog)
    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert plan.table_ids == [1]
    assert plan.relationship_ids == []
    assert plan.referenced_tables == ["clean_clue_follow_records"]
    assert "clean_dispatch_order" not in sql
    assert "clean_jf_sale_order" not in sql
    assert "t0.`follow_person` LIKE '%\u5f20\u8499%'" in sql
    assert "t0.`follow_time` < '2025-04-01'" in sql
    service._validate_compiled_sql(sql)


def test_semantic_inventory_turnover_lowest_goods_compiles_without_clarification():
    service = SemanticQueryService()
    catalog = _turnover_catalog()
    intent = service.extract_intent(
        "\u67e5\u8be2\u8fd1180\u5929\u5e93\u5b58\u5468\u8f6c\u7387\u6700\u4f4e\u7684\u5546\u54c1",
        catalog,
    )

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert intent.query_type == "topn"
    assert intent.time_range == "\u8fd1180\u5929"
    assert intent.clarification_items == []
    assert plan.referenced_tables == ["clean_jf_now_details", "clean_jf_out_store_detail"]
    assert "DATE_SUB(CURRENT_DATE, INTERVAL '180' DAY)" in sql
    assert "HAVING SUM(i.`number`) > 0" in sql
    assert "ORDER BY `\u5e93\u5b58\u5468\u8f6c\u7387` ASC" in sql
    service._validate_compiled_sql(sql)


def test_semantic_hybrid_llm_fills_vague_followup_intent(monkeypatch):
    service = SemanticQueryService()
    catalog = _follow_catalog()

    async def fake_llm_intent(question, catalog, rule_intent):
        return {
            "query_type": "detail",
            "tables": ["订单跟进记录表"],
            "metrics": [],
            "dimensions": [],
            "filters": [{"field": "跟进人", "operator": "contains", "value": "张蒙"}],
            "time_range": "2025年3月",
            "limit": 100,
            "confidence": 0.9,
            "clarification_items": [],
        }

    monkeypatch.setattr(service, "_extract_intent_with_llm", fake_llm_intent)

    intent = asyncio.run(service.extract_intent_hybrid("看一下张蒙2025年3月的拜访情况", catalog))
    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert intent.tables == ["订单跟进记录表"]
    assert intent.filters == [{"field": "跟进人", "operator": "contains", "value": "张蒙"}]
    assert intent.time_range == "2025-03"
    assert "t0.`follow_person` LIKE '%张蒙%'" in sql
    assert "t0.`follow_time` >= '2025-03-01'" in sql


def test_semantic_hybrid_yearless_month_requires_clarification_before_llm(monkeypatch):
    service = SemanticQueryService()
    catalog = _follow_catalog()

    async def unexpected_llm_intent(*args, **kwargs):
        raise AssertionError("yearless month must not be guessed by the LLM")

    monkeypatch.setattr(service, "_extract_intent_with_llm", unexpected_llm_intent)

    with pytest.raises(SemanticQueryError) as exc_info:
        asyncio.run(service.extract_intent_hybrid("张蒙三月份的订单跟进情况", catalog))

    error = exc_info.value
    clarification = error.details["clarification"]
    assert error.error_type == "time_year_missing"
    assert error.safe_to_fallback is False
    assert clarification["kind"] == "time_year_missing"
    assert "3月" in clarification["message"]
    assert len(clarification["options"]) == 3
    assert all(
        option["selection_patch"]["intent_patch"]["time_range"].endswith("-03")
        for option in clarification["options"]
    )


def test_semantic_yearless_month_clarification_overrides_llm_guess(monkeypatch):
    service = SemanticQueryService()
    catalog = _follow_catalog()

    async def fake_llm_intent(question, catalog, rule_intent):
        return {
            "query_type": "detail",
            "tables": ["订单跟进记录表"],
            "metrics": [],
            "dimensions": [],
            "filters": [{"field": "跟进人", "operator": "contains", "value": "张蒙"}],
            "time_range": "2024-03",
            "limit": 100,
            "confidence": 0.9,
            "clarification_items": [],
        }

    monkeypatch.setattr(service, "_extract_intent_with_llm", fake_llm_intent)
    clarification = {
        "selection_patch": {"intent_patch": {"time_range": "2025-03"}},
    }

    intent = asyncio.run(
        service.extract_intent_hybrid(
            "张蒙三月份的订单跟进情况",
            catalog,
            semantic_clarification=clarification,
        )
    )
    intent = service._apply_semantic_clarification(intent, catalog, clarification)

    assert intent.time_range == "2025-03"


def test_semantic_yearless_month_accepts_free_text_year_clarification(monkeypatch):
    service = SemanticQueryService()
    catalog = _follow_catalog()

    async def fake_llm_intent(question, catalog, rule_intent):
        return {
            "query_type": "detail",
            "tables": ["订单跟进记录表"],
            "metrics": [],
            "dimensions": [],
            "filters": [{"field": "跟进人", "operator": "contains", "value": "张蒙"}],
            "time_range": "2024-03",
            "limit": 100,
            "confidence": 0.9,
            "clarification_items": [],
        }

    monkeypatch.setattr(service, "_extract_intent_with_llm", fake_llm_intent)
    clarification = {"free_text": "2025年3月"}

    intent = asyncio.run(
        service.extract_intent_hybrid(
            "张蒙三月份的订单跟进情况",
            catalog,
            semantic_clarification=clarification,
        )
    )
    intent = service._apply_semantic_clarification(intent, catalog, clarification)

    assert intent.time_range == "2025-03"


def test_semantic_llm_invalid_objects_are_discarded():
    service = SemanticQueryService()
    catalog = _follow_catalog()
    rule_intent = IntentQuery(query_type="detail", limit=100)

    intent = service._merge_llm_intent(
        rule_intent,
        {
            "query_type": "detail",
            "tables": ["不存在的表"],
            "metrics": ["不存在指标"],
            "dimensions": ["不存在字段"],
            "filters": [{"field": "不存在字段", "operator": "drop", "value": "x"}],
            "time_range": "",
            "limit": 100,
            "confidence": 0.9,
            "clarification_items": [],
        },
        catalog,
    )

    assert intent.tables == []
    assert intent.metrics == []
    assert intent.dimensions == []
    assert intent.filters == []


def test_semantic_time_column_ambiguity_returns_clarification():
    service = SemanticQueryService()
    catalog = _follow_catalog(ambiguous_time=True)
    catalog.tables[0].business_name = "事件表"
    catalog.tables[0].synonyms = []
    catalog.__post_init__()
    intent = IntentQuery(query_type="detail", tables=["事件表"], time_range="2025-03")

    with pytest.raises(SemanticQueryError) as exc_info:
        service.resolve_plan(intent, catalog, _access())

    assert exc_info.value.error_type == "time_field_ambiguous"
    assert "candidate_time_columns" in exc_info.value.details


def test_semantic_time_column_selection_retries_with_selected_field():
    service = SemanticQueryService()
    catalog = _follow_catalog(ambiguous_time=True)
    catalog.tables[0].business_name = "事件表"
    catalog.tables[0].synonyms = []
    catalog.__post_init__()
    intent = IntentQuery(
        query_type="detail",
        tables=["事件表"],
        time_range="2025-03",
        selected_time_column_id=5,
    )

    plan = service.resolve_plan(intent, catalog, _access())
    sql = service.compile_sql(plan, catalog)

    assert "`next_visit_time`" in sql


def test_semantic_permission_denied_metric_with_alternative_returns_rewrite():
    service = SemanticQueryService()
    catalog = _catalog()
    catalog.metrics.append(
        SemanticMetric(
            id=2,
            workspace_id="w1",
            datasource_id=1,
            name="order_count",
            business_name="订单数",
            formula="COUNT(*)",
            table_id=1,
            time_column_id=3,
            status="confirmed",
        )
    )
    catalog.__post_init__()
    intent = IntentQuery(query_type="aggregate", metrics=["sales_amount"])

    with pytest.raises(SemanticQueryError) as exc_info:
        service.resolve_plan(intent, catalog, _access_with_restricted_metric())

    assert exc_info.value.error_type == "permission_rewrite_required"
    clarification = exc_info.value.details["clarification"]
    assert clarification["kind"] == "permission_rewrite_required"
    assert clarification["options"][0]["selection_patch"]["replace_metric"]["to_name"] == "order_count"


def test_semantic_permission_denied_metric_without_alternative_fails_closed():
    service = SemanticQueryService()
    catalog = _catalog()
    intent = IntentQuery(query_type="aggregate", metrics=["sales_amount"])

    with pytest.raises(SemanticQueryError) as exc_info:
        service.resolve_plan(intent, catalog, _access_with_restricted_metric())

    assert exc_info.value.error_type == "permission_denied"
    assert exc_info.value.safe_to_fallback is False


def test_semantic_permission_rewrite_replaces_all_blocked_columns_at_once():
    service = SemanticQueryService()
    catalog = _follow_catalog()
    catalog.columns.extend(
        [
            SemanticColumn(
                id=5,
                workspace_id="w1",
                datasource_id=1,
                table_id=1,
                physical_table="clean_clue_follow_records",
                physical_name="follow_method",
                business_name="跟进方式",
                data_type="varchar",
                status="confirmed",
            ),
            SemanticColumn(
                id=6,
                workspace_id="w1",
                datasource_id=1,
                table_id=1,
                physical_table="clean_clue_follow_records",
                physical_name="follow_record",
                business_name="跟进记录详情",
                data_type="text",
                status="confirmed",
            ),
        ]
    )
    catalog.__post_init__()
    access = _access()
    for column_id in (5, 6):
        access["semantic_access"]["effects_by_asset"][f"column:{column_id}"] = [
            {
                "policy_id": 10 + column_id,
                "effect_type": "hidden",
                "asset_type": "column",
                "asset_id": column_id,
                "subject_type": "user",
                "subject_id": "u1",
            }
        ]
        access["semantic_access"]["policy_ids"].append(10 + column_id)
    intent = service.extract_intent(
        "张蒙2025年3月的订单跟进记录，显示跟进方式和跟进记录详情",
        catalog,
    )

    with pytest.raises(SemanticQueryError) as exc_info:
        service.resolve_plan(intent, catalog, access)

    clarification = exc_info.value.details["clarification"]
    assert clarification["message"] == (
        "当前问题涉及您无权访问的对象：跟进方式、跟进记录详情。"
        "请选择您想查询的替代内容"
    )
    assert {item["label"] for item in clarification["blocked_objects"]} == {
        "跟进方式",
        "跟进记录详情",
    }
    content_option = next(
        option
        for option in clarification["options"]
        if option["label"] == "改用字段：跟进内容"
    )
    replacements = content_option["selection_patch"]["replace_columns"]
    assert {item["from_id"] for item in replacements} == {5, 6}
    assert {item["to_id"] for item in replacements} == {4}

    reparsed_intent = IntentQuery(
        query_type="detail",
        tables=["订单跟进记录表"],
        dimensions=["跟进方式"],
    )
    rewritten = service._apply_semantic_clarification(
        reparsed_intent,
        catalog,
        {"selection_patch": content_option["selection_patch"]},
    )
    assert "跟进方式" not in rewritten.dimensions
    assert "跟进记录详情" not in rewritten.dimensions
    assert rewritten.dimensions.count("跟进内容") == 1
    assert rewritten.filters == [
        {"field": "跟进人", "operator": "contains", "value": "张蒙"}
    ]
    assert rewritten.time_range == "2025-03"
    plan = service.resolve_plan(rewritten, catalog, access)
    assert 4 in plan.permission_column_ids
    assert 5 not in plan.permission_column_ids
    assert 6 not in plan.permission_column_ids


def test_semantic_sensitive_column_is_classification_not_a_permission_rule():
    service = SemanticQueryService()
    catalog = _catalog(sensitive_customer=True)
    intent = IntentQuery(
        query_type="topn",
        metrics=["sales_amount"],
        dimensions=["客户"],
        order_by=[{"field": "sales_amount", "direction": "desc"}],
        limit=10,
    )

    plan = service.resolve_plan(intent, catalog, _access())

    assert 1 in plan.permission_column_ids


def test_semantic_sensitive_filter_column_is_allowed_when_table_is_visible():
    service = SemanticQueryService()
    catalog = _follow_catalog(sensitive_person=True)
    intent = IntentQuery(
        query_type="detail",
        tables=["订单跟进记录表"],
        filters=[{"field": "跟进人", "operator": "contains", "value": "张蒙"}],
        time_range="2025-03",
    )

    plan = service.resolve_plan(intent, catalog, _access())

    assert plan.intent.filters


def test_semantic_detail_projection_excludes_policy_restricted_columns():
    service = SemanticQueryService()
    catalog = _catalog()
    access = _access()
    access["semantic_access"]["effects_by_asset"]["column:1"] = [
        {
            "policy_id": 10,
            "effect_type": "hidden",
            "asset_type": "column",
            "asset_id": 1,
            "subject_type": "user",
            "subject_id": "u1",
        }
    ]
    access["semantic_access"]["policy_ids"].append(10)
    plan = service.resolve_plan(IntentQuery(query_type="detail", tables=["订单"]), catalog, access)

    sql = service.compile_sql(plan, catalog)

    assert "customer_name" not in sql
    assert "amount" in sql


def test_compile_sql_orders_join_edges_from_the_selected_base_table():
    datasource = SemanticDatasource(id=1, workspace_id="w1", name="demo", host="localhost", port=3306, database="demo", semantic_sql_enabled=True)
    table_one = SemanticTable(id=1, workspace_id="w1", datasource_id=1, physical_name="table_one", business_name="表一", status="confirmed")
    table_two = SemanticTable(id=2, workspace_id="w1", datasource_id=1, physical_name="table_two", business_name="表二", status="confirmed")
    table_three = SemanticTable(id=3, workspace_id="w1", datasource_id=1, physical_name="table_three", business_name="表三", status="confirmed")
    columns = [
        SemanticColumn(id=11, workspace_id="w1", datasource_id=1, table_id=1, physical_table=table_one.physical_name, physical_name="two_id", data_type="varchar", business_name="表二ID", status="confirmed"),
        SemanticColumn(id=12, workspace_id="w1", datasource_id=1, table_id=1, physical_table=table_one.physical_name, physical_name="amount_one", data_type="decimal", business_name="金额一", status="confirmed"),
        SemanticColumn(id=21, workspace_id="w1", datasource_id=1, table_id=2, physical_table=table_two.physical_name, physical_name="id", data_type="varchar", business_name="表二主键", status="confirmed"),
        SemanticColumn(id=22, workspace_id="w1", datasource_id=1, table_id=2, physical_table=table_two.physical_name, physical_name="three_id", data_type="varchar", business_name="表三ID", status="confirmed"),
        SemanticColumn(id=23, workspace_id="w1", datasource_id=1, table_id=2, physical_table=table_two.physical_name, physical_name="amount_two", data_type="decimal", business_name="金额二", status="confirmed"),
        SemanticColumn(id=31, workspace_id="w1", datasource_id=1, table_id=3, physical_table=table_three.physical_name, physical_name="id", data_type="varchar", business_name="表三主键", status="confirmed"),
        SemanticColumn(id=32, workspace_id="w1", datasource_id=1, table_id=3, physical_table=table_three.physical_name, physical_name="amount_three", data_type="decimal", business_name="金额三", status="confirmed"),
    ]
    metrics = [
        SemanticMetric(id=1, workspace_id="w1", datasource_id=1, name="metric_three", business_name="指标三", formula="SUM({amount_three})", table_id=3, column_id=32, status="confirmed"),
        SemanticMetric(id=2, workspace_id="w1", datasource_id=1, name="metric_one", business_name="指标一", formula="SUM({amount_one})", table_id=1, column_id=12, status="confirmed"),
        SemanticMetric(id=3, workspace_id="w1", datasource_id=1, name="metric_two", business_name="指标二", formula="SUM({amount_two})", table_id=2, column_id=23, status="confirmed"),
    ]
    relationships = [
        SemanticRelationship(id=1, workspace_id="w1", datasource_id=1, left_table_id=1, right_table_id=2, left_column_id=11, right_column_id=21, status="confirmed"),
        SemanticRelationship(id=2, workspace_id="w1", datasource_id=1, left_table_id=3, right_table_id=2, left_column_id=31, right_column_id=22, status="confirmed"),
    ]
    catalog = SemanticCatalog(datasource=datasource, tables=[table_one, table_two, table_three], columns=columns, metrics=metrics, relationships=relationships)
    service = SemanticQueryService()
    intent = IntentQuery(query_type="aggregate", metrics=["metric_three", "metric_one", "metric_two"])

    plan = service.resolve_plan(intent, catalog, _admin_access())
    sql = service.compile_sql(plan, catalog)

    assert plan.relationship_ids == [1, 2]
    assert "CROSS JOIN" in sql
    assert "FROM `table_three` AS m0" in sql
    assert "FROM `table_one` AS m0" in sql
    assert "FROM `table_two` AS m0" in sql
    assert len(set(plan.table_ids)) == 3


def test_semantic_missing_join_path_returns_structured_error():
    service = SemanticQueryService()
    catalog = _catalog()
    products = SemanticTable(
        id=2,
        workspace_id="w1",
        datasource_id=1,
        physical_name="products",
        business_name="商品",
        status="confirmed",
    )
    product_name = SemanticColumn(
        id=4,
        workspace_id="w1",
        datasource_id=1,
        table_id=2,
        physical_table="products",
        physical_name="product_name",
        business_name="商品",
        status="confirmed",
    )
    catalog.tables.append(products)
    catalog.columns.append(product_name)
    catalog.__post_init__()

    intent = IntentQuery(
        query_type="topn",
        metrics=["sales_amount"],
        dimensions=["商品"],
        order_by=[{"field": "sales_amount", "direction": "desc"}],
        limit=5,
    )

    with pytest.raises(SemanticQueryError) as exc_info:
        service.resolve_plan(intent, catalog, _access())

    assert exc_info.value.error_type == "semantic_model_incomplete"
    assert exc_info.value.safe_to_fallback is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "planner_rewrite", "expected_type"),
    [
        ("按月统计 2025 年 3 月到 2026 年 5 月的销售金额趋势", "查询并统计2025年3月到2026年5月各月的销售金额汇总数据", "trend"),
        ("对比 2026 年 4 月和 5 月各客户的销售金额变化", "查询并统计2026年4月和5月各客户的销售金额，计算两个月的变化量及变化率", "compare"),
    ],
)
async def test_executor_sql_tool_worker_contract_preserves_original_question(
    monkeypatch,
    question: str,
    planner_rewrite: str,
    expected_type: str,
):
    from app.agents import sql_worker as sql_worker_module
    from app.supervisor.nodes.executor import execute_worker_task
    from app.tools import registry as registry_module
    from app.tools.sql_tool import sql_tool

    catalog = _planner_conflict_catalog()
    observed: dict[str, object] = {}

    class FakeWorker:
        async def execute_task(self, task_description: str, original_query: str | None = None, **kwargs):
            intent = await SemanticQueryService().extract_intent_hybrid(
                original_query or task_description,
                catalog,
                context_hint=task_description,
            )
            plan = SemanticQueryService().resolve_plan(intent, catalog, _admin_access())
            observed.update(original_query=original_query, task_description=task_description, intent=intent, plan=plan)
            return {"success": True, "row_count": 1, "data": [{"ok": 1}], "columns": ["ok"], "artifacts": {}}

        @staticmethod
        def format_result_for_synthesizer(result):
            return "ok"

    monkeypatch.setattr(sql_worker_module, "get_sql_worker", lambda: FakeWorker())
    monkeypatch.setattr(registry_module, "get_tool", lambda name: sql_tool if name == "sql_worker" else None)

    await execute_worker_task(
        worker="sql_worker",
        description=planner_rewrite,
        user_query=question,
        user_context={"user_id": "u1", "workspace_id": "w1"},
    )

    assert observed["original_query"] == question
    assert observed["task_description"] == planner_rewrite
    assert observed["intent"].query_type == expected_type
    assert observed["plan"].table_ids == [33]


@pytest.mark.asyncio
async def test_sql_tool_uses_structured_semantic_error_without_text_guessing(monkeypatch):
    from app.agents import sql_worker as sql_worker_module
    from app.tools.sql_tool import run_sql_task

    class FakeWorker:
        async def execute_task(self, **kwargs):
            return {
                "success": False,
                "error": "任意本地化文案",
                "error_type": "semantic_model_incomplete",
                "retryable": False,
                "from_semantic": True,
            }

    monkeypatch.setattr(sql_worker_module, "get_sql_worker", lambda: FakeWorker())

    result = await run_sql_task(query="跨表统计", original_query="跨表统计", user_id="u1")

    assert result.quality_signal["reason_code"] == "semantic_model_incomplete"
    assert result.quality_signal["retryable"] is False


def test_produce_task_item_quantity_query_requires_time_clarification():
    service = SemanticQueryService()
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
        id=52,
        workspace_id="w1",
        datasource_id=1,
        physical_name="clean_produce_task",
        business_name="生产任务表",
        synonyms=["生产任务"],
        status="confirmed",
    )
    columns = [
        SemanticColumn(id=858, workspace_id="w1", datasource_id=1, table_id=52, physical_table=table.physical_name, physical_name="document_date", data_type="date", business_name="制单日期", synonyms=["任务日期", "创建日期"], status="confirmed"),
        SemanticColumn(id=870, workspace_id="w1", datasource_id=1, table_id=52, physical_table=table.physical_name, physical_name="plan_finish_date", data_type="date", business_name="计划完工日期", status="confirmed"),
        SemanticColumn(id=875, workspace_id="w1", datasource_id=1, table_id=52, physical_table=table.physical_name, physical_name="number", data_type="decimal", business_name="生产数量", synonyms=["任务数量", "数量"], status="confirmed"),
        SemanticColumn(id=879, workspace_id="w1", datasource_id=1, table_id=52, physical_table=table.physical_name, physical_name="product_name", data_type="varchar", business_name="品名", synonyms=["产品名称", "商品名称"], status="confirmed"),
    ]
    catalog = SemanticCatalog(
        datasource=datasource,
        tables=[table],
        columns=columns,
        metrics=[],
        relationships=[],
    )

    intent = service.extract_intent("查询2025年3月生产任务表的品名和生产数量", catalog)

    assert intent.query_type == "detail"
    assert intent.metrics == []
    assert intent.tables == ["生产任务表"]
    assert intent.dimensions == ["品名", "生产数量"]
    with pytest.raises(SemanticQueryError) as exc_info:
        service.resolve_plan(intent, catalog, _access())
    assert exc_info.value.error_type == "time_field_ambiguous"


def test_semantic_sql_validation_rejects_non_select_and_multistatement():
    service = SemanticQueryService()

    with pytest.raises(SemanticQueryError) as non_select:
        service._validate_compiled_sql("DELETE FROM orders LIMIT 1")
    assert non_select.value.error_type == "unsafe_sql"
    assert non_select.value.safe_to_fallback is False

    with pytest.raises(SemanticQueryError) as multistatement:
        service._validate_compiled_sql("SELECT * FROM orders LIMIT 1; SELECT 1")
    assert multistatement.value.error_type == "unsafe_sql"
    assert multistatement.value.safe_to_fallback is False


def _cross_grain_sales_catalog() -> SemanticCatalog:
    datasource = SemanticDatasource(
        id=1,
        workspace_id="w1",
        name="demo",
        host="localhost",
        port=3306,
        database="demo",
        semantic_sql_enabled=True,
    )
    header = SemanticTable(
        id=33,
        workspace_id="w1",
        datasource_id=1,
        physical_name="clean_jf_sale_order",
        business_name="销售订单表",
        status="confirmed",
    )
    detail = SemanticTable(
        id=34,
        workspace_id="w1",
        datasource_id=1,
        physical_name="clean_jf_sale_order_1",
        business_name="销售订单明细表",
        status="confirmed",
    )
    columns = [
        SemanticColumn(id=1, workspace_id="w1", datasource_id=1, table_id=33, physical_table=header.physical_name, physical_name="sale_order_no", data_type="varchar", business_name="订单号", status="confirmed"),
        SemanticColumn(id=2, workspace_id="w1", datasource_id=1, table_id=33, physical_table=header.physical_name, physical_name="customer_name", data_type="varchar", business_name="客户名称", status="confirmed"),
        SemanticColumn(id=3, workspace_id="w1", datasource_id=1, table_id=33, physical_table=header.physical_name, physical_name="money_sum", data_type="decimal", business_name="销售金额", status="confirmed"),
        SemanticColumn(id=4, workspace_id="w1", datasource_id=1, table_id=33, physical_table=header.physical_name, physical_name="make_date", data_type="date", business_name="制单日期", status="confirmed"),
        SemanticColumn(id=5, workspace_id="w1", datasource_id=1, table_id=34, physical_table=detail.physical_name, physical_name="id", data_type="varchar", business_name="明细ID", status="confirmed"),
        SemanticColumn(id=6, workspace_id="w1", datasource_id=1, table_id=34, physical_table=detail.physical_name, physical_name="main_code", data_type="varchar", business_name="主订单代码", status="confirmed"),
        SemanticColumn(id=7, workspace_id="w1", datasource_id=1, table_id=34, physical_table=detail.physical_name, physical_name="quantity", data_type="decimal", business_name="销售数量", status="confirmed"),
        SemanticColumn(id=8, workspace_id="w1", datasource_id=1, table_id=34, physical_table=detail.physical_name, physical_name="tax_amount", data_type="decimal", business_name="含税金额", status="confirmed"),
    ]
    metrics = [
        SemanticMetric(id=1, workspace_id="w1", datasource_id=1, name="sales_amount", business_name="销售金额", formula="SUM({money_sum})", table_id=33, column_id=3, time_column_id=4, default_grain="month", status="confirmed"),
        SemanticMetric(id=2, workspace_id="w1", datasource_id=1, name="sales_order_count", business_name="销售订单数", formula="COUNT(DISTINCT {sale_order_no})", table_id=33, column_id=1, time_column_id=4, default_grain="month", status="confirmed"),
        SemanticMetric(id=3, workspace_id="w1", datasource_id=1, name="sales_order_line_count", business_name="销售明细行数", formula="COUNT(DISTINCT {id})", table_id=34, column_id=5, status="confirmed"),
        SemanticMetric(id=4, workspace_id="w1", datasource_id=1, name="avg_unit_price", business_name="平均单价", formula="SUM({tax_amount}) / NULLIF(SUM({quantity}), 0)", table_id=34, column_id=8, status="confirmed"),
    ]
    relationship = SemanticRelationship(
        id=1,
        workspace_id="w1",
        datasource_id=1,
        left_table_id=34,
        right_table_id=33,
        left_column_id=6,
        right_column_id=1,
        relationship_type="many_to_one",
        confidence=1,
        status="confirmed",
    )
    return SemanticCatalog(
        datasource=datasource,
        tables=[header, detail],
        columns=columns,
        metrics=metrics,
        relationships=[relationship],
    )


def test_cross_grain_metrics_are_aggregated_before_joining():
    service = SemanticQueryService()
    catalog = _cross_grain_sales_catalog()
    intent = IntentQuery(
        query_type="aggregate",
        tables=["销售订单表", "销售订单明细表"],
        metrics=["sales_amount", "sales_order_count", "sales_order_line_count", "avg_unit_price"],
        dimensions=[],
        explicit_dimensions=[],
        time_range="2026-05",
    )

    plan = service.resolve_plan(intent, catalog, _admin_access())
    sql = service.compile_sql(plan, catalog)

    assert "CROSS JOIN" in sql
    assert "FROM `clean_jf_sale_order` AS m0" in sql
    assert "FROM `clean_jf_sale_order_1` AS m0" in sql
    assert "JOIN `clean_jf_sale_order` AS m1" in sql
    assert sql.count("SUM(m0.`money_sum`)") == 1
    assert "SUM(m1.`money_sum`)" not in sql
    service._validate_compiled_sql(sql)


def test_cross_grain_topn_groups_each_metric_at_customer_grain_before_merging():
    service = SemanticQueryService()
    catalog = _cross_grain_sales_catalog()
    intent = IntentQuery(
        query_type="topn",
        tables=["销售订单表", "销售订单明细表"],
        metrics=["sales_amount", "sales_order_count", "sales_order_line_count"],
        dimensions=["客户名称"],
        explicit_dimensions=["客户名称"],
        time_range="2026-05",
        order_by=[{"field": "sales_amount", "direction": "desc"}],
        limit=10,
    )

    plan = service.resolve_plan(intent, catalog, _admin_access())
    sql = service.compile_sql(plan, catalog)

    assert "UNION" in sql
    assert sql.count("GROUP BY m0.`customer_name`") >= 1
    assert "GROUP BY m1.`customer_name`" in sql
    assert "ORDER BY `销售金额` DESC" in sql
    assert sql.endswith("LIMIT 10")
    service._validate_compiled_sql(sql)

