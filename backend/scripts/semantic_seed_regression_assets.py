"""Seed first-stage semantic assets for the fixed NL2SQL regression cases.

Default mode is dry-run. Use --apply to persist changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.db.database import get_async_db_manager  # noqa: E402
from app.models.config.semantic import (  # noqa: E402
    SemanticColumnModel,
    SemanticDatasourceModel,
    SemanticMetricModel,
    SemanticRelationshipModel,
    SemanticTableModel,
)


TABLE_SEMANTICS: dict[str, dict[str, Any]] = {
    "clean_jf_sale_order": {
        "business_name": "销售订单主表",
        "description": "销售订单主数据，包含客户、订单号、金额、制单日期、交货日期和完成状态。",
        "synonyms": ["销售订单表", "销售订单", "订单主表"],
    },
    "clean_jf_order_detail": {
        "business_name": "销售订单明细表",
        "description": "销售订单明细数据，包含物料、品名、规格、数量、单价和金额。",
        "synonyms": ["销售明细", "订单明细", "销售订单从表"],
    },
    "clean_jf_now_details": {
        "business_name": "当前库存明细表",
        "description": "当前库存明细数据，包含物料名称、规格、颜色、色号和库存数量。",
        "synonyms": ["现存量", "库存明细", "当前库存"],
    },
    "clean_jf_in_store_detail": {
        "business_name": "入库明细表",
        "description": "入库业务明细数据，包含物料、仓库、入库数量和入库日期。",
        "synonyms": ["入库记录", "入库数量", "入库流水"],
    },
    "clean_jf_out_store_detail": {
        "business_name": "出库明细表",
        "description": "出库业务明细数据，包含物料、仓库、出库数量和出库日期。",
        "synonyms": ["出库记录", "出库数量", "出库流水"],
    },
    "clean_dispatch_order": {
        "business_name": "生产派工单",
        "description": "生产派工单数据，包含派工单号、派工数量、投产设备和制单日期。",
        "synonyms": ["派工订单", "派工单", "生产派工"],
    },
    "clean_produce_task": {
        "business_name": "生产任务表",
        "description": "生产任务数据，包含任务单号、计划完工日期、完工状态、数量和设备。",
        "synonyms": ["生产任务", "生产工单", "生产任务单"],
    },
    "clean_make_order_proofing": {
        "business_name": "打样办单主表",
        "description": "打样办单主数据，包含办单单号、客户、要求交期、急单标记和完成状态。",
        "synonyms": ["打样办单", "办单主表", "打样订单"],
    },
    "clean_make_order_proofing_detail": {
        "business_name": "打样办单明细表",
        "description": "打样办单明细数据，包含货号、品名、规格、数量、金额和要求交期。",
        "synonyms": ["打样明细", "办单明细", "打样订单明细"],
    },
    "clean_finish_inspection_detail": {
        "business_name": "完工检验明细表",
        "description": "完工检验明细数据，包含检验日期、品名、合格数量和不合格数量。",
        "synonyms": ["完工检验", "检验明细", "质量检验"],
    },
    "clean_finish_accept_detail": {
        "business_name": "成品验收明细表",
        "description": "成品验收明细数据，包含验收日期、品名、验收数量和不合格数量。",
        "synonyms": ["成品验收", "验收明细", "质量验收"],
    },
    "clean_dz_inspection_work": {
        "business_name": "设备点检记录表",
        "description": "设备点检记录，包含车间、设备、点检结果和是否报修。",
        "synonyms": ["设备点检工作表", "点检记录", "设备巡检"],
    },
}


FIELD_SEMANTICS: dict[str, dict[str, dict[str, Any]]] = {
    "clean_jf_sale_order": {
        "sale_order_no": {"business_name": "订单号", "synonyms": ["销售单号", "销售订单号", "内部订单号"]},
        "customer_name": {"business_name": "客户名称", "synonyms": ["客户", "客户名", "买方名称"]},
        "money_sum": {"business_name": "含税总金额", "synonyms": ["销售金额", "销售额", "订单金额", "总金额"]},
        "make_date": {"business_name": "制单日期", "synonyms": ["订单日期", "下单日期", "创建日期"]},
        "order_last_date": {"business_name": "交货日期", "synonyms": ["要求交期", "交付日期", "发货日期"]},
        "status": {"business_name": "完成状态", "synonyms": ["订单状态", "执行状态", "完工状态"]},
    },
    "clean_jf_order_detail": {
        "order_detail_number": {"business_name": "明细编号", "synonyms": ["订单明细号", "明细单号"]},
        "main_code": {"business_name": "主表单号", "synonyms": ["主单号", "订单主单号"]},
        "material_number": {"business_name": "货号", "synonyms": ["物料编码", "商品编号", "产品代码"]},
        "material_name": {"business_name": "品名", "synonyms": ["产品名称", "商品名称", "物料名称"]},
        "specification_model": {"business_name": "规格", "synonyms": ["规格型号", "型号", "规格描述"]},
        "purchase_number": {"business_name": "销售数量", "synonyms": ["订单数量", "明细数量", "数量"]},
        "tax_single": {"business_name": "含税单价", "synonyms": ["销售单价", "单价"]},
        "tax_amount": {"business_name": "明细金额", "synonyms": ["销售明细金额", "含税金额", "金额"]},
        "colour_no": {"business_name": "色号", "synonyms": ["颜色代码", "色码"]},
        "colour": {"business_name": "颜色", "synonyms": ["色名", "颜色名称"]},
    },
    "clean_jf_now_details": {
        "mname": {"business_name": "物料名称", "synonyms": ["品名", "商品名称", "物料"]},
        "item_name": {"business_name": "品名", "synonyms": ["产品名称", "商品名称"]},
        "mtype": {"business_name": "规格", "synonyms": ["规格型号", "型号"]},
        "colour": {"business_name": "颜色", "synonyms": ["色名", "颜色名称"]},
        "colour_no": {"business_name": "色号", "synonyms": ["颜色代码", "色码"]},
        "number": {"business_name": "当前库存数量", "synonyms": ["库存数量", "现存数量", "库存"]},
        "store_name": {"business_name": "仓库", "synonyms": ["仓库名称", "库位"]},
    },
    "clean_jf_in_store_detail": {
        "mname": {"business_name": "物料名称", "synonyms": ["品名", "商品名称", "物料"]},
        "item_name": {"business_name": "品名", "synonyms": ["产品名称", "商品名称"]},
        "mtype": {"business_name": "规格", "synonyms": ["规格型号", "型号"]},
        "colour": {"business_name": "颜色", "synonyms": ["色名", "颜色名称"]},
        "colour_no": {"business_name": "色号", "synonyms": ["颜色代码", "色码"]},
        "in_store_num": {"business_name": "入库数量", "synonyms": ["入库数", "入库量"]},
        "create_time": {"business_name": "入库日期", "synonyms": ["入库时间", "创建日期"]},
        "store_name": {"business_name": "仓库", "synonyms": ["仓库名称", "库位"]},
    },
    "clean_jf_out_store_detail": {
        "mname": {"business_name": "物料名称", "synonyms": ["品名", "商品名称", "物料"]},
        "item_name": {"business_name": "品名", "synonyms": ["产品名称", "商品名称"]},
        "colour": {"business_name": "颜色", "synonyms": ["色名", "颜色名称"]},
        "colour_no": {"business_name": "色号", "synonyms": ["颜色代码", "色码"]},
        "out_store_num": {"business_name": "出库数量", "synonyms": ["出库数", "出库量"]},
        "create_time": {"business_name": "出库日期", "synonyms": ["出库时间", "创建日期"]},
        "store_name": {"business_name": "仓库", "synonyms": ["仓库名称", "库位"]},
    },
    "clean_dispatch_order": {
        "document_no": {"business_name": "派工单号", "synonyms": ["派工单", "单据编号", "派工订单号"]},
        "dispatch_number": {"business_name": "派工数量", "synonyms": ["派工量", "任务数量"]},
        "produce_device": {"business_name": "生产设备", "synonyms": ["投产设备", "设备编号"]},
        "make_date": {"business_name": "制单日期", "synonyms": ["派工日期", "创建日期"]},
        "product_name": {"business_name": "品名", "synonyms": ["产品名称", "商品名称"]},
        "article_no": {"business_name": "货号", "synonyms": ["物料编码", "商品编号"]},
        "spec": {"business_name": "规格", "synonyms": ["规格型号", "型号"]},
    },
    "clean_produce_task": {
        "document_no": {"business_name": "生产任务单号", "synonyms": ["生产工单号", "任务单号", "单据编号"]},
        "document_date": {"business_name": "制单日期", "synonyms": ["任务日期", "创建日期"]},
        "plan_finish_date": {"business_name": "计划完工日期", "synonyms": ["计划完成日期", "预计完工日期"]},
        "finish_status": {"business_name": "完工状态", "synonyms": ["完成状态", "生产状态"]},
        "number": {"business_name": "生产数量", "synonyms": ["任务数量", "数量"]},
        "product_name": {"business_name": "品名", "synonyms": ["产品名称", "商品名称"]},
        "spec": {"business_name": "规格", "synonyms": ["规格型号", "型号"]},
        "produce_device": {"business_name": "生产设备", "synonyms": ["投产设备", "设备编号"]},
    },
    "clean_make_order_proofing": {
        "make_order_no": {"business_name": "办单单号", "synonyms": ["打样单号", "打样办单号", "单据编号"]},
        "customer_name": {"business_name": "客户名称", "synonyms": ["客户", "客户名"]},
        "require_date": {"business_name": "要求交期", "synonyms": ["交货日期", "要求交付日期"]},
        "make_date": {"business_name": "制单日期", "synonyms": ["办单日期", "创建日期"]},
        "urgent_order": {"business_name": "急单标记", "synonyms": ["急单", "是否急单"]},
        "finish_status": {"business_name": "完成状态", "synonyms": ["完工状态", "打样状态"]},
    },
    "clean_make_order_proofing_detail": {
        "article_no": {"business_name": "货号", "synonyms": ["物料编码", "商品编号"]},
        "product_name": {"business_name": "品名", "synonyms": ["产品名称", "商品名称"]},
        "spec": {"business_name": "规格", "synonyms": ["规格型号", "型号"]},
        "number": {"business_name": "打样数量", "synonyms": ["数量", "办单数量"]},
        "total_price": {"business_name": "打样金额", "synonyms": ["金额", "总金额"]},
        "arrival_date": {"business_name": "要求交期", "synonyms": ["交货日期", "到货日期"]},
        "main_code": {"business_name": "主表单号", "synonyms": ["办单主单号", "打样单号"]},
    },
    "clean_finish_inspection_detail": {
        "inspect_date": {"business_name": "检验日期", "synonyms": ["完工检验日期", "质检日期"]},
        "article_no": {"business_name": "货号", "synonyms": ["物料编码", "商品编号"]},
        "product_name": {"business_name": "品名", "synonyms": ["产品名称", "商品名称"]},
        "accept_number": {"business_name": "合格数量", "synonyms": ["检验数量", "验收数量"]},
        "unqualified_number": {"business_name": "不合格数量", "synonyms": ["异常数量", "不良数量"]},
    },
    "clean_finish_accept_detail": {
        "inspect_date": {"business_name": "验收日期", "synonyms": ["检验日期", "质检日期"]},
        "article_no": {"business_name": "货号", "synonyms": ["物料编码", "商品编号"]},
        "product_name": {"business_name": "品名", "synonyms": ["产品名称", "商品名称"]},
        "accept_number": {"business_name": "验收数量", "synonyms": ["检验数量", "合格数量"]},
        "unqualified_number": {"business_name": "不合格数量", "synonyms": ["异常数量", "不良数量"]},
    },
    "clean_dz_inspection_work": {
        "billno": {"business_name": "点检单号", "synonyms": ["检查单号", "点检记录号"]},
        "workshop_name": {"business_name": "车间", "synonyms": ["车间名称", "生产单元"]},
        "device_code": {"business_name": "设备编号", "synonyms": ["设备代码", "资产编号"]},
        "device_name": {"business_name": "设备名称", "synonyms": ["设备", "机器名称"]},
        "inspection_finish_time": {"business_name": "点检完成时间", "synonyms": ["点检日期", "检查结束时间"]},
        "inspection_result": {"business_name": "点检结果", "synonyms": ["点检总结果", "检查结果", "异常状态"]},
        "is_repair": {"business_name": "是否报修", "synonyms": ["报修", "维修需求"]},
    },
}


METRIC_SPECS: list[dict[str, Any]] = [
    {
        "name": "sales_amount",
        "business_name": "销售金额",
        "table": "clean_jf_sale_order",
        "formula": "SUM({money_sum})",
        "aggregation": "sum",
        "column": "money_sum",
        "time_column": "make_date",
        "default_grain": "month",
        "synonyms": ["销售额", "订单金额", "总金额", "含税总金额"],
    },
    {
        "name": "sales_order_count",
        "business_name": "销售订单数",
        "table": "clean_jf_sale_order",
        "formula": "COUNT(DISTINCT {sale_order_no})",
        "aggregation": "count_distinct",
        "column": "sale_order_no",
        "time_column": "make_date",
        "default_grain": "month",
        "synonyms": ["订单数量", "订单数", "销售单数"],
    },
    {
        "name": "sales_quantity",
        "business_name": "销售数量",
        "table": "clean_jf_order_detail",
        "formula": "SUM({purchase_number})",
        "aggregation": "sum",
        "column": "purchase_number",
        "synonyms": ["订单数量", "明细数量", "总数量"],
    },
    {
        "name": "sales_detail_amount",
        "business_name": "销售明细金额",
        "table": "clean_jf_order_detail",
        "formula": "SUM({tax_amount})",
        "aggregation": "sum",
        "column": "tax_amount",
        "synonyms": ["明细金额", "含税金额", "销售金额"],
    },
    {
        "name": "avg_sales_unit_price",
        "business_name": "平均单价",
        "table": "clean_jf_order_detail",
        "formula": "SUM({tax_amount}) / NULLIF(SUM({purchase_number}), 0)",
        "aggregation": "custom",
        "column": "tax_amount",
        "synonyms": ["客单价", "平均销售单价", "单价"],
    },
    {
        "name": "dispatch_quantity",
        "business_name": "派工数量",
        "table": "clean_dispatch_order",
        "formula": "SUM({dispatch_number})",
        "aggregation": "sum",
        "column": "dispatch_number",
        "time_column": "make_date",
        "default_grain": "month",
        "synonyms": ["派工量", "任务数量"],
    },
    {
        "name": "dispatch_order_count",
        "business_name": "派工单数",
        "table": "clean_dispatch_order",
        "formula": "COUNT(DISTINCT {document_no})",
        "aggregation": "count_distinct",
        "column": "document_no",
        "time_column": "make_date",
        "default_grain": "month",
        "synonyms": ["派工订单数", "派工单量"],
    },
    {
        "name": "current_inventory_quantity",
        "business_name": "当前库存数量",
        "table": "clean_jf_now_details",
        "formula": "SUM({number})",
        "aggregation": "sum",
        "column": "number",
        "synonyms": ["库存数量", "现存数量", "当前库存"],
    },
    {
        "name": "inbound_quantity",
        "business_name": "入库数量",
        "table": "clean_jf_in_store_detail",
        "formula": "SUM({in_store_num})",
        "aggregation": "sum",
        "column": "in_store_num",
        "time_column": "create_time",
        "default_grain": "month",
        "synonyms": ["入库数", "入库量"],
    },
    {
        "name": "outbound_quantity",
        "business_name": "出库数量",
        "table": "clean_jf_out_store_detail",
        "formula": "SUM({out_store_num})",
        "aggregation": "sum",
        "column": "out_store_num",
        "time_column": "create_time",
        "default_grain": "month",
        "synonyms": ["出库数", "出库量"],
    },
    {
        "name": "unqualified_rate",
        "business_name": "不合格率",
        "table": "clean_finish_inspection_detail",
        "formula": "SUM({unqualified_number}) / NULLIF(SUM({accept_number}), 0)",
        "aggregation": "custom",
        "column": "unqualified_number",
        "time_column": "inspect_date",
        "default_grain": "month",
        "synonyms": ["质量异常率", "不良率", "完工检验不合格率"],
    },
    {
        "name": "inspection_exception_count",
        "business_name": "点检异常记录数",
        "table": "clean_dz_inspection_work",
        "formula": "COUNT(DISTINCT {billno})",
        "aggregation": "count_distinct",
        "column": "billno",
        "time_column": "inspection_finish_time",
        "default_grain": "month",
        "synonyms": ["报修记录数", "异常记录数", "点检记录数"],
    },
]


RELATIONSHIP_SPECS: list[dict[str, Any]] = [
    {
        "left_table": "clean_jf_order_detail",
        "left_column": "main_code",
        "right_table": "clean_jf_sale_order",
        "right_column": "sale_order_no",
        "relationship_type": "many_to_one",
        "confidence": 0.95,
        "description": "15题回归语义治理关系: 销售订单明细主表单号关联销售订单主表订单号。",
    },
    {
        "left_table": "clean_make_order_proofing_detail",
        "left_column": "main_code",
        "right_table": "clean_make_order_proofing",
        "right_column": "make_order_no",
        "relationship_type": "many_to_one",
        "confidence": 0.95,
        "description": "15题回归语义治理关系: 打样办单明细主表单号关联打样办单主表单号。",
    },
]


CORE_METRIC_NAMES = {item["name"] for item in METRIC_SPECS}
NOISY_GENERATED_METRIC_PREFIXES = (
    "avg_unit_price_",
    "sum_",
    "count_distinct_",
)


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    return [str(value)]


def _merge_synonyms(existing: Any, additions: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*_as_list(existing), *additions]:
        text = str(item or "").strip()
        key = _normalize(text)
        if text and key and key not in seen:
            seen.add(key)
            merged.append(text)
    return merged


def _should_replace_business_name(current: str, physical_name: str) -> bool:
    return not current or _normalize(current) == _normalize(physical_name)


def _set_if_changed(obj: Any, attr: str, value: Any, *, apply: bool) -> bool:
    if getattr(obj, attr) == value:
        return False
    if apply:
        setattr(obj, attr, value)
    return True


def apply_label_patch(
    obj: Any,
    *,
    business_name: str,
    description: str = "",
    synonyms: list[str] | None = None,
    apply: bool,
) -> bool:
    changed = False
    desired_synonyms = list(synonyms or [])
    if _should_replace_business_name(getattr(obj, "business_name", ""), obj.physical_name):
        changed = _set_if_changed(obj, "business_name", business_name, apply=apply) or changed
    elif _normalize(getattr(obj, "business_name", "")) != _normalize(business_name):
        desired_synonyms.insert(0, business_name)
    if description and not getattr(obj, "description", None):
        changed = _set_if_changed(obj, "description", description, apply=apply) or changed
    merged_synonyms = _merge_synonyms(getattr(obj, "synonyms", []), desired_synonyms)
    changed = _set_if_changed(obj, "synonyms", merged_synonyms, apply=apply) or changed
    return changed


def _metric_formula_fields(formula: str) -> list[str]:
    return re.findall(r"\{([A-Za-z_][\w$]*|[\u4e00-\u9fff][\w\u4e00-\u9fff]*)\}", formula or "")


async def _load_datasource(session, workspace_id: str, datasource_id: int | None) -> SemanticDatasourceModel:
    stmt = select(SemanticDatasourceModel).where(
        SemanticDatasourceModel.workspace_id == workspace_id,
        SemanticDatasourceModel.is_active == True,  # noqa: E712
    )
    if datasource_id is not None:
        stmt = stmt.where(SemanticDatasourceModel.id == datasource_id)
    stmt = stmt.order_by(SemanticDatasourceModel.updated_at.desc())
    datasource = (await session.execute(stmt)).scalars().first()
    if not datasource:
        raise SystemExit("No active semantic datasource found for workspace.")
    return datasource


async def seed_regression_assets(
    *,
    workspace_id: str,
    datasource_id: int | None,
    apply: bool,
) -> dict[str, Any]:
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        datasource = await _load_datasource(session, workspace_id, datasource_id)
        tables = list(
            (
                await session.execute(
                    select(SemanticTableModel).where(
                        SemanticTableModel.workspace_id == workspace_id,
                        SemanticTableModel.datasource_id == datasource.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        columns = list(
            (
                await session.execute(
                    select(SemanticColumnModel).where(
                        SemanticColumnModel.workspace_id == workspace_id,
                        SemanticColumnModel.datasource_id == datasource.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        metrics = list(
            (
                await session.execute(
                    select(SemanticMetricModel).where(
                        SemanticMetricModel.workspace_id == workspace_id,
                        SemanticMetricModel.datasource_id == datasource.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        relationships = list(
            (
                await session.execute(
                    select(SemanticRelationshipModel).where(
                        SemanticRelationshipModel.workspace_id == workspace_id,
                        SemanticRelationshipModel.datasource_id == datasource.id,
                    )
                )
            )
            .scalars()
            .all()
        )

        table_by_name = {table.physical_name: table for table in tables}
        columns_by_table: dict[str, dict[str, SemanticColumnModel]] = {}
        for column in columns:
            columns_by_table.setdefault(column.physical_table, {})[column.physical_name] = column
        metric_by_name = {metric.name: metric for metric in metrics}

        active_table_ids = {
            table.id for table in tables if table.status == "confirmed" and table.is_queryable
        }
        polluted_metrics = [
            metric for metric in metrics
            if metric.status == "confirmed" and metric.is_queryable and metric.table_id not in active_table_ids
        ]
        polluted_relationships = [
            rel for rel in relationships
            if rel.status == "confirmed"
            and rel.is_queryable
            and (rel.left_table_id not in active_table_ids or rel.right_table_id not in active_table_ids)
        ]

        summary: dict[str, Any] = {
            "apply": apply,
            "workspace_id": workspace_id,
            "datasource_id": datasource.id,
            "polluted_metric_count": len(polluted_metrics),
            "disabled_metric_count": 0,
            "disabled_relationship_count": 0,
            "updated_table_count": 0,
            "updated_column_count": 0,
            "created_metric_count": 0,
            "updated_metric_count": 0,
            "disabled_noisy_metric_count": 0,
            "created_relationship_count": 0,
            "updated_relationship_count": 0,
            "skipped": [],
            "warnings": [],
        }

        for metric in polluted_metrics:
            changed = False
            changed = _set_if_changed(metric, "status", "disabled", apply=apply) or changed
            changed = _set_if_changed(metric, "is_queryable", False, apply=apply) or changed
            if changed:
                summary["disabled_metric_count"] += 1
        for rel in polluted_relationships:
            changed = False
            changed = _set_if_changed(rel, "status", "disabled", apply=apply) or changed
            changed = _set_if_changed(rel, "is_queryable", False, apply=apply) or changed
            if changed:
                summary["disabled_relationship_count"] += 1

        regression_table_ids = {
            table_by_name[name].id
            for name in TABLE_SEMANTICS
            if name in table_by_name
        }
        noisy_metrics = [
            metric for metric in metrics
            if metric.table_id in regression_table_ids
            and metric.name not in CORE_METRIC_NAMES
            and metric.status == "confirmed"
            and metric.is_queryable
            and metric.name.startswith(NOISY_GENERATED_METRIC_PREFIXES)
        ]
        for metric in noisy_metrics:
            changed = False
            changed = _set_if_changed(metric, "status", "disabled", apply=apply) or changed
            changed = _set_if_changed(metric, "is_queryable", False, apply=apply) or changed
            if changed:
                summary["disabled_noisy_metric_count"] += 1

        for physical_name, spec in TABLE_SEMANTICS.items():
            table = table_by_name.get(physical_name)
            if not table:
                summary["warnings"].append(f"missing table: {physical_name}")
                continue
            changed = False
            changed = apply_label_patch(
                table,
                business_name=spec["business_name"],
                description=spec.get("description", ""),
                synonyms=[physical_name, *spec.get("synonyms", [])],
                apply=apply,
            ) or changed
            changed = _set_if_changed(table, "status", "confirmed", apply=apply) or changed
            changed = _set_if_changed(table, "is_queryable", True, apply=apply) or changed
            if changed:
                summary["updated_table_count"] += 1

        for table_name, field_specs in FIELD_SEMANTICS.items():
            table = table_by_name.get(table_name)
            if not table:
                continue
            table_columns = columns_by_table.get(table_name, {})
            for physical_name, spec in field_specs.items():
                column = table_columns.get(physical_name)
                if not column:
                    summary["warnings"].append(f"missing column: {table_name}.{physical_name}")
                    continue
                changed = False
                changed = apply_label_patch(
                    column,
                    business_name=spec["business_name"],
                    description=spec.get("description", ""),
                    synonyms=[physical_name, *spec.get("synonyms", [])],
                    apply=apply,
                ) or changed
                changed = _set_if_changed(column, "status", "confirmed", apply=apply) or changed
                changed = _set_if_changed(column, "is_queryable", True, apply=apply) or changed
                if changed:
                    summary["updated_column_count"] += 1

        for spec in METRIC_SPECS:
            table = table_by_name.get(spec["table"])
            if not table:
                summary["warnings"].append(f"skip metric {spec['name']}: missing table {spec['table']}")
                continue
            table_columns = columns_by_table.get(spec["table"], {})
            missing_fields = [field for field in _metric_formula_fields(spec["formula"]) if field not in table_columns]
            if missing_fields:
                summary["warnings"].append(
                    f"skip metric {spec['name']}: missing formula fields {', '.join(missing_fields)}"
                )
                continue
            column = table_columns.get(spec.get("column", ""))
            time_column = table_columns.get(spec.get("time_column", ""))
            existing = metric_by_name.get(spec["name"])
            metric_synonyms = _merge_synonyms(
                existing.synonyms if existing else [],
                [spec["name"], spec["business_name"], *spec.get("synonyms", [])],
            )
            payload = {
                "workspace_id": workspace_id,
                "datasource_id": datasource.id,
                "name": spec["name"],
                "business_name": spec["business_name"],
                "description": spec.get("description") or f"15题回归语义治理指标: {spec['business_name']}",
                "formula": spec["formula"],
                "aggregation": spec.get("aggregation"),
                "table_id": table.id,
                "column_id": column.id if column else None,
                "time_column_id": time_column.id if time_column else None,
                "default_grain": spec.get("default_grain"),
                "synonyms": metric_synonyms,
                "status": "confirmed",
                "is_queryable": True,
            }
            if existing:
                changed = False
                for attr, value in payload.items():
                    changed = _set_if_changed(existing, attr, value, apply=apply) or changed
                if changed:
                    summary["updated_metric_count"] += 1
            else:
                summary["created_metric_count"] += 1
                if apply:
                    session.add(SemanticMetricModel(**payload))

        for spec in RELATIONSHIP_SPECS:
            left_table = table_by_name.get(spec["left_table"])
            right_table = table_by_name.get(spec["right_table"])
            if not left_table:
                summary["warnings"].append(
                    f"skip relationship {spec['left_table']}.{spec['left_column']} -> "
                    f"{spec['right_table']}.{spec['right_column']}: missing left table"
                )
                continue
            if not right_table:
                summary["warnings"].append(
                    f"skip relationship {spec['left_table']}.{spec['left_column']} -> "
                    f"{spec['right_table']}.{spec['right_column']}: missing right table"
                )
                continue

            left_column = columns_by_table.get(spec["left_table"], {}).get(spec["left_column"])
            right_column = columns_by_table.get(spec["right_table"], {}).get(spec["right_column"])
            if not left_column:
                summary["warnings"].append(
                    f"skip relationship {spec['left_table']}.{spec['left_column']} -> "
                    f"{spec['right_table']}.{spec['right_column']}: missing left column"
                )
                continue
            if not right_column:
                summary["warnings"].append(
                    f"skip relationship {spec['left_table']}.{spec['left_column']} -> "
                    f"{spec['right_table']}.{spec['right_column']}: missing right column"
                )
                continue

            existing = next(
                (
                    rel for rel in relationships
                    if (
                        rel.left_column_id == left_column.id
                        and rel.right_column_id == right_column.id
                    )
                    or (
                        rel.left_column_id == right_column.id
                        and rel.right_column_id == left_column.id
                    )
                ),
                None,
            )
            payload = {
                "workspace_id": workspace_id,
                "datasource_id": datasource.id,
                "left_table_id": left_table.id,
                "right_table_id": right_table.id,
                "left_column_id": left_column.id,
                "right_column_id": right_column.id,
                "relationship_type": spec.get("relationship_type") or "many_to_one",
                "confidence": float(spec.get("confidence") or 0.9),
                "status": "confirmed",
                "is_queryable": True,
                "description": spec.get("description") or "15题回归语义治理关系",
            }
            if existing:
                changed = False
                for attr, value in payload.items():
                    changed = _set_if_changed(existing, attr, value, apply=apply) or changed
                if changed:
                    summary["updated_relationship_count"] += 1
            else:
                summary["created_relationship_count"] += 1
                if apply:
                    session.add(SemanticRelationshipModel(**payload))

        if apply:
            await session.flush()

        summary["skipped"] = sorted(summary["skipped"])
        summary["warnings"] = sorted(set(summary["warnings"]))
        return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True, help="Workspace id to seed.")
    parser.add_argument("--datasource-id", type=int, default=None, help="Optional semantic datasource id.")
    parser.add_argument("--apply", action="store_true", help="Persist changes. Omit for dry-run.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = asyncio.run(
        seed_regression_assets(
            workspace_id=args.workspace_id,
            datasource_id=args.datasource_id,
            apply=args.apply,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
