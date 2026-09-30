"""Semantic NL2SQL MVP service.

This module implements the first-stage semantic query path while keeping the
existing SqlWorker contract intact: it returns SQL, rows, columns and artifacts
metadata, but only after resolving confirmed semantic objects and permissions.
"""

from __future__ import annotations

import logging
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, inspect, or_, select, update
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.core.db.mysql_connection_policy import create_mysql_engine
from app.core.db.read_only_executor import ReadOnlyExecutor
from app.core.llm.async_llm import get_async_llm
from app.models.auth.organization import DepartmentModel
from app.models.auth.rbac import RoleModel, UserModel
from app.models.config.db_config import (
    UserDBConfig,
    get_user_db_config_async,
    get_workspace_admin_db_config_async,
    get_workspace_db_config_async,
)
from app.models.config.semantic import (
    SemanticColumn,
    SemanticColumnProfileModel,
    SemanticColumnModel,
    SemanticBusinessSuggestion,
    SemanticBusinessSuggestionModel,
    SemanticDatasource,
    SemanticDatasourceModel,
    SemanticGovernanceEventModel,
    SemanticMetric,
    SemanticMetricModel,
    SemanticQueryRun,
    SemanticQueryRunModel,
    SemanticRelationship,
    SemanticRelationshipModel,
    SemanticTable,
    SemanticTableModel,
)
from app.services.sql_result_diagnostics import (
    analyze_sql_result,
    append_diagnostics_text,
)
from app.services.semantic_access_policy_service import (
    AccessPolicyError,
    get_semantic_access_policy_service,
)

logger = logging.getLogger(__name__)
settings = get_settings()


QueryType = Literal["detail", "aggregate", "topn", "trend", "compare"]


@dataclass(frozen=True)
class _MetricSuggestion:
    name: str
    business_name: str
    description: str
    formula: str
    aggregation: str
    table_id: int
    column_id: Optional[int]
    time_column_id: Optional[int]
    default_grain: Optional[str]
    synonyms: list[str]
    status: str
    confidence: float


@dataclass(frozen=True)
class _RelationshipSuggestion:
    left_table_id: int
    right_table_id: int
    left_column_id: int
    right_column_id: int
    relationship_type: str
    confidence: float
    status: str
    is_queryable: bool
    description: str


@dataclass(frozen=True)
class _SemanticMatch:
    object_type: Literal["table", "column", "metric"]
    object_id: int
    output_name: str
    table_id: int
    term: str
    start: int
    end: int
    source: Literal["business_name", "technical_name", "synonym", "loose_table"]
    source_rank: int
    confidence: float


SEMANTIC_SAFE_ERROR_TYPES = {
    "permission_denied",
    "permission_rewrite_required",
    "semantic_clarification_required",
    "permission_check_failed",
    "sensitive_object",
    "readonly_required",
    "readonly_execution_failed",
    "unsafe_sql",
    "semantic_disabled",
    "metric_ambiguous",
    "semantic_model_incomplete",
}

SEMANTIC_TERM_ZH = {
    "clean": "",
    "jf": "",
    "clue": "线索",
    "follow": "跟进",
    "details": "明细",
    "detail": "明细",
    "records": "记录",
    "record": "记录",
    "method": "方式",
    "content": "内容",
    "time": "时间",
    "person": "人员",
    "dispatch": "派单",
    "sale": "销售",
    "sales": "销售",
    "order": "订单",
    "incoming": "来料",
    "material": "物料",
    "acceptance": "接收",
    "equipment": "设备",
    "file": "档案",
    "maintenance": "维护",
    "inspection": "检验",
    "work": "工单",
    "main": "主",
    "code": "编码",
    "no": "编号",
    "number": "编号",
    "id": "ID",
    "name": "名称",
    "type": "类型",
    "status": "状态",
    "state": "状态",
    "date": "日期",
    "create": "创建",
    "created": "创建",
    "update": "更新",
    "updated": "更新",
    "delete": "删除",
    "deleted": "删除",
    "user": "用户",
    "customer": "客户",
    "phone": "电话",
    "mobile": "手机号",
    "address": "地址",
    "amount": "金额",
    "price": "价格",
    "quantity": "数量",
    "qty": "数量",
    "total": "合计",
    "count": "数量",
    "num": "数量",
    "finish": "完工",
    "finished": "完工",
    "product": "产品",
    "item": "项目",
    "flag": "标记",
    "remark": "备注",
    "description": "说明",
    "desc": "说明",
    "dept": "部门",
    "department": "部门",
    "org": "组织",
    "organization": "组织",
    "region": "区域",
    "area": "区域",
    "city": "城市",
    "province": "省份",
    "source": "来源",
    "channel": "渠道",
    "level": "等级",
    "category": "分类",
    "class": "分类",
}


class SemanticQueryError(Exception):
    """Structured semantic query failure."""

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        retryable: bool = False,
        safe_to_fallback: bool = True,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.retryable = retryable
        self.safe_to_fallback = safe_to_fallback and error_type not in SEMANTIC_SAFE_ERROR_TYPES
        self.details = details or {}


class IntentQuery(BaseModel):
    """Stable structured query object extracted from user language."""

    query_type: QueryType = "detail"
    tables: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    # ``None`` preserves the fail-closed meaning for legacy/manual intents:
    # every supplied dimension is treated as explicit.  Extracted intents set
    # this to a list (possibly empty) so default projections can be separated
    # from fields the user actually named.
    explicit_dimensions: Optional[list[str]] = None
    filters: list[dict[str, Any]] = Field(default_factory=list)
    time_range: Optional[str] = None
    order_by: list[dict[str, str]] = Field(default_factory=list)
    limit: int = 100
    confidence: float = 0.65
    clarification_items: list[str] = Field(default_factory=list)
    selected_time_column_id: Optional[int] = None
    # Persisted only in the semantic query audit record.  It is excluded from
    # ordinary API/chat payloads so internal candidate IDs are not exposed.
    matching_trace: list[dict[str, Any]] = Field(default_factory=list, exclude=True)


class SemanticClarificationOption(BaseModel):
    id: str
    label: str
    selection_patch: dict[str, Any] = Field(default_factory=dict)


class SemanticClarification(BaseModel):
    kind: str
    message: str
    options: list[SemanticClarificationOption] = Field(default_factory=list)
    blocked_objects: list[dict[str, Any]] = Field(default_factory=list)
    intent_patch: dict[str, Any] = Field(default_factory=dict)
    selection_patch: dict[str, Any] = Field(default_factory=dict)
    original_intent: dict[str, Any] = Field(default_factory=dict)


class SemanticPlan(BaseModel):
    intent: IntentQuery
    datasource_id: int
    table_ids: list[int] = Field(default_factory=list)
    metric_ids: list[int] = Field(default_factory=list)
    column_ids: list[int] = Field(default_factory=list)
    permission_column_ids: list[int] = Field(default_factory=list)
    relationship_ids: list[int] = Field(default_factory=list)
    permission_actions: list[dict[str, Any]] = Field(default_factory=list)
    user_access: dict[str, Any] = Field(default_factory=dict)
    sql: str = ""
    referenced_tables: list[str] = Field(default_factory=list)


class SemanticExecutionResult(BaseModel):
    success: bool
    sql: str = ""
    intent: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    data: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    result_text: str = ""
    referenced_tables: list[str] = Field(default_factory=list)
    execution_time_ms: int = 0
    run_id: Optional[int] = None
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    sql_example_match: Optional[dict[str, Any]] = None


@dataclass
class SemanticCatalog:
    datasource: SemanticDatasource
    tables: list[SemanticTable]
    columns: list[SemanticColumn]
    metrics: list[SemanticMetric]
    relationships: list[SemanticRelationship]
    table_by_id: dict[int, SemanticTable] = field(default_factory=dict)
    columns_by_table: dict[int, list[SemanticColumn]] = field(default_factory=dict)
    column_by_id: dict[int, SemanticColumn] = field(default_factory=dict)
    metric_by_id: dict[int, SemanticMetric] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.table_by_id = {table.id: table for table in self.tables}
        self.column_by_id = {column.id: column for column in self.columns}
        self.metric_by_id = {metric.id: metric for metric in self.metrics}
        grouped: dict[int, list[SemanticColumn]] = {}
        for column in self.columns:
            grouped.setdefault(column.table_id, []).append(column)
        self.columns_by_table = grouped


def _is_active_semantic_table(table: Any) -> bool:
    return bool(
        table
        and table.status == "confirmed"
        and table.is_queryable
        and getattr(table, "sync_state", "current") == "current"
    )


def _is_active_table_id(table_id: int, catalog: SemanticCatalog) -> bool:
    return _is_active_semantic_table(catalog.table_by_id.get(table_id))


def _is_active_semantic_column(column: Any, catalog: SemanticCatalog) -> bool:
    return bool(
        column
        and column.status == "confirmed"
        and column.is_queryable
        and getattr(column, "sync_state", "current") == "current"
        and _is_active_table_id(column.table_id, catalog)
    )


def _is_active_semantic_metric(metric: Any, catalog: SemanticCatalog) -> bool:
    return bool(
        metric
        and metric.status == "confirmed"
        and metric.is_queryable
        and getattr(metric, "sync_state", "current") == "current"
        and _is_active_table_id(metric.table_id, catalog)
    )


def _active_scan_objects(
    tables: list[Any],
    columns: list[Any],
) -> tuple[list[Any], dict[int, list[Any]]]:
    active_table_ids = {table.id for table in tables if _is_active_semantic_table(table)}
    columns_by_table: dict[int, list[Any]] = {}
    for column in columns:
        if (
            column.table_id not in active_table_ids
            or column.status != "confirmed"
            or not column.is_queryable
            or getattr(column, "sync_state", "current") != "current"
        ):
            continue
        columns_by_table.setdefault(column.table_id, []).append(column)
    for table_columns in columns_by_table.values():
        table_columns.sort(key=lambda column: column.ordinal_position)
    return [table for table in tables if table.id in active_table_ids], columns_by_table


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_term(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _terms_for(*values: str, synonyms: Optional[list[str]] = None) -> list[str]:
    terms = []
    for value in values:
        if value:
            terms.append(str(value))
    terms.extend(str(item) for item in (synonyms or []) if item)
    for term in list(terms):
        terms.extend(
            part.strip()
            for part in re.split(r"[,，;；、\n]+", term)
            if part.strip() and part.strip() != term
        )
    seen = set()
    output = []
    for term in terms:
        key = _normalize_term(term)
        if key and key not in seen:
            seen.add(key)
            output.append(term)
    return output


def _contains_term(text: str, terms: list[str]) -> bool:
    normalized_text = _normalize_term(text)
    return any(_normalize_term(term) in normalized_text for term in terms if term)


def _compact_semantic_term(term: str) -> str:
    normalized = _normalize_term(term)
    for suffix in ("数据表", "记录表", "明细表", "信息表", "情况表", "表", "记录", "明细", "情况", "详情"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
            normalized = normalized[: -len(suffix)]
    return normalized


def _contains_loose_term(text: str, terms: list[str]) -> bool:
    normalized_text = _normalize_term(text)
    for term in terms:
        normalized_term = _normalize_term(term)
        compact_term = _compact_semantic_term(term)
        if normalized_term and normalized_term in normalized_text:
            return True
        if compact_term and compact_term in normalized_text:
            return True
        if compact_term and len(compact_term) >= 4:
            for length in range(len(compact_term), max(3, len(compact_term) - 4), -1):
                if compact_term[:length] in normalized_text:
                    return True
    return False


def _is_numeric_type(data_type: str) -> bool:
    normalized = str(data_type or "").lower()
    return any(
        token in normalized
        for token in ("int", "decimal", "numeric", "double", "float", "real")
    )


def _is_time_type(data_type: str) -> bool:
    normalized = str(data_type or "").lower()
    return any(token in normalized for token in ("date", "time", "year", "timestamp"))


def _is_time_column(column: SemanticColumn) -> bool:
    if _is_time_type(column.data_type):
        return True
    text = " ".join(
        [column.physical_name, column.business_name, column.description or "", *column.synonyms]
    ).lower()
    english_text = re.sub(r"[_\W]+", " ", text)
    return bool(
        re.search(r"\b(?:date|time|datetime|timestamp|year|month)\b", english_text)
        or any(token in text for token in ("日期", "时间", "月份", "年度", "年份"))
    )


def _is_person_column(column: SemanticColumn) -> bool:
    text = _normalize_term(
        " ".join([column.physical_name, column.business_name, column.description or "", *column.synonyms])
    )
    return any(
        token in text
        for token in ("person", "user", "name", "by", "人员", "人", "姓名", "负责人", "联系人", "员工")
    )


def _looks_like_identifier_column(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    return normalized == "id" or normalized.endswith("_id")


_DIMENSION_FIELD_TOKENS = {
    "id",
    "code",
    "no",
    "serial",
    "category",
    "grade",
    "status",
    "state",
    "type",
    "kind",
    "color",
    "colour",
    "spec",
    "size",
    "name",
    "remark",
    "description",
}

_ADDITIVE_MEASURE_TOKENS = {
    "amount",
    "total",
    "cost",
    "quantity",
    "qty",
    "number",
    "num",
    "count",
    "pieces",
    "length",
    "width",
    "height",
    "weight",
    "area",
    "output",
    "accept",
    "received",
    "purchase",
    "dispatch",
    "unqualified",
    "qualified",
    "need",
    "plan",
}

_AVERAGE_MEASURE_TOKENS = {
    "unit",
    "single",
    "salary",
    "price",
    "rate",
    "ratio",
    "percent",
    "percentage",
}

_COUNT_DISTINCT_SUFFIXES = (
    "_order_code",
    "_order_no",
    "_number",
    "document_no",
    "billno",
    "make_order_no",
    "bom_no",
    "order_number",
)

_PUBLIC_JOIN_CODE_FIELDS = {
    "article_no",
    "colour_no",
    "color_no",
    "customer_code",
    "material_code",
    "mcode",
    "device_code",
    "supplier_code",
    "film_no",
}


def _column_tokens(name: str) -> set[str]:
    return set(_split_identifier(name))


def _has_any_token(name: str, tokens: set[str]) -> bool:
    normalized = str(name or "").strip().lower()
    parts = _column_tokens(normalized)
    for token in tokens:
        if token in parts:
            return True
        if len(token) >= 3 and token in normalized:
            return True
    return False


def _looks_like_dimension_measure_exclusion(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    if _looks_like_identifier_column(normalized):
        return True
    if normalized in {"number", "count"}:
        return False
    if normalized.endswith("_no") or normalized.endswith("_code"):
        return True
    return _has_any_token(normalized, _DIMENSION_FIELD_TOKENS)


def _looks_like_count_distinct_column(column: SemanticColumn) -> bool:
    normalized = str(column.physical_name or "").strip().lower()
    if normalized in {"number", "serial_number"}:
        return False
    return any(normalized == suffix or normalized.endswith(suffix) for suffix in _COUNT_DISTINCT_SUFFIXES)


def _metric_kind_for_column(column: SemanticColumn) -> Optional[str]:
    normalized = str(column.physical_name or "").strip().lower()
    if _looks_like_count_distinct_column(column) and not _is_numeric_type(column.data_type):
        return "count_distinct"
    if not _is_numeric_type(column.data_type):
        return None
    if _looks_like_dimension_measure_exclusion(normalized):
        return None
    if normalized in {"single_salary", "unit_price"}:
        return "avg"
    if _has_any_token(normalized, _AVERAGE_MEASURE_TOKENS) and not normalized.startswith(("total_", "sum_")):
        return "avg"
    if _has_any_token(normalized, _ADDITIVE_MEASURE_TOKENS):
        return "sum"
    return None


def _metric_business_name(prefix: str, column: SemanticColumn) -> str:
    label = column.business_name or column.physical_name
    return f"{prefix}{label}"[:128]


def _time_column_for_table(columns: list[SemanticColumn]) -> Optional[SemanticColumn]:
    preferred_terms = ("make_date", "order_date", "bill_date", "create_date", "created_at", "date", "time")
    for term in preferred_terms:
        match = next((column for column in columns if term in column.physical_name.lower()), None)
        if match:
            return match
    return next((column for column in columns if _is_time_column(column)), None)


def _metric_description(kind: str, confidence: float, evidence: str) -> str:
    status = "auto-confirmed" if confidence >= 0.75 else "suggested"
    return f"Generated by semantic scan ({status}, kind={kind}, confidence={confidence:.2f}). Evidence: {evidence}"


def _suggest_metrics_for_columns(columns_by_table: dict[int, list[SemanticColumn]]) -> list[_MetricSuggestion]:
    suggestions: list[_MetricSuggestion] = []
    existing_names: set[str] = set()
    for table_id, columns in columns_by_table.items():
        time_column = _time_column_for_table(columns)
        columns_by_name = {column.physical_name.lower(): column for column in columns}
        additive_columns = []
        for column in columns:
            kind = _metric_kind_for_column(column)
            if not kind:
                continue
            if kind == "count_distinct":
                aggregation = "count_distinct"
                formula = f"COUNT(DISTINCT {{{column.physical_name}}})"
                prefix = "去重计数"
                confidence = 0.78
            elif kind == "avg":
                aggregation = "avg"
                formula = f"AVG({{{column.physical_name}}})"
                prefix = "平均"
                confidence = 0.82
            else:
                aggregation = "sum"
                formula = f"SUM({{{column.physical_name}}})"
                prefix = "合计"
                confidence = 0.86
                additive_columns.append(column)

            metric_name = f"{aggregation}_{column.physical_table}_{column.physical_name}"[:128]
            if metric_name in existing_names:
                continue
            existing_names.add(metric_name)
            suggestions.append(
                _MetricSuggestion(
                    name=metric_name,
                    business_name=_metric_business_name(prefix, column),
                    description=_metric_description(kind, confidence, column.physical_name),
                    formula=formula,
                    aggregation=aggregation,
                    table_id=table_id,
                    column_id=column.id,
                    time_column_id=time_column.id if time_column else None,
                    default_grain="month" if time_column else None,
                    synonyms=[column.business_name, column.physical_name],
                    status="confirmed" if confidence >= 0.75 else "suggested",
                    confidence=confidence,
                )
            )

        amount_column = next(
            (
                column for column in additive_columns
                if any(token in column.physical_name.lower() for token in ("amount", "total_price", "total_amount", "money"))
            ),
            None,
        )
        quantity_column = next(
            (
                column for column in additive_columns
                if any(token in column.physical_name.lower() for token in ("quantity", "qty", "number", "count"))
                and "unqualified" not in column.physical_name.lower()
            ),
            None,
        )
        if amount_column and quantity_column:
            metric_name = f"avg_unit_price_{amount_column.physical_table}"[:128]
            if metric_name not in existing_names:
                existing_names.add(metric_name)
                suggestions.append(
                    _MetricSuggestion(
                        name=metric_name,
                        business_name="平均单价",
                        description=_metric_description("derived_avg_unit_price", 0.80, f"{amount_column.physical_name}/{quantity_column.physical_name}"),
                        formula=f"SUM({{{amount_column.physical_name}}}) / NULLIF(SUM({{{quantity_column.physical_name}}}), 0)",
                        aggregation="custom",
                        table_id=table_id,
                        column_id=amount_column.id,
                        time_column_id=time_column.id if time_column else None,
                        default_grain="month" if time_column else None,
                        synonyms=["平均单价", "客单价", amount_column.physical_name, quantity_column.physical_name],
                        status="confirmed",
                        confidence=0.80,
                    )
                )

        unqualified_column = columns_by_name.get("unqualified_number")
        denominator_column = (
            columns_by_name.get("accept_number")
            or columns_by_name.get("inspection_number")
            or columns_by_name.get("qualified_number")
        )
        if unqualified_column and denominator_column and _is_numeric_type(unqualified_column.data_type) and _is_numeric_type(denominator_column.data_type):
            metric_name = f"unqualified_rate_{unqualified_column.physical_table}"[:128]
            if metric_name not in existing_names:
                existing_names.add(metric_name)
                suggestions.append(
                    _MetricSuggestion(
                        name=metric_name,
                        business_name="不合格率",
                        description=_metric_description("derived_unqualified_rate", 0.84, f"{unqualified_column.physical_name}/{denominator_column.physical_name}"),
                        formula=f"SUM({{{unqualified_column.physical_name}}}) / NULLIF(SUM({{{denominator_column.physical_name}}}), 0)",
                        aggregation="custom",
                        table_id=table_id,
                        column_id=unqualified_column.id,
                        time_column_id=time_column.id if time_column else None,
                        default_grain="month" if time_column else None,
                        synonyms=["不合格率", "质量异常率", unqualified_column.physical_name],
                        status="confirmed",
                        confidence=0.84,
                    )
                )
    return suggestions


def _parent_table_name_for_detail(table_name: str) -> Optional[str]:
    normalized = str(table_name or "").strip().lower()
    suffixes = ("_detail", "_details", "_worklist", "_list")
    for suffix in suffixes:
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return None


def _parent_key_priority(column_name: str) -> int:
    name = column_name.lower()
    if name.endswith("_order_code"):
        return 0
    priorities = {
        "document_no": 1,
        "billno": 2,
        "make_order_no": 3,
        "bom_no": 4,
        "order_number": 5,
        "source_code": 6,
        "id": 9,
    }
    return priorities.get(name, 20)


def _child_link_priority(column_name: str) -> int:
    priorities = {
        "main_code": 0,
        "source_code": 1,
        "order_number": 2,
    }
    return priorities.get(column_name.lower(), 20)


def _choose_parent_key(columns: list[SemanticColumn]) -> Optional[SemanticColumn]:
    candidates = [
        column for column in columns
        if _parent_key_priority(column.physical_name) < 20
    ]
    candidates.sort(key=lambda column: (_parent_key_priority(column.physical_name), column.ordinal_position))
    return candidates[0] if candidates else None


def _choose_child_link(columns: list[SemanticColumn]) -> Optional[SemanticColumn]:
    candidates = [
        column for column in columns
        if _child_link_priority(column.physical_name) < 20
    ]
    candidates.sort(key=lambda column: (_child_link_priority(column.physical_name), column.ordinal_position))
    return candidates[0] if candidates else None


def _looks_like_dimension_table(table_name: str) -> bool:
    normalized = table_name.lower()
    return any(token in normalized for token in ("customer", "supplier", "material", "equipment_file", "basic", "profile"))


def _suggest_relationships_for_columns(
    tables: list[SemanticTable],
    columns_by_table: dict[int, list[SemanticColumn]],
) -> list[_RelationshipSuggestion]:
    suggestions: list[_RelationshipSuggestion] = []
    seen: set[tuple[int, int, int, int]] = set()
    table_by_name = {table.physical_name.lower(): table for table in tables if table.status != "disabled"}
    table_by_id = {table.id: table for table in tables}

    for child_table in tables:
        parent_name = _parent_table_name_for_detail(child_table.physical_name)
        if not parent_name:
            continue
        parent_table = table_by_name.get(parent_name)
        if not parent_table:
            continue
        child_column = _choose_child_link(columns_by_table.get(child_table.id, []))
        parent_column = _choose_parent_key(columns_by_table.get(parent_table.id, []))
        if not child_column or not parent_column:
            continue
        rel_key = (child_table.id, parent_table.id, child_column.id, parent_column.id)
        if rel_key in seen:
            continue
        seen.add(rel_key)
        suggestions.append(
            _RelationshipSuggestion(
                left_table_id=child_table.id,
                right_table_id=parent_table.id,
                left_column_id=child_column.id,
                right_column_id=parent_column.id,
                relationship_type="many_to_one",
                confidence=0.90,
                status="confirmed",
                is_queryable=True,
                description=(
                    "Generated by semantic scan: high-confidence parent/detail relationship "
                    f"{child_table.physical_name}.{child_column.physical_name} -> "
                    f"{parent_table.physical_name}.{parent_column.physical_name}"
                ),
            )
        )

    columns_by_name: dict[str, list[SemanticColumn]] = {}
    for columns in columns_by_table.values():
        for column in columns:
            name = column.physical_name.lower()
            if name in _PUBLIC_JOIN_CODE_FIELDS:
                columns_by_name.setdefault(name, []).append(column)

    for name, columns in columns_by_name.items():
        dimension_columns = []
        for column in columns:
            table = table_by_id.get(column.table_id)
            if table and _looks_like_dimension_table(table.physical_name):
                dimension_columns.append(column)
        if not dimension_columns:
            continue
        for dimension_column in dimension_columns[:3]:
            dimension_table = table_by_id.get(dimension_column.table_id)
            if not dimension_table:
                continue
            for fact_column in columns[:20]:
                if fact_column.table_id == dimension_column.table_id:
                    continue
                fact_table = table_by_id.get(fact_column.table_id)
                if not fact_table or _looks_like_dimension_table(fact_table.physical_name):
                    continue
                rel_key = (fact_column.table_id, dimension_column.table_id, fact_column.id, dimension_column.id)
                if rel_key in seen:
                    continue
                seen.add(rel_key)
                suggestions.append(
                    _RelationshipSuggestion(
                        left_table_id=fact_column.table_id,
                        right_table_id=dimension_column.table_id,
                        left_column_id=fact_column.id,
                        right_column_id=dimension_column.id,
                        relationship_type="many_to_one",
                        confidence=0.58,
                        status="suggested",
                        is_queryable=True,
                        description=(
                            "Generated by semantic scan: low-confidence shared business code candidate "
                            f"{fact_table.physical_name}.{fact_column.physical_name} -> "
                            f"{dimension_table.physical_name}.{dimension_column.physical_name}"
                        ),
                    )
                )
    return suggestions


def _should_auto_disable_table(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return True
    system_prefixes = (
        "sys_",
        "mysql_",
        "information_schema",
        "performance_schema",
    )
    if normalized.startswith(system_prefixes):
        return True
    return bool(
        re.search(r"(^|_)(bak|backup|tmp|temp|test|copy|old)(_|[0-9]|$)", normalized)
        or re.search(r"(_bak[0-9]*|_backup[0-9]*|_tmp[0-9]*|_temp[0-9]*)$", normalized)
    )


def _is_sensitive_column_name(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return False
    sensitive_tokens = (
        "password",
        "passwd",
        "pwd",
        "token",
        "secret",
        "credential",
        "id_card",
        "identity",
        "phone",
        "mobile",
        "email",
        "salary",
        "bank",
        "account",
        "address",
        "身份证",
        "手机号",
        "电话",
        "邮箱",
        "密码",
        "工资",
        "薪资",
        "银行卡",
        "地址",
    )
    return any(token in normalized for token in sensitive_tokens)


def _split_identifier(name: str) -> list[str]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name or ""))
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value)
    return [part.lower() for part in value.strip("_").split("_") if part]


def _is_human_semantic(value: str, physical_name: str) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    return _normalize_term(normalized) != _normalize_term(physical_name)


def _suggest_business_name(physical_name: str, *, comment: str = "", object_type: str = "column") -> dict[str, Any]:
    comment = str(comment or "").strip()
    if re.search(r"[\u4e00-\u9fff]", comment):
        first_line = re.split(r"[\n\r。；;，,]", comment)[0].strip()
        if 2 <= len(first_line) <= 24:
            return {
                "business_name": first_line,
                "description": comment,
                "synonyms": [physical_name],
                "confidence": 0.9,
                "source": "rule",
                "evidence": {"comment": comment, "physical_name": physical_name},
            }

    tokens = _split_identifier(physical_name)
    translated = [SEMANTIC_TERM_ZH.get(token, token) for token in tokens]
    zh_parts = [part for part in translated if part]
    unknown_count = sum(1 for token in tokens if token not in SEMANTIC_TERM_ZH and not token.isdigit())
    business_name = "".join(zh_parts).strip()
    if object_type == "table" and business_name and not business_name.endswith(("表", "记录", "明细", "档案")):
        business_name = f"{business_name}表"
    confidence = 0.78 if tokens and unknown_count == 0 else 0.58 if tokens else 0.4
    synonyms = [physical_name]
    if "_" in physical_name:
        compact = physical_name.replace("_", "")
        if compact != physical_name:
            synonyms.append(compact)
    needs_manual_input = confidence < 0.65 or not business_name
    reason = (
        "命名规则置信度较低，请人工填写真实业务名和说明。"
        if needs_manual_input
        else "命名规则可作为初稿，请人工确认。"
    )
    return {
        "business_name": "" if needs_manual_input else business_name,
        "description": comment if comment and not needs_manual_input else "",
        "synonyms": synonyms[:4],
        "confidence": confidence,
        "source": "rule",
        "evidence": {
            "tokens": tokens,
            "unknown_count": unknown_count,
            "physical_name": physical_name,
            "needs_manual_input": needs_manual_input,
            "reason": reason,
        },
        "needs_manual_input": needs_manual_input,
        "reason": reason,
    }


def _compact_business_label(value: str) -> str:
    label = str(value or "").strip()
    for suffix in ("数据表", "记录表", "信息表", "明细表", "表"):
        if label.endswith(suffix) and len(label) > len(suffix):
            return label[: -len(suffix)]
    return label


def _table_suffix_for(tokens: list[str], columns: list[SemanticColumn]) -> str:
    if any(token in tokens for token in ("detail", "details")):
        return "明细"
    column_text = " ".join(column.physical_name.lower() for column in columns[:80])
    if any(token in column_text for token in ("amount", "price", "quantity", "qty", "number", "status", "state", "date", "time", "order")):
        return "记录"
    if any(token in tokens for token in ("record", "records", "inspection", "order", "dispatch", "acceptance")):
        return "记录"
    return "表"


def _suggest_table_business_semantics(
    physical_name: str,
    *,
    comment: str = "",
    columns: Optional[list[SemanticColumn]] = None,
) -> dict[str, Any]:
    columns = columns or []
    rule = _suggest_business_name(physical_name, comment=comment, object_type="table")
    tokens = _split_identifier(physical_name)
    translated = [SEMANTIC_TERM_ZH.get(token, "") for token in tokens if token in SEMANTIC_TERM_ZH or token.isdigit()]
    root = "".join(part for part in translated if part and part not in {"明细", "记录"})
    if not root and rule.get("business_name"):
        root = _compact_business_label(str(rule["business_name"]))

    business_name = str(rule.get("business_name") or "").strip()
    if root and (rule.get("needs_manual_input") or not business_name or not str(rule.get("description") or "").strip()):
        suffix = _table_suffix_for(tokens, columns)
        business_name = root if root.endswith(("表", "记录", "明细", "档案")) else f"{root}{suffix}"
        rule["business_name"] = business_name
        rule["confidence"] = max(float(rule.get("confidence") or 0.0), 0.72)
        rule["needs_manual_input"] = False
        rule["reason"] = "根据表名和字段结构生成了可复核的表语义建议。"

    column_names: list[str] = []
    for column in columns[:12]:
        column_rule = _suggest_business_name(
            column.physical_name,
            comment=column.physical_comment or "",
            object_type="column",
        )
        name = str(column_rule.get("business_name") or column.business_name or "").strip()
        if not name or _normalize_term(name) == _normalize_term(column.physical_name):
            continue
        if name in {"ID", "编码"} and len(column_names) >= 2:
            continue
        if name not in column_names:
            column_names.append(name)
        if len(column_names) >= 5:
            break

    if business_name and not str(rule.get("description") or "").strip():
        subject = _compact_business_label(business_name)
        detail = f"，包含{'、'.join(column_names)}等信息" if column_names else ""
        rule["description"] = f"记录{subject}相关业务数据{detail}。"

    synonyms = _as_list(rule.get("synonyms"))
    compact = physical_name.replace("_", "")
    for synonym in (physical_name, compact, _compact_business_label(business_name)):
        if synonym and synonym not in synonyms:
            synonyms.append(synonym)
    rule["synonyms"] = synonyms[:6]
    rule["evidence"] = {
        **dict(rule.get("evidence") or {}),
        "table_tokens": tokens,
        "column_business_names": column_names,
        "needs_manual_input": bool(rule.get("needs_manual_input")),
        "reason": rule.get("reason"),
    }
    return rule


def _quote_ident(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def _safe_alias(text: str) -> str:
    return str(text or "").replace("`", "").replace("\n", " ").strip()[:64] or "value"


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def _format_result_text(columns: list[str], rows: list[tuple], row_count: int) -> str:
    if not columns:
        return "语义查询执行成功，但没有返回字段。\n"
    result_text = " | ".join(str(col) for col in columns) + "\n"
    result_text += "-" * 50 + "\n"
    for row in rows[:50]:
        result_text += " | ".join(str(v) for v in row) + "\n"
    if row_count > 50:
        result_text += f"... 共 {row_count} 行\n"
    return result_text


class SemanticQueryService:
    """Coordinator for semantic scan, model CRUD and query execution."""

    DEFAULT_LIMIT = 100
    MAX_LIMIT = 1000

    async def get_active_datasource(self, workspace_id: str) -> Optional[SemanticDatasource]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            result = await session.execute(
                select(SemanticDatasourceModel)
                .where(
                    SemanticDatasourceModel.workspace_id == workspace_id,
                    SemanticDatasourceModel.is_active == True,  # noqa: E712
                )
                .order_by(SemanticDatasourceModel.updated_at.desc())
            )
            model = result.scalars().first()
            return SemanticDatasource.from_orm(model) if model else None

    async def get_catalog(self, workspace_id: str) -> Optional[SemanticCatalog]:
        datasource = await self.get_active_datasource(workspace_id)
        if not datasource:
            return None

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
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

        catalog = SemanticCatalog(
            datasource=datasource,
            tables=[SemanticTable.from_orm(item) for item in tables],
            columns=[SemanticColumn.from_orm(item) for item in columns],
            metrics=[SemanticMetric.from_orm(item) for item in metrics],
            relationships=[SemanticRelationship.from_orm(item) for item in relationships],
        )
        canonical_metrics = self._canonical_metrics(catalog)
        if canonical_metrics:
            existing_names = {_normalize_term(metric.name) for metric in catalog.metrics}
            catalog.metrics.extend(
                metric for metric in canonical_metrics if _normalize_term(metric.name) not in existing_names
            )
            catalog.__post_init__()
        return catalog

    async def list_models(self, workspace_id: str) -> dict[str, Any]:
        catalog = await self.get_catalog(workspace_id)
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            runs = list(
                (
                    await session.execute(
                        select(SemanticQueryRunModel)
                        .where(SemanticQueryRunModel.workspace_id == workspace_id)
                        .order_by(SemanticQueryRunModel.created_at.desc())
                        .limit(20)
                    )
                )
                .scalars()
                .all()
            )
            suggestions = list(
                (
                    await session.execute(
                        select(SemanticBusinessSuggestionModel)
                        .where(
                            SemanticBusinessSuggestionModel.workspace_id == workspace_id,
                            SemanticBusinessSuggestionModel.status == "pending",
                        )
                        .order_by(
                            SemanticBusinessSuggestionModel.confidence.desc(),
                            SemanticBusinessSuggestionModel.updated_at.desc(),
                        )
                        .limit(500)
                    )
                )
                .scalars()
                .all()
            )

        if not catalog:
            return {
                "datasource": None,
                "tables": [],
                "columns": [],
                "metrics": [],
                "relationships": [],
                "business_suggestions": [
                    SemanticBusinessSuggestion.from_orm(item).model_dump(mode="json")
                    for item in suggestions
                ],
                "recent_runs": [SemanticQueryRun.from_orm(run).model_dump(mode="json") for run in runs],
                "matching_diagnostics": [],
            }
        return {
            "datasource": catalog.datasource.model_dump(mode="json"),
            "tables": [item.model_dump(mode="json") for item in catalog.tables],
            "columns": [item.model_dump(mode="json") for item in catalog.columns],
            "metrics": [item.model_dump(mode="json") for item in catalog.metrics],
            "relationships": [item.model_dump(mode="json") for item in catalog.relationships],
            "business_suggestions": [
                SemanticBusinessSuggestion.from_orm(item).model_dump(mode="json")
                for item in suggestions
            ],
            "recent_runs": [SemanticQueryRun.from_orm(run).model_dump(mode="json") for run in runs],
            "matching_diagnostics": self.build_matching_diagnostics(catalog),
        }

    async def get_model_overview(self, workspace_id: str) -> dict[str, Any]:
        """Return the semantic landing-page counters without loading every asset.

        Large workspaces can contain tens of thousands of semantic objects.  The
        landing page only needs counts, so loading and serializing the complete
        catalog here would waste database, API and browser resources.
        """
        datasource = await self.get_active_datasource(workspace_id)
        empty_counts = {
            "tables": 0,
            "queryable_tables": 0,
            "columns": 0,
            "queryable_columns": 0,
            "metrics": 0,
            "queryable_metrics": 0,
            "relationships": 0,
            "queryable_relationships": 0,
            "stale_assets": 0,
            "orphaned_assets": 0,
            "recent_runs": 0,
            "recent_success_runs": 0,
        }
        if not datasource:
            return {"datasource": None, "counts": empty_counts}

        db_manager = get_async_db_manager()
        counts = dict(empty_counts)
        async with db_manager.get_session() as session:
            model_specs = (
                ("tables", "queryable_tables", SemanticTableModel),
                ("columns", "queryable_columns", SemanticColumnModel),
                ("metrics", "queryable_metrics", SemanticMetricModel),
                ("relationships", "queryable_relationships", SemanticRelationshipModel),
            )
            for total_key, queryable_key, model in model_specs:
                total, queryable, stale, orphaned = (
                    await session.execute(
                        select(
                            func.count(model.id),
                            func.coalesce(func.sum(case((
                                (model.status == "confirmed")
                                & (model.is_queryable.is_(True))
                                & (model.sync_state == "current"),
                                1,
                            ), else_=0)), 0),
                            func.coalesce(func.sum(case((model.sync_state == "stale", 1), else_=0)), 0),
                            func.coalesce(func.sum(case((model.sync_state == "orphaned", 1), else_=0)), 0),
                        ).where(
                            model.workspace_id == workspace_id,
                            model.datasource_id == datasource.id,
                        )
                    )
                ).one()
                counts[total_key] = int(total or 0)
                counts[queryable_key] = int(queryable or 0)
                counts["stale_assets"] += int(stale or 0)
                counts["orphaned_assets"] += int(orphaned or 0)

            recent_statuses = list((await session.execute(
                select(SemanticQueryRunModel.status)
                .where(SemanticQueryRunModel.workspace_id == workspace_id)
                .order_by(SemanticQueryRunModel.created_at.desc())
                .limit(20)
            )).scalars())
            counts["recent_runs"] = len(recent_statuses)
            counts["recent_success_runs"] = sum(status == "success" for status in recent_statuses)

        return {
            "datasource": datasource.model_dump(mode="json"),
            "counts": counts,
        }

    def build_matching_diagnostics(self, catalog: SemanticCatalog) -> list[dict[str, Any]]:
        """Report risky business terms without mutating the administrator's catalog."""

        term_refs: dict[str, list[dict[str, Any]]] = {}
        term_labels: dict[str, str] = {}

        def collect(object_type: str, obj: Any, table_id: int) -> None:
            values = [(getattr(obj, "business_name", ""), "business_name")]
            values.extend((item, "synonym") for item in (getattr(obj, "synonyms", []) or []))
            seen: set[str] = set()
            for value, source in values:
                normalized = _normalize_term(value)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                term_labels.setdefault(normalized, str(value))
                term_refs.setdefault(normalized, []).append({
                    "object_type": object_type,
                    "object_id": int(obj.id),
                    "table_id": int(table_id),
                    "business_name": str(getattr(obj, "business_name", "") or ""),
                    "source": source,
                })

        for table in catalog.tables:
            if _is_active_semantic_table(table):
                collect("table", table, table.id)
        for column in catalog.columns:
            if _is_active_semantic_column(column, catalog):
                collect("column", column, column.table_id)
        for metric in catalog.metrics:
            if _is_active_semantic_metric(metric, catalog):
                collect("metric", metric, metric.table_id)

        diagnostics: list[dict[str, Any]] = []
        for normalized, refs in sorted(term_refs.items()):
            label = term_labels[normalized]
            han_length = len(re.findall(r"[\u4e00-\u9fff]", normalized))
            table_ids = {int(item["table_id"]) for item in refs}
            if (han_length and han_length <= 2) or len(table_ids) >= 3:
                diagnostics.append({
                    "type": "generic_term",
                    "severity": "warning",
                    "term": label,
                    "objects": refs,
                })
            unique_objects = {(item["object_type"], item["object_id"]) for item in refs}
            if len(table_ids) > 1 and len(unique_objects) > 1:
                diagnostics.append({
                    "type": "duplicate_term",
                    "severity": "warning",
                    "term": label,
                    "objects": refs,
                })
        return diagnostics

    async def scan_workspace_database(self, workspace_id: str, admin_user_id: str) -> dict[str, Any]:
        config = await get_workspace_db_config_async(workspace_id)
        if not config:
            config = await get_user_db_config_async(admin_user_id)
        if not config or not config.is_active:
            raise SemanticQueryError(
                "datasource_missing",
                "工作区未配置可扫描的数据库连接",
                retryable=False,
            )

        connection_url = config.get_connection_url()
        engine = create_mysql_engine(
            connection_url,
            pool_pre_ping=True,
            pool_size=1,
        )
        try:
            inspector = inspect(engine)
            table_names = inspector.get_table_names()
            views = []
            try:
                views = inspector.get_view_names()
            except Exception:  # noqa: BLE001
                views = []

            table_payloads = []
            for table_name in [*table_names, *views]:
                columns = inspector.get_columns(table_name)
                indexes = inspector.get_indexes(table_name)
                indexed_columns = {
                    col
                    for index in indexes
                    for col in (index.get("column_names") or [])
                    if col
                }
                description = ""
                try:
                    comment = inspector.get_table_comment(table_name) or {}
                    description = str(comment.get("text") or "")
                except Exception:  # noqa: BLE001
                    description = ""
                table_payloads.append(
                    {
                        "name": table_name,
                        "description": description,
                        "columns": [
                            {
                                "name": col["name"],
                                "type": str(col.get("type") or ""),
                                "comment": str(col.get("comment") or ""),
                                "primary_key": bool(col.get("primary_key")),
                                "indexed": col["name"] in indexed_columns,
                                "ordinal_position": idx,
                            }
                            for idx, col in enumerate(columns)
                        ],
                    }
                )
        finally:
            engine.dispose()

        counts = await self._upsert_scan_payload(workspace_id, config, table_payloads)
        datasource = await self.get_active_datasource(workspace_id)
        if not datasource:
            raise SemanticQueryError(
                "datasource_missing",
                "语义数据源写入失败，请检查数据库连接配置",
                safe_to_fallback=False,
            )
        return {
            "datasource": datasource.model_dump(mode="json"),
            **counts,
        }

    async def _upsert_scan_payload(
        self,
        workspace_id: str,
        config: UserDBConfig,
        table_payloads: list[dict[str, Any]],
    ) -> dict[str, int]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            existing_ds = (
                await session.execute(
                    select(SemanticDatasourceModel).where(
                        SemanticDatasourceModel.workspace_id == workspace_id,
                        SemanticDatasourceModel.host == config.host,
                        SemanticDatasourceModel.port == config.port,
                        SemanticDatasourceModel.database == config.database,
                    )
                )
            ).scalar_one_or_none()
            if existing_ds:
                existing_ds.name = config.database
                existing_ds.is_active = True
                existing_ds.updated_at = datetime.now()
                datasource = existing_ds
            else:
                datasource = SemanticDatasourceModel(
                    workspace_id=workspace_id,
                    name=config.database,
                    host=config.host,
                    port=config.port,
                    database=config.database,
                    dialect="mysql",
                    is_active=True,
                    semantic_sql_enabled=False,
                    semantic_sql_fallback_enabled=True,
                )
                session.add(datasource)
                await session.flush()

            table_count = 0
            column_count = 0
            for table_payload in table_payloads:
                physical_name = table_payload["name"]
                table_auto_disabled = _should_auto_disable_table(physical_name)
                table = (
                    await session.execute(
                        select(SemanticTableModel).where(
                            SemanticTableModel.workspace_id == workspace_id,
                            SemanticTableModel.datasource_id == datasource.id,
                            SemanticTableModel.physical_name == physical_name,
                        )
                    )
                ).scalar_one_or_none()
                if not table:
                    table = SemanticTableModel(
                        workspace_id=workspace_id,
                        datasource_id=datasource.id,
                        physical_name=physical_name,
                        business_name=physical_name,
                        description=table_payload.get("description") or "",
                        synonyms=[],
                        status="disabled" if table_auto_disabled else "confirmed",
                        is_queryable=not table_auto_disabled,
                    )
                    session.add(table)
                    await session.flush()
                else:
                    if table.status == "suggested":
                        table.status = "disabled" if table_auto_disabled else "confirmed"
                        if table_auto_disabled:
                            table.is_queryable = False
                        if table_payload.get("description"):
                            table.description = table_payload.get("description") or table.description
                    table.updated_at = datetime.now()
                table_count += 1
                table_queryable_for_scan = table.status != "disabled" and table.is_queryable

                for column_payload in table_payload["columns"]:
                    column_name = column_payload["name"]
                    column_sensitive = _is_sensitive_column_name(column_name)
                    column = (
                        await session.execute(
                            select(SemanticColumnModel).where(
                                SemanticColumnModel.workspace_id == workspace_id,
                                SemanticColumnModel.datasource_id == datasource.id,
                                SemanticColumnModel.table_id == table.id,
                                SemanticColumnModel.physical_name == column_name,
                            )
                        )
                    ).scalar_one_or_none()
                    if not column:
                        session.add(
                            SemanticColumnModel(
                                workspace_id=workspace_id,
                                datasource_id=datasource.id,
                                table_id=table.id,
                                physical_table=physical_name,
                                physical_name=column_name,
                                data_type=column_payload["type"],
                                business_name=column_name,
                                description=column_payload.get("comment") or "",
                                synonyms=[],
                                status="confirmed",
                                is_queryable=table_queryable_for_scan,
                                is_sensitive=column_sensitive,
                                is_primary_key=column_payload.get("primary_key", False),
                                is_indexed=column_payload.get("indexed", False),
                                ordinal_position=column_payload.get("ordinal_position", 0),
                            )
                        )
                    else:
                        column.data_type = column_payload["type"]
                        column.is_primary_key = column_payload.get("primary_key", False)
                        column.is_indexed = column_payload.get("indexed", False)
                        column.ordinal_position = column_payload.get("ordinal_position", 0)
                        if column.status == "suggested" and column_payload.get("comment"):
                            column.description = column_payload.get("comment") or column.description
                        if column.status == "suggested":
                            column.status = "confirmed"
                            if not table_queryable_for_scan:
                                column.is_queryable = False
                        if column_sensitive:
                            column.is_sensitive = True
                        column.updated_at = datetime.now()
                    column_count += 1

            suggestion_counts = await self._ensure_scan_suggestions(session, workspace_id, datasource.id)

        return {
            "table_count": table_count,
            "column_count": column_count,
            **suggestion_counts,
        }

    async def _ensure_scan_suggestions(self, session, workspace_id: str, datasource_id: int) -> dict[str, int]:
        tables = list(
            (
                await session.execute(
                    select(SemanticTableModel).where(
                        SemanticTableModel.workspace_id == workspace_id,
                        SemanticTableModel.datasource_id == datasource_id,
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
                        SemanticColumnModel.datasource_id == datasource_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_metrics = list(
            (
                await session.execute(
                    select(SemanticMetricModel).where(
                        SemanticMetricModel.workspace_id == workspace_id,
                        SemanticMetricModel.datasource_id == datasource_id,
                    )
                )
            ).scalars()
        )
        existing_relationships = list(
            (
                await session.execute(
                    select(SemanticRelationshipModel).where(
                        SemanticRelationshipModel.workspace_id == workspace_id,
                        SemanticRelationshipModel.datasource_id == datasource_id,
                    )
                )
            ).scalars()
        )
        existing_metric_names = {item.name for item in existing_metrics}
        existing_relationship_keys = {
            (item.left_table_id, item.right_table_id, item.left_column_id, item.right_column_id)
            for item in existing_relationships
        }

        active_tables, columns_by_table = _active_scan_objects(tables, columns)

        metric_suggestions = _suggest_metrics_for_columns(columns_by_table)
        relationship_suggestions = _suggest_relationships_for_columns(active_tables, columns_by_table)

        new_metrics = [item for item in metric_suggestions if item.name not in existing_metric_names]
        new_relationships = [
            item
            for item in relationship_suggestions
            if (item.left_table_id, item.right_table_id, item.left_column_id, item.right_column_id)
            not in existing_relationship_keys
        ]

        for suggestion in new_metrics:
            session.add(
                SemanticMetricModel(
                    workspace_id=workspace_id,
                    datasource_id=datasource_id,
                    name=suggestion.name,
                    business_name=suggestion.business_name,
                    description=suggestion.description,
                    formula=suggestion.formula,
                    aggregation=suggestion.aggregation,
                    table_id=suggestion.table_id,
                    column_id=suggestion.column_id,
                    time_column_id=suggestion.time_column_id,
                    default_grain=suggestion.default_grain,
                    synonyms=suggestion.synonyms,
                    status="suggested",
                    is_queryable=False,
                    sync_state="current",
                    origin_source="rule_scan",
                    management_mode="system",
                    confidence=suggestion.confidence,
                    evidence_json={"source": "semantic_scan_rule"},
                    stale_reason_json={},
                )
            )

        for suggestion in new_relationships:
            session.add(
                SemanticRelationshipModel(
                    workspace_id=workspace_id,
                    datasource_id=datasource_id,
                    left_table_id=suggestion.left_table_id,
                    right_table_id=suggestion.right_table_id,
                    left_column_id=suggestion.left_column_id,
                    right_column_id=suggestion.right_column_id,
                    relationship_type=suggestion.relationship_type,
                    confidence=suggestion.confidence,
                    status="suggested",
                    is_queryable=False,
                    description=suggestion.description,
                    sync_state="current",
                    origin_source="rule_scan",
                    management_mode="system",
                    evidence_json={"source": "semantic_scan_rule"},
                    stale_reason_json={},
                )
            )

        metric_count = len(new_metrics)
        relationship_count = len(new_relationships)
        metric_confirmed_count = sum(1 for item in new_metrics if item.status == "confirmed")
        relationship_confirmed_count = sum(1 for item in new_relationships if item.status == "confirmed")
        return {
            "metric_suggestion_count": metric_count,
            "relationship_suggestion_count": relationship_count,
            "metric_count": metric_count,
            "metric_confirmed_count": metric_confirmed_count,
            "relationship_count": relationship_count,
            "relationship_confirmed_count": relationship_confirmed_count,
            "removed_metric_count": 0,
            "removed_relationship_count": 0,
        }

    async def generate_business_suggestions(
        self,
        workspace_id: str,
        *,
        scope: str = "datasource",
        table_id: Optional[int] = None,
        force: bool = False,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        catalog = await self.get_catalog(workspace_id)
        if not catalog:
            raise SemanticQueryError("datasource_missing", "请先扫描数据库生成语义模型")

        tables = [
            table
            for table in catalog.tables
            if (scope != "table" or table.id == table_id)
            and (scope == "table" or (table.status == "confirmed" and table.is_queryable))
        ]
        if scope == "table" and table_id and not tables:
            raise SemanticQueryError("not_found", "语义表不存在或不属于当前工作区")

        columns_by_table = catalog.columns_by_table
        profile_summaries = await self._latest_column_profile_summaries(
            workspace_id,
            catalog.datasource.id,
            [column.id for table in tables for column in columns_by_table.get(table.id, [])],
        )
        suggestions: list[dict[str, Any]] = []
        for table in tables:
            table_columns = columns_by_table.get(table.id, [])
            if force or not _is_human_semantic(table.business_name, table.physical_name):
                rule = _suggest_table_business_semantics(
                    table.physical_name,
                    comment=table.physical_comment or "",
                    columns=table_columns,
                )
                suggestions.append(
                    {
                        "object_type": "table",
                        "object_id": table.id,
                        "physical_name": table.physical_name,
                        "target_context": {
                            "table_id": table.id,
                            "table_physical_name": table.physical_name,
                            "table_business_name": table.business_name,
                            "column_count": len(table_columns),
                            "columns": [
                                {
                                    "column_id": column.id,
                                    "physical_name": column.physical_name,
                                    "business_name": column.business_name,
                                    "data_type": column.data_type,
                                }
                                for column in table_columns[:40]
                            ],
                        },
                        **rule,
                    }
                )
            for column in columns_by_table.get(table.id, []):
                if not force and _is_human_semantic(column.business_name, column.physical_name):
                    continue
                rule = _suggest_business_name(
                    column.physical_name,
                    comment=column.physical_comment or "",
                    object_type="column",
                )
                suggestions.append(
                    {
                        "object_type": "column",
                        "object_id": column.id,
                        "physical_name": column.physical_name,
                        "table_id": table.id,
                        "table_name": table.physical_name,
                        "data_type": column.data_type,
                        "profile_summary": profile_summaries.get(column.id, {}),
                        "target_context": {
                            "table_id": table.id,
                            "table_physical_name": table.physical_name,
                            "table_business_name": table.business_name,
                            "column_id": column.id,
                            "column_physical_name": column.physical_name,
                            "column_business_name": column.business_name,
                            "data_type": column.data_type,
                        },
                        **rule,
                    }
                )

        if use_llm and suggestions:
            llm_suggestions = await self._generate_llm_business_suggestions(tables, columns_by_table, profile_summaries)
            suggestions = self._merge_llm_business_suggestions(suggestions, llm_suggestions)

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            count = 0
            for item in suggestions:
                await self._upsert_business_suggestion(
                    session,
                    workspace_id,
                    catalog.datasource.id,
                    item,
                    force=force,
                )
                count += 1

        return {"suggestion_count": count}

    async def list_table_business_suggestions(
        self,
        workspace_id: str,
        table_id: int,
    ) -> list[dict[str, Any]]:
        """List pending table/column suggestions without a workspace-wide cap."""
        datasource = await self.get_active_datasource(workspace_id)
        if not datasource:
            raise SemanticQueryError("datasource_missing", "请先扫描数据库生成语义数据源")

        db_manager = get_async_db_manager()
        async with db_manager.get_session() as session:
            table = (await session.execute(select(SemanticTableModel.id).where(
                SemanticTableModel.id == table_id,
                SemanticTableModel.workspace_id == workspace_id,
                SemanticTableModel.datasource_id == datasource.id,
            ))).scalar_one_or_none()
            if table is None:
                raise SemanticQueryError("not_found", "语义表不存在或不属于当前工作区")

            rows = list((await session.execute(
                select(SemanticBusinessSuggestionModel)
                .outerjoin(
                    SemanticColumnModel,
                    and_(
                        SemanticBusinessSuggestionModel.object_type == "column",
                        SemanticColumnModel.id == SemanticBusinessSuggestionModel.object_id,
                        SemanticColumnModel.workspace_id == workspace_id,
                        SemanticColumnModel.datasource_id == datasource.id,
                    ),
                )
                .where(
                    SemanticBusinessSuggestionModel.workspace_id == workspace_id,
                    SemanticBusinessSuggestionModel.datasource_id == datasource.id,
                    SemanticBusinessSuggestionModel.status == "pending",
                    or_(
                        and_(
                            SemanticBusinessSuggestionModel.object_type == "table",
                            SemanticBusinessSuggestionModel.object_id == table_id,
                        ),
                        and_(
                            SemanticBusinessSuggestionModel.object_type == "column",
                            SemanticColumnModel.table_id == table_id,
                        ),
                    ),
                )
                .order_by(
                    SemanticBusinessSuggestionModel.confidence.desc(),
                    SemanticBusinessSuggestionModel.updated_at.desc(),
                )
            )).scalars().all())

        return [
            SemanticBusinessSuggestion.from_orm(row).model_dump(mode="json")
            for row in rows
        ]

    async def _latest_column_profile_summaries(
        self,
        workspace_id: str,
        datasource_id: int,
        column_ids: list[int],
    ) -> dict[int, dict[str, Any]]:
        if not column_ids:
            return {}
        db_manager = get_async_db_manager()
        latest: dict[int, Any] = {}
        async with db_manager.session_scope() as session:
            rows = list((await session.execute(
                select(SemanticColumnProfileModel)
                .where(
                    SemanticColumnProfileModel.workspace_id == workspace_id,
                    SemanticColumnProfileModel.datasource_id == datasource_id,
                    SemanticColumnProfileModel.column_id.in_(list(dict.fromkeys(column_ids))),
                    SemanticColumnProfileModel.profile_status == "complete",
                )
                .order_by(SemanticColumnProfileModel.profiled_at.desc())
            )).scalars())
            for row in rows:
                if row.column_id in latest:
                    continue
                latest[row.column_id] = row
        return {
            column_id: {
                "sample_method": row.sample_method,
                "estimated_rows": row.estimated_rows,
                "sampled_rows": row.sampled_rows,
                "non_null_count": row.non_null_count,
                "null_ratio": row.null_ratio,
                "distinct_count": row.distinct_count,
                "distinct_ratio": row.distinct_ratio,
                "range": row.range_json or {},
                "avg_length": row.avg_length,
                "pattern_ratios": row.pattern_ratios_json or {},
                "profiled_at": row.profiled_at.isoformat() if row.profiled_at else None,
            }
            for column_id, row in latest.items()
        }

    async def _generate_llm_business_suggestions(
        self,
        tables: list[SemanticTable],
        columns_by_table: dict[int, list[SemanticColumn]],
        profile_summaries: Optional[dict[int, dict[str, Any]]] = None,
    ) -> dict[tuple[str, int], dict[str, Any]]:
        if not getattr(settings.llm, "api_key", ""):
            return {}
        table_payload = []
        for table in tables[:20]:
            table_payload.append(
                {
                    "id": table.id,
                    "physical_name": table.physical_name,
                    "comment": table.physical_comment or table.description or "",
                    "columns": [
                        {
                            "id": column.id,
                            "physical_name": column.physical_name,
                            "data_type": column.data_type,
                            "comment": column.physical_comment or column.description or "",
                            "is_primary_key": column.is_primary_key,
                            "is_indexed": column.is_indexed,
                            "profile_summary": (profile_summaries or {}).get(column.id, {}),
                        }
                        for column in columns_by_table.get(table.id, [])[:80]
                    ],
                }
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是企业数据语义治理助手。请根据数据库表名、字段名、类型和注释，"
                    "以及仅包含聚合统计的字段画像，生成简洁准确的中文业务名、业务说明和同义词。"
                    "不要读取、要求或推测原始样例值。表说明必须描述业务用途，不能描述生成来源。"
                    "无法可靠判断时 confidence 低于 0.65，并留空 business_name 或 description。只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "输出结构: {\"tables\":[{\"id\":1,\"business_name\":\"...\","
                    "\"description\":\"...\",\"synonyms\":[\"...\"],\"confidence\":0.0,"
                    "\"columns\":[{\"id\":2,\"business_name\":\"...\",\"description\":\"...\","
                    "\"synonyms\":[\"...\"],\"confidence\":0.0}]}]}\n\n"
                    f"数据库结构:\n{table_payload}"
                ),
            },
        ]
        try:
            llm = get_async_llm()
            parsed = await llm.generate_json(
                messages,
                model=getattr(settings.llm, "fast_model", settings.llm.model),
                temperature=0.1,
                max_tokens=6000,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("semantic business suggestion LLM failed: %s", exc)
            return {}

        output: dict[tuple[str, int], dict[str, Any]] = {}
        for table in parsed.get("tables", []) if isinstance(parsed, dict) else []:
            try:
                table_id = int(table.get("id"))
            except Exception:  # noqa: BLE001
                continue
            output[("table", table_id)] = table
            for column in table.get("columns", []) or []:
                try:
                    column_id = int(column.get("id"))
                except Exception:  # noqa: BLE001
                    continue
                output[("column", column_id)] = column
        return output

    def _merge_llm_business_suggestions(
        self,
        suggestions: list[dict[str, Any]],
        llm_suggestions: dict[tuple[str, int], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not llm_suggestions:
            return suggestions
        merged = []
        for item in suggestions:
            llm_item = llm_suggestions.get((item["object_type"], int(item["object_id"])))
            if llm_item:
                confidence = max(0.0, min(1.0, float(llm_item.get("confidence") or 0.0)))
                if confidence >= 0.65 and llm_item.get("business_name"):
                    item = {
                        **item,
                        "business_name": str(llm_item.get("business_name"))[:128],
                        "description": str(llm_item.get("description") or item.get("description") or ""),
                        "synonyms": _as_list(llm_item.get("synonyms"))[:6],
                        "confidence": max(float(item.get("confidence") or 0.0), confidence),
                        "source": "rule_llm",
                        "needs_manual_input": False,
                        "reason": "LLM 根据结构、注释和聚合画像生成了可复核语义建议。",
                        "evidence": {
                            **dict(item.get("evidence") or {}),
                            "llm": llm_item,
                            "needs_manual_input": False,
                            "reason": "LLM 根据结构、注释和聚合画像生成了可复核语义建议。",
                        },
                    }
            merged.append(item)
        return merged

    async def _upsert_business_suggestion(
        self,
        session,
        workspace_id: str,
        datasource_id: int,
        item: dict[str, Any],
        *,
        force: bool = False,
    ) -> SemanticBusinessSuggestionModel:
        existing = (
            await session.execute(
                select(SemanticBusinessSuggestionModel).where(
                    SemanticBusinessSuggestionModel.workspace_id == workspace_id,
                    SemanticBusinessSuggestionModel.datasource_id == datasource_id,
                    SemanticBusinessSuggestionModel.object_type == item["object_type"],
                    SemanticBusinessSuggestionModel.object_id == int(item["object_id"]),
                    SemanticBusinessSuggestionModel.status == "pending",
                )
            )
        ).scalar_one_or_none()
        if existing and not force:
            return existing
        payload = {
            "physical_name": item["physical_name"],
            "suggested_business_name": item["business_name"],
            "suggested_description": item.get("description") or "",
            "suggested_synonyms": _as_list(item.get("synonyms"))[:6],
            "confidence": float(item.get("confidence") or 0.5),
            "source": item.get("source") or "rule",
            "evidence_json": {
                **dict(item.get("evidence") or {}),
                "needs_manual_input": bool(item.get("needs_manual_input")),
                "reason": item.get("reason"),
                "profile_summary": item.get("profile_summary") or {},
                "target_context": item.get("target_context") or {},
            },
            "updated_at": datetime.now(),
        }
        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
            return existing
        model = SemanticBusinessSuggestionModel(
            workspace_id=workspace_id,
            datasource_id=datasource_id,
            object_type=item["object_type"],
            object_id=int(item["object_id"]),
            status="pending",
            **payload,
        )
        session.add(model)
        await session.flush()
        return model

    async def update_business_suggestion(
        self,
        workspace_id: str,
        suggestion_id: int,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {
            "suggested_business_name",
            "suggested_description",
            "suggested_synonyms",
            "confidence",
            "status",
        }
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            suggestion = await self._get_business_suggestion(session, workspace_id, suggestion_id)
            for key, value in patch.items():
                if key in allowed:
                    setattr(suggestion, key, _as_list(value) if key == "suggested_synonyms" else value)
            suggestion.updated_at = datetime.now()
            await session.flush()
            await session.refresh(suggestion)
            return SemanticBusinessSuggestion.from_orm(suggestion).model_dump(mode="json")

    async def accept_business_suggestion(self, workspace_id: str, suggestion_id: int, actor_id: str = "legacy-admin") -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            suggestion = await self._get_business_suggestion(session, workspace_id, suggestion_id)
            await self._apply_business_suggestion(session, suggestion, actor_id)
            await session.refresh(suggestion)
            payload = SemanticBusinessSuggestion.from_orm(suggestion).model_dump(mode="json")
        return payload

    async def batch_accept_business_suggestions(
        self,
        workspace_id: str,
        suggestion_ids: list[int],
        actor_id: str = "legacy-admin",
    ) -> dict[str, int]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            count = 0
            for suggestion_id in suggestion_ids:
                suggestion = await self._get_business_suggestion(session, workspace_id, suggestion_id)
                await self._apply_business_suggestion(session, suggestion, actor_id)
                count += 1
        return {"accepted_count": count}

    async def accept_table_business_suggestions(
        self,
        workspace_id: str,
        table_id: int,
        actor_id: str = "legacy-admin",
    ) -> dict[str, int]:
        datasource = await self.get_active_datasource(workspace_id)
        if not datasource:
            raise SemanticQueryError("datasource_missing", "请先扫描数据库生成语义数据源")
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            table = (await session.execute(select(SemanticTableModel).where(
                SemanticTableModel.id == table_id,
                SemanticTableModel.workspace_id == workspace_id,
                SemanticTableModel.datasource_id == datasource.id,
            ))).scalar_one_or_none()
            if not table:
                raise SemanticQueryError("not_found", "语义表不存在或不属于当前工作区")
            pending = list((await session.execute(select(SemanticBusinessSuggestionModel).where(
                SemanticBusinessSuggestionModel.workspace_id == workspace_id,
                SemanticBusinessSuggestionModel.datasource_id == datasource.id,
                SemanticBusinessSuggestionModel.status == "pending",
            ))).scalars())
            accepted = 0
            for suggestion in pending:
                context = suggestion.evidence_json.get("target_context") if isinstance(suggestion.evidence_json, dict) else {}
                is_table = suggestion.object_type == "table" and suggestion.object_id == table_id
                is_column = suggestion.object_type == "column" and int((context or {}).get("table_id") or -1) == table_id
                if not is_table and not is_column:
                    continue
                if not (suggestion.suggested_business_name or "").strip() or suggestion.confidence < 0.65:
                    continue
                await self._apply_business_suggestion(session, suggestion, actor_id)
                accepted += 1
            session.add(SemanticGovernanceEventModel(
                workspace_id=workspace_id,
                datasource_id=datasource.id,
                actor_id=actor_id,
                action="business_suggestions_table_accepted",
                object_type="tables",
                object_id=table_id,
                payload_json={"accepted_count": accepted},
            ))
            return {"accepted_count": accepted}

    async def reject_business_suggestion(self, workspace_id: str, suggestion_id: int, actor_id: str = "legacy-admin") -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            suggestion = await self._get_business_suggestion(session, workspace_id, suggestion_id)
            suggestion.status = "rejected"
            suggestion.updated_at = datetime.now()
            await session.flush()
            await session.refresh(suggestion)
            payload = SemanticBusinessSuggestion.from_orm(suggestion).model_dump(mode="json")
        return payload

    async def _get_business_suggestion(
        self,
        session,
        workspace_id: str,
        suggestion_id: int,
    ) -> SemanticBusinessSuggestionModel:
        suggestion = (
            await session.execute(
                select(SemanticBusinessSuggestionModel).where(
                    SemanticBusinessSuggestionModel.id == suggestion_id,
                    SemanticBusinessSuggestionModel.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if not suggestion:
            raise SemanticQueryError("not_found", "语义建议不存在")
        return suggestion

    async def _apply_business_suggestion(
        self, session, suggestion: SemanticBusinessSuggestionModel, actor_id: str,
    ) -> None:
        if suggestion.status != "pending":
            raise SemanticQueryError("invalid_suggestion_status", "只能接受待确认的语义建议")
        if not (suggestion.suggested_business_name or "").strip():
            raise SemanticQueryError("business_name_required", "低置信建议需要先填写业务名再接受")
        model_cls = SemanticTableModel if suggestion.object_type == "table" else SemanticColumnModel
        target = (
            await session.execute(
                select(model_cls).where(
                    model_cls.id == suggestion.object_id,
                    model_cls.workspace_id == suggestion.workspace_id,
                    model_cls.datasource_id == suggestion.datasource_id,
                )
            )
        ).scalar_one_or_none()
        if not target:
            raise SemanticQueryError("not_found", "语义建议对应对象不存在")
        target.business_name = suggestion.suggested_business_name
        target.description = suggestion.suggested_description or target.description
        target.synonyms = suggestion.suggested_synonyms or target.synonyms
        target.management_mode = "human"
        target.business_semantics_status = "confirmed"
        target.business_semantics_revision = int(target.business_semantics_revision or 0) + 1
        target.business_semantics_reviewed_by = actor_id
        target.business_semantics_reviewed_at = datetime.now()
        target.origin_source = target.origin_source or "human"
        target.evidence_json = {
            **(target.evidence_json or {}),
            "accepted_business_suggestion_id": suggestion.id,
        }
        target.updated_at = datetime.now()
        await session.execute(
            update(SemanticBusinessSuggestionModel)
            .where(
                SemanticBusinessSuggestionModel.workspace_id == suggestion.workspace_id,
                SemanticBusinessSuggestionModel.datasource_id == suggestion.datasource_id,
                SemanticBusinessSuggestionModel.object_type == suggestion.object_type,
                SemanticBusinessSuggestionModel.object_id == suggestion.object_id,
                SemanticBusinessSuggestionModel.status == "accepted",
            )
            .values(status="expired", updated_at=datetime.now())
        )
        suggestion.status = "accepted"
        suggestion.accepted_at = datetime.now()
        suggestion.updated_at = datetime.now()
        from app.services.permission_evidence_service import get_permission_evidence_service
        await get_permission_evidence_service().enqueue_if_ready_in_session(
            session, suggestion.workspace_id, int(suggestion.datasource_id), actor_id,
        )

    async def generate_metric_suggestions(self, workspace_id: str) -> dict[str, Any]:
        catalog = await self.get_catalog(workspace_id)
        if not catalog:
            raise SemanticQueryError("datasource_missing", "请先扫描数据库生成语义模型")
        existing_names = {metric.name for metric in catalog.metrics}
        active_tables, active_columns = _active_scan_objects(catalog.tables, catalog.columns)
        table_by_id = {table.id: table for table in active_tables}
        column_by_id = {column.id: column for columns in active_columns.values() for column in columns}
        suggestions = [item for item in _suggest_metrics_for_columns(active_columns) if item.name not in existing_names]
        db_manager = get_async_db_manager()
        created = 0
        async with db_manager.session_scope() as session:
            for suggestion in suggestions:
                table = table_by_id.get(suggestion.table_id)
                column = column_by_id.get(suggestion.column_id) if suggestion.column_id else None
                time_column = column_by_id.get(suggestion.time_column_id) if suggestion.time_column_id else None
                session.add(SemanticMetricModel(
                    workspace_id=workspace_id,
                    datasource_id=catalog.datasource.id,
                    name=suggestion.name,
                    business_name=suggestion.business_name,
                    description=suggestion.description,
                    formula=suggestion.formula,
                    aggregation=suggestion.aggregation,
                    table_id=suggestion.table_id,
                    column_id=suggestion.column_id,
                    time_column_id=suggestion.time_column_id,
                    default_grain=suggestion.default_grain,
                    synonyms=suggestion.synonyms,
                    status="suggested",
                    is_queryable=False,
                    sync_state="current",
                    origin_source="suggestion_generate",
                    management_mode="system",
                    confidence=suggestion.confidence,
                    evidence_json={
                        "source": "metric_suggestion_rule",
                        "target_context": {
                            "table_id": table.id if table else suggestion.table_id,
                            "table_physical_name": table.physical_name if table else None,
                            "table_business_name": table.business_name if table else None,
                            "column_id": column.id if column else None,
                            "column_physical_name": column.physical_name if column else None,
                            "column_business_name": column.business_name if column else None,
                            "time_column_id": time_column.id if time_column else None,
                    "time_column_physical_name": time_column.physical_name if time_column else None,
                    "time_column_business_name": time_column.business_name if time_column else None,
                    "formula": suggestion.formula,
                    "basis": "根据字段类型、命名规则与表字段结构生成，需人工确认后启用。",
                },
            },
                    stale_reason_json={},
                ))
                created += 1
        return {"suggestion_count": created}

    async def generate_relationship_suggestions(self, workspace_id: str) -> dict[str, Any]:
        catalog = await self.get_catalog(workspace_id)
        if not catalog:
            raise SemanticQueryError("datasource_missing", "请先扫描数据库生成语义模型")
        existing_keys = {
            (item.left_table_id, item.right_table_id, item.left_column_id, item.right_column_id)
            for item in catalog.relationships
        }
        active_tables, active_columns = _active_scan_objects(catalog.tables, catalog.columns)
        table_by_id = {table.id: table for table in active_tables}
        column_by_id = {column.id: column for columns in active_columns.values() for column in columns}
        suggestions = [
            item for item in _suggest_relationships_for_columns(active_tables, active_columns)
            if (item.left_table_id, item.right_table_id, item.left_column_id, item.right_column_id) not in existing_keys
        ]
        db_manager = get_async_db_manager()
        created = 0
        async with db_manager.session_scope() as session:
            for suggestion in suggestions:
                left_table = table_by_id.get(suggestion.left_table_id)
                right_table = table_by_id.get(suggestion.right_table_id)
                left_column = column_by_id.get(suggestion.left_column_id)
                right_column = column_by_id.get(suggestion.right_column_id)
                session.add(SemanticRelationshipModel(
                    workspace_id=workspace_id,
                    datasource_id=catalog.datasource.id,
                    left_table_id=suggestion.left_table_id,
                    right_table_id=suggestion.right_table_id,
                    left_column_id=suggestion.left_column_id,
                    right_column_id=suggestion.right_column_id,
                    relationship_type=suggestion.relationship_type,
                    confidence=suggestion.confidence,
                    status="suggested",
                    is_queryable=False,
                    description=suggestion.description,
                    sync_state="current",
                    origin_source="suggestion_generate",
                    management_mode="system",
                    evidence_json={
                        "source": "relationship_suggestion_rule",
                        "target_context": {
                            "left_table_id": left_table.id if left_table else suggestion.left_table_id,
                            "left_table_physical_name": left_table.physical_name if left_table else None,
                            "left_table_business_name": left_table.business_name if left_table else None,
                            "left_column_id": left_column.id if left_column else suggestion.left_column_id,
                            "left_column_physical_name": left_column.physical_name if left_column else None,
                            "left_column_business_name": left_column.business_name if left_column else None,
                            "right_table_id": right_table.id if right_table else suggestion.right_table_id,
                            "right_table_physical_name": right_table.physical_name if right_table else None,
                            "right_table_business_name": right_table.business_name if right_table else None,
                            "right_column_id": right_column.id if right_column else suggestion.right_column_id,
                    "right_column_physical_name": right_column.physical_name if right_column else None,
                    "right_column_business_name": right_column.business_name if right_column else None,
                    "relationship_type": suggestion.relationship_type,
                    "basis": "根据主键、索引、字段命名和表结构匹配生成，需人工确认后启用。",
                },
            },
                    stale_reason_json={},
                ))
                created += 1
        return {"suggestion_count": created}

    async def update_model(
        self,
        workspace_id: str,
        model_type: str,
        model_id: int,
        patch: dict[str, Any],
        actor_id: Optional[str] = None,
    ) -> dict[str, Any]:
        model_map = {
            "datasources": SemanticDatasourceModel,
            "tables": SemanticTableModel,
            "columns": SemanticColumnModel,
            "metrics": SemanticMetricModel,
            "relationships": SemanticRelationshipModel,
        }
        model_cls = model_map.get(model_type)
        if not model_cls:
            raise SemanticQueryError("invalid_model_type", f"不支持的语义模型类型: {model_type}")

        allowed_fields = {
            "datasources": {"name", "is_active", "semantic_sql_enabled", "semantic_sql_fallback_enabled", "runtime_mode"},
            "tables": {
                "business_name",
                "description",
                "synonyms",
                "status",
                "is_queryable",
                "is_sensitive",
                "sync_state",
            },
            "columns": {
                "business_name",
                "description",
                "synonyms",
                "status",
                "is_queryable",
                "is_sensitive",
                "sync_state",
            },
            "metrics": {
                "name",
                "business_name",
                "description",
                "formula",
                "aggregation",
                "table_id",
                "column_id",
                "time_column_id",
                "default_grain",
                "synonyms",
                "status",
                "is_queryable",
                "is_sensitive",
                "sync_state",
            },
            "relationships": {
                "left_table_id",
                "right_table_id",
                "left_column_id",
                "right_column_id",
                "relationship_type",
                "confidence",
                "status",
                "is_queryable",
                "description",
                "sync_state",
            },
        }[model_type]

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            model = (
                await session.execute(
                    select(model_cls).where(
                        model_cls.id == model_id,
                        model_cls.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if not model:
                raise SemanticQueryError("not_found", "语义对象不存在")


            for key, value in patch.items():
                if key in allowed_fields:
                    setattr(model, key, value)
            if model_type == "datasources":
                if "runtime_mode" in patch:
                    model.semantic_sql_enabled = model.runtime_mode != "disabled"
                    model.semantic_sql_fallback_enabled = model.runtime_mode == "shadow"
                elif "semantic_sql_enabled" in patch or "semantic_sql_fallback_enabled" in patch:
                    model.runtime_mode = (
                        "disabled"
                        if not model.semantic_sql_enabled
                        else "shadow" if model.semantic_sql_fallback_enabled else "trusted"
                    )
            else:
                model.management_mode = "human"
                if actor_id:
                    model.confirmed_by = actor_id
                if patch.get("status") == "confirmed":
                    model.confirmed_at = datetime.now()
                if model_type in {"tables", "columns"} and {
                    "business_name", "description", "synonyms",
                } & set(patch):
                    model.business_semantics_status = "confirmed"
                    model.business_semantics_revision = int(
                        model.business_semantics_revision or 0
                    ) + 1
                    model.business_semantics_reviewed_by = actor_id
                    model.business_semantics_reviewed_at = datetime.now()
                    from app.services.permission_evidence_service import get_permission_evidence_service
                    await get_permission_evidence_service().enqueue_if_ready_in_session(
                        session, workspace_id, int(model.datasource_id), actor_id or "system",
                    )
            model.updated_at = datetime.now()
            session.add(
                SemanticGovernanceEventModel(
                    workspace_id=workspace_id,
                    datasource_id=getattr(model, "datasource_id", getattr(model, "id", None)),
                    actor_id=actor_id,
                    action="semantic_object_updated",
                    object_type=model_type,
                    object_id=model_id,
                    payload_json={"changed_fields": sorted(key for key in patch if key in allowed_fields)},
                )
            )

            if model_type == "tables" and ({"status", "is_queryable"} & set(patch.keys())):
                table_enabled = model.status == "confirmed" and bool(model.is_queryable)
                column_update = update(SemanticColumnModel).where(
                    SemanticColumnModel.workspace_id == workspace_id,
                    SemanticColumnModel.datasource_id == model.datasource_id,
                    SemanticColumnModel.table_id == model.id,
                )
                if table_enabled:
                    column_update = column_update.where(SemanticColumnModel.sync_state == "current")
                await session.execute(column_update.values(status="confirmed" if table_enabled else "disabled", is_queryable=table_enabled, updated_at=datetime.now()))
                if not table_enabled:
                    await session.execute(
                        update(SemanticMetricModel)
                        .where(
                            SemanticMetricModel.workspace_id == workspace_id,
                            SemanticMetricModel.datasource_id == model.datasource_id,
                            SemanticMetricModel.table_id == model.id,
                        )
                        .values(
                            status="disabled",
                            is_queryable=False,
                            updated_at=datetime.now(),
                        )
                    )
                    await session.execute(
                        update(SemanticRelationshipModel)
                        .where(
                            SemanticRelationshipModel.workspace_id == workspace_id,
                            SemanticRelationshipModel.datasource_id == model.datasource_id,
                            or_(
                                SemanticRelationshipModel.left_table_id == model.id,
                                SemanticRelationshipModel.right_table_id == model.id,
                            ),
                        )
                        .values(
                            status="disabled",
                            is_queryable=False,
                            updated_at=datetime.now(),
                        )
                    )

            await session.flush()
            await session.refresh(model)

            converter = {
                "datasources": SemanticDatasource,
                "tables": SemanticTable,
                "columns": SemanticColumn,
                "metrics": SemanticMetric,
                "relationships": SemanticRelationship,
            }[model_type]
            return converter.from_orm(model).model_dump(mode="json")

    async def create_metric(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        datasource = await self.get_active_datasource(workspace_id)
        if not datasource:
            raise SemanticQueryError("datasource_missing", "请先扫描数据库生成语义数据源")
        required = {"name", "formula", "table_id"}
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise SemanticQueryError("invalid_metric", f"缺少指标字段: {', '.join(missing)}")

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            table = (
                await session.execute(
                    select(SemanticTableModel).where(
                        SemanticTableModel.id == int(payload["table_id"]),
                        SemanticTableModel.workspace_id == workspace_id,
                        SemanticTableModel.datasource_id == datasource.id,
                    )
                )
            ).scalar_one_or_none()
            if not table:
                raise SemanticQueryError("invalid_metric", "指标主表不存在或不属于当前工作区")
            column_id = payload.get("column_id")
            if column_id is not None:
                column = (
                    await session.execute(
                        select(SemanticColumnModel).where(
                            SemanticColumnModel.id == int(column_id),
                            SemanticColumnModel.workspace_id == workspace_id,
                            SemanticColumnModel.datasource_id == datasource.id,
                            SemanticColumnModel.table_id == table.id,
                        )
                    )
                ).scalar_one_or_none()
                if not column:
                    raise SemanticQueryError("invalid_metric", "指标字段不存在或不属于指标主表")
            time_column_id = payload.get("time_column_id")
            if time_column_id is not None:
                time_column = (
                    await session.execute(
                        select(SemanticColumnModel).where(
                            SemanticColumnModel.id == int(time_column_id),
                            SemanticColumnModel.workspace_id == workspace_id,
                            SemanticColumnModel.datasource_id == datasource.id,
                            SemanticColumnModel.table_id == table.id,
                        )
                    )
                ).scalar_one_or_none()
                if not time_column:
                    raise SemanticQueryError("invalid_metric", "指标时间字段不存在或不属于指标主表")
            metric = SemanticMetricModel(
                workspace_id=workspace_id,
                datasource_id=datasource.id,
                name=payload["name"],
                business_name=payload.get("business_name") or payload["name"],
                description=payload.get("description") or "",
                formula=payload["formula"],
                aggregation=payload.get("aggregation"),
                table_id=int(payload["table_id"]),
                column_id=payload.get("column_id"),
                time_column_id=payload.get("time_column_id"),
                default_grain=payload.get("default_grain"),
                synonyms=_as_list(payload.get("synonyms")),
                status=payload.get("status") or "suggested",
                is_queryable=bool(payload.get("is_queryable", True)),
                is_sensitive=bool(payload.get("is_sensitive", False)),
                sync_state="current",
                origin_source="human",
                management_mode="human",
                confidence=1.0,
                evidence_json={"source": "manual_create"},
                stale_reason_json={},
            )
            session.add(metric)
            await session.flush()
            await session.refresh(metric)
            return SemanticMetric.from_orm(metric).model_dump(mode="json")

    async def create_relationship(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        datasource = await self.get_active_datasource(workspace_id)
        if not datasource:
            raise SemanticQueryError("datasource_missing", "请先扫描数据库生成语义数据源")
        required = {"left_table_id", "right_table_id", "left_column_id", "right_column_id"}
        missing = [key for key in required if payload.get(key) in (None, "")]
        if missing:
            raise SemanticQueryError("invalid_relationship", f"缺少关系字段: {', '.join(missing)}")

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            left_table_id = int(payload["left_table_id"])
            right_table_id = int(payload["right_table_id"])
            left_column_id = int(payload["left_column_id"])
            right_column_id = int(payload["right_column_id"])
            table_count = (
                await session.execute(
                    select(SemanticTableModel.id).where(
                        SemanticTableModel.id.in_([left_table_id, right_table_id]),
                        SemanticTableModel.workspace_id == workspace_id,
                        SemanticTableModel.datasource_id == datasource.id,
                    )
                )
            ).scalars().all()
            if len(set(table_count)) != 2:
                raise SemanticQueryError("invalid_relationship", "关系表不存在或不属于当前工作区")
            left_column = (
                await session.execute(
                    select(SemanticColumnModel).where(
                        SemanticColumnModel.id == left_column_id,
                        SemanticColumnModel.workspace_id == workspace_id,
                        SemanticColumnModel.datasource_id == datasource.id,
                        SemanticColumnModel.table_id == left_table_id,
                    )
                )
            ).scalar_one_or_none()
            right_column = (
                await session.execute(
                    select(SemanticColumnModel).where(
                        SemanticColumnModel.id == right_column_id,
                        SemanticColumnModel.workspace_id == workspace_id,
                        SemanticColumnModel.datasource_id == datasource.id,
                        SemanticColumnModel.table_id == right_table_id,
                    )
                )
            ).scalar_one_or_none()
            if not left_column or not right_column:
                raise SemanticQueryError("invalid_relationship", "关系字段不存在或不属于对应语义表")
            rel = SemanticRelationshipModel(
                workspace_id=workspace_id,
                datasource_id=datasource.id,
                left_table_id=left_table_id,
                right_table_id=right_table_id,
                left_column_id=left_column_id,
                right_column_id=right_column_id,
                relationship_type=payload.get("relationship_type") or "many_to_one",
                confidence=float(payload.get("confidence", 0.8)),
                status=payload.get("status") or "suggested",
                is_queryable=bool(payload.get("is_queryable", True)),
                description=payload.get("description") or "",
                sync_state="current",
                origin_source="human",
                management_mode="human",
                evidence_json={"source": "manual_create"},
                stale_reason_json={},
            )
            session.add(rel)
            await session.flush()
            await session.refresh(rel)
            return SemanticRelationship.from_orm(rel).model_dump(mode="json")

    @staticmethod
    def _merge_validated_intent_hint(
        current: IntentQuery,
        raw_hint: dict[str, Any],
    ) -> IntentQuery:
        """Merge an already validated example blueprint into current intent.

        The stored blueprint wins for the query shape. Explicit filters and time
        expressions from the current utterance are retained. Every referenced
        object is resolved and permission-checked later by ``resolve_plan``.
        """

        try:
            hint = IntentQuery.model_validate(raw_hint)
        except Exception as exc:  # noqa: BLE001 - persisted data may be stale/corrupt
            raise SemanticQueryError(
                "sql_example_intent_invalid",
                "SQL 示例的结构化查询口径已失效，请重新校验该示例",
                retryable=False,
                safe_to_fallback=False,
            ) from exc

        merged = hint.model_copy(deep=True)
        merged.tables = list(dict.fromkeys(hint.tables or current.tables))
        merged.metrics = list(dict.fromkeys(hint.metrics or current.metrics))
        merged.dimensions = list(dict.fromkeys(hint.dimensions or current.dimensions))
        merged.explicit_dimensions = (
            list(dict.fromkeys(hint.explicit_dimensions or hint.dimensions))
            if (hint.explicit_dimensions is not None or hint.dimensions)
            else current.explicit_dimensions
        )

        # Dynamic values have already been safely applied to ``hint`` by the
        # private-example service. Do not append model-inferred filters here:
        # doing so can silently change an exact equality into a fuzzy match or
        # introduce dimensions that were absent from the validated SQL.
        merged.filters = [dict(item) for item in hint.filters]
        merged.time_range = hint.time_range or current.time_range
        merged.order_by = list(hint.order_by or current.order_by)
        merged.limit = max(1, int(hint.limit or current.limit))
        merged.confidence = max(float(current.confidence or 0), float(hint.confidence or 0), 0.85)
        merged.selected_time_column_id = hint.selected_time_column_id or current.selected_time_column_id

        clarification_items = list(dict.fromkeys([*hint.clarification_items, *current.clarification_items]))
        if merged.metrics:
            clarification_items = [item for item in clarification_items if item != "缺少已确认指标"]
        if merged.dimensions:
            clarification_items = [item for item in clarification_items if item != "缺少排行维度"]
        if merged.time_range:
            clarification_items = [item for item in clarification_items if item != "缺少时间范围"]
        merged.clarification_items = clarification_items
        merged.matching_trace = [*hint.matching_trace, *current.matching_trace]
        return merged

    async def execute_semantic_query(
        self,
        *,
        question: str,
        context_hint: Optional[str] = None,
        user_id: str,
        workspace_id: str,
        session_id: str = "",
        force_enabled: bool = False,
        expected_limit: Optional[int] = None,
        semantic_clarification: Optional[dict[str, Any]] = None,
        _validated_intent_hint: Optional[dict[str, Any]] = None,
    ) -> SemanticExecutionResult:
        started = time.perf_counter()
        run_id = None
        try:
            catalog = await self.get_catalog(workspace_id)
            if not catalog or (not catalog.datasource.semantic_sql_enabled and not force_enabled):
                raise SemanticQueryError(
                    "semantic_disabled",
                    "语义问数链路未启用",
                    safe_to_fallback=True,
                )
            config = await get_user_db_config_async(user_id)
            if not config or not config.is_active:
                config = await get_workspace_db_config_async(workspace_id)
            if not config or not config.is_active:
                config = await get_workspace_admin_db_config_async(workspace_id)
            if not config or not config.is_active:
                raise SemanticQueryError("datasource_missing", "用户或工作区未配置数据库连接")
            execution_url = config.get_readonly_connection_url()
            if not execution_url:
                execution_url = config.get_connection_url()
                logger.warning(
                    "SemanticQueryService: workspace=%s user=%s 未配置只读账号，"
                    "临时使用原数据库连接执行已通过 SELECT-only 校验的语义 SQL",
                    workspace_id,
                    user_id,
                )

            user_access = await self._load_user_access(user_id, workspace_id)
            user_access["semantic_access"] = await get_semantic_access_policy_service().load_runtime_access(
                workspace_id,
                catalog.datasource.id,
                user_access,
            )
            try:
                intent = await self.extract_intent_hybrid(
                    question,
                    catalog,
                    context_hint=context_hint,
                    semantic_clarification=semantic_clarification,
                )
            except SemanticQueryError as exc:
                clarification = self._clarification_from_error(
                    exc,
                    catalog,
                    IntentQuery(),
                    user_access,
                )
                if clarification:
                    clarification = self._carry_forward_metric_selections(
                        clarification,
                        semantic_clarification,
                    )
                    raise SemanticQueryError(
                        "semantic_clarification_required",
                        clarification.message,
                        retryable=False,
                        safe_to_fallback=False,
                        details={"clarification": clarification.model_dump(mode="json")},
                    ) from exc
                raise
            intent = self._apply_semantic_clarification(intent, catalog, semantic_clarification)
            if _validated_intent_hint:
                # This internal-only input is produced from an account-owned SQL
                # example after save-time validation.  It supplies query shape,
                # never SQL text, and still goes through the ordinary permission
                # resolver, row-scope injection and safe SQL compiler below.
                intent = self._merge_validated_intent_hint(intent, _validated_intent_hint)
            try:
                plan = self.resolve_plan(intent, catalog, user_access)
                plan.sql = self.compile_sql(plan, catalog)
            except SemanticQueryError as exc:
                clarification = self._clarification_from_error(exc, catalog, intent, user_access)
                if clarification:
                    raise SemanticQueryError(
                        "semantic_clarification_required",
                        clarification.message,
                        retryable=False,
                        safe_to_fallback=False,
                        details={"clarification": clarification.model_dump(mode="json")},
                    ) from exc
                raise
            self._validate_compiled_sql(plan.sql)

            executor = ReadOnlyExecutor(user_id, execution_url)
            result = executor.execute_query(plan.sql, timeout_sec=5, max_rows=min(intent.limit, self.MAX_LIMIT))
            if result.error:
                has_row_policy = any(
                    get_semantic_access_policy_service().table_requires_row_filter(
                        catalog.table_by_id[table_id],
                        plan.user_access,
                    )
                    for table_id in plan.table_ids
                    if table_id in catalog.table_by_id
                )
                repaired_sql = None
                if not has_row_policy:
                    repaired_sql = await self._repair_sql_after_execution_error(
                        question=question,
                        sql=plan.sql,
                        error=result.error,
                        referenced_tables=plan.referenced_tables,
                    )
                if repaired_sql:
                    self._validate_compiled_sql(repaired_sql)
                    repaired_result = executor.execute_query(
                        repaired_sql,
                        timeout_sec=5,
                        max_rows=min(intent.limit, self.MAX_LIMIT),
                    )
                    if not repaired_result.error:
                        plan.permission_actions.append(
                            {
                                "action": "sql_repaired_after_error",
                                "error": result.error[:300],
                                "original_sql": plan.sql,
                            }
                        )
                        plan.sql = repaired_sql
                        result = repaired_result
                    else:
                        result = repaired_result
                if result.error:
                    raise SemanticQueryError(
                        "readonly_execution_failed",
                        result.error,
                        safe_to_fallback=False,
                    )

            rows = [tuple(row) for row in result.rows]
            columns = list(result.columns)
            data = [dict(zip(columns, row)) for row in rows]
            diagnostics = analyze_sql_result(
                question=question,
                columns=columns,
                rows=rows,
                expected_limit=expected_limit or intent.limit,
            )
            result_text = append_diagnostics_text(
                _format_result_text(columns, rows, result.row_count),
                diagnostics,
            )
            execution_ms = int((time.perf_counter() - started) * 1000)
            public_intent = intent.model_dump(mode="json")
            run_id = await self.record_query_run(
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                question=question,
                semantic_enabled=True,
                fallback_used=False,
                status="success",
                error_type=None,
                intent={**public_intent, "matching_trace": intent.matching_trace},
                plan={**plan.model_dump(mode="json"), "diagnostics": diagnostics},
                sql=plan.sql,
                referenced_tables=plan.referenced_tables,
                row_count=result.row_count,
                execution_time_ms=execution_ms,
            )
            return SemanticExecutionResult(
                success=True,
                sql=plan.sql,
                intent=public_intent,
                plan=plan.model_dump(mode="json"),
                data=data,
                columns=columns,
                row_count=result.row_count,
                result_text=result_text,
                referenced_tables=plan.referenced_tables,
                execution_time_ms=execution_ms,
                run_id=run_id,
                diagnostics=diagnostics,
            )
        except SemanticQueryError as exc:
            execution_ms = int((time.perf_counter() - started) * 1000)
            await self.record_query_run(
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                question=question,
                semantic_enabled=True,
                fallback_used=False,
                status="error",
                error_type=exc.error_type,
                intent=None,
                plan=exc.details,
                sql=None,
                referenced_tables=[],
                row_count=0,
                execution_time_ms=execution_ms,
            )
            raise

    async def _repair_sql_after_execution_error(
        self,
        *,
        question: str,
        sql: str,
        error: str,
        referenced_tables: list[str],
    ) -> Optional[str]:
        normalized_error = str(error or "").lower()
        normalized_sql = str(sql or "").lower()
        if "full outer join" not in normalized_error and "full outer join" not in normalized_sql and "only_full_group_by" not in normalized_error:
            return None
        prompt = f"""你是 DeluData 的 MySQL SQL 修复器。只返回一条可执行的 MySQL SELECT SQL，不要解释，不要 markdown。

用户问题:
{question}

允许使用的数据表:
{", ".join(referenced_tables) or "仅使用原 SQL 已引用表"}

原 SQL:
{sql}

执行错误:
{error}

修复要求:
1. 如果包含 FULL OUTER JOIN，改写为 MySQL 兼容的 LEFT JOIN UNION RIGHT JOIN。
2. 如果是 only_full_group_by，补齐 GROUP BY 或改写 ORDER BY/SELECT 聚合表达式。
3. 保留 LIMIT，只允许单条 SELECT 查询。"""
        try:
            llm = get_async_llm()
            repaired = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=1200,
            )
            repaired = self._extract_sql_from_text(repaired)
            if repaired and repaired.strip().lower() != str(sql or "").strip().lower():
                return repaired
        except Exception as exc:  # noqa: BLE001
            logger.warning("Semantic SQL repair failed: %s", exc)
        return None

    @staticmethod
    def _extract_sql_from_text(text: str) -> str:
        value = str(text or "").strip()
        match = re.search(r"```(?:sql)?\s*([\s\S]*?)```", value, re.I)
        if match:
            value = match.group(1).strip()
        value = re.sub(r"^\s*SQL\s*:\s*", "", value, flags=re.I).strip()
        return value.rstrip(";")

    async def extract_intent_hybrid(
        self,
        question: str,
        catalog: SemanticCatalog,
        *,
        context_hint: Optional[str] = None,
        semantic_clarification: Optional[dict[str, Any]] = None,
    ) -> IntentQuery:
        metric_selections = self._metric_selections_from_clarification(semantic_clarification)
        semantic_question = self._semantic_request_text(question)
        yearless_month = self._extract_yearless_month(semantic_question)
        selected_time_range = self._time_range_from_clarification(semantic_clarification)
        if yearless_month and not selected_time_range:
            base_intent = self.extract_intent(
                question,
                catalog,
                metric_selections=metric_selections,
            )
            current_year = datetime.now().year
            clarification = SemanticClarification(
                kind="time_year_missing",
                message=(
                    f"你说的“{yearless_month}月”没有指定年份。"
                    "请选择年份，或直接输入完整时间，例如“2025年3月”。"
                ),
                options=[
                    SemanticClarificationOption(
                        id=f"time_range:{year:04d}-{yearless_month:02d}",
                        label=f"{year}年{yearless_month}月",
                        selection_patch={
                            "base_intent": base_intent.model_dump(mode="json"),
                            "intent_patch": {
                                "time_range": f"{year:04d}-{yearless_month:02d}",
                            },
                        },
                    )
                    for year in range(current_year, current_year - 3, -1)
                ],
                original_intent=base_intent.model_dump(mode="json"),
            )
            raise SemanticQueryError(
                "time_year_missing",
                clarification.message,
                retryable=False,
                safe_to_fallback=False,
                details={"clarification": clarification.model_dump(mode="json")},
            )
        hint = str(context_hint or "").strip()
        if not hint or _normalize_term(hint) == _normalize_term(question):
            return await self._extract_intent_hybrid_single(
                question,
                catalog,
                metric_selections=metric_selections,
            )
        # The raw utterance is the authority boundary.  When contextual input
        # exists, keep its explicit objects deterministic instead of allowing
        # an LLM to infer objects that only occur in the Planner envelope.
        primary = self.extract_intent(question, catalog, metric_selections=metric_selections)
        context_intent = await self._extract_intent_hybrid_single(
            hint,
            catalog,
            metric_selections=metric_selections,
        )
        return self._merge_context_intent(question, primary, context_intent)

    async def _extract_intent_hybrid_single(
        self,
        question: str,
        catalog: SemanticCatalog,
        *,
        metric_selections: Optional[list[tuple[int, str]]] = None,
    ) -> IntentQuery:
        semantic_question = self._semantic_request_text(question)
        rule_intent = self.extract_intent(
            question,
            catalog,
            metric_selections=metric_selections,
        )
        if not self._should_use_llm_intent(semantic_question, rule_intent):
            return rule_intent
        try:
            llm_payload = await self._extract_intent_with_llm(semantic_question, catalog, rule_intent)
            merged = self._merge_llm_intent(rule_intent, llm_payload, catalog)
            return self._refine_business_intent(semantic_question, merged)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Semantic intent LLM fallback failed: %s", exc)
            return rule_intent

    @staticmethod
    def _metric_selections_from_clarification(
        semantic_clarification: Optional[dict[str, Any]],
    ) -> list[tuple[int, str]]:
        if not isinstance(semantic_clarification, dict):
            return []
        patch = semantic_clarification.get("selection_patch")
        if not isinstance(patch, dict):
            patch = semantic_clarification
        replacements = [
            item for item in (patch.get("replace_metrics") or [])
            if isinstance(item, dict)
        ]
        if isinstance(patch.get("replace_metric"), dict):
            replacements.append(patch["replace_metric"])
        selections_by_term: dict[str, tuple[int, str]] = {}
        for replacement in replacements:
            if not replacement.get("to_id"):
                continue
            try:
                metric_id = int(replacement["to_id"])
            except (TypeError, ValueError):
                continue
            normalized_term = _normalize_term(str(replacement.get("from_name") or ""))
            selection_key = normalized_term or f"metric:{metric_id}"
            selections_by_term[selection_key] = (metric_id, normalized_term)
        return list(selections_by_term.values())

    def _time_range_from_clarification(
        self,
        semantic_clarification: Optional[dict[str, Any]],
    ) -> Optional[str]:
        if not isinstance(semantic_clarification, dict):
            return None
        patch = semantic_clarification.get("selection_patch")
        if not isinstance(patch, dict):
            patch = semantic_clarification
        intent_patch = patch.get("intent_patch")
        if isinstance(intent_patch, dict):
            selected = self._normalize_time_range(intent_patch.get("time_range"))
            if selected:
                return selected
        return self._extract_time_range(str(semantic_clarification.get("free_text") or ""))

    def _carry_forward_metric_selections(
        self,
        clarification: SemanticClarification,
        prior_clarification: Optional[dict[str, Any]],
    ) -> SemanticClarification:
        if not isinstance(prior_clarification, dict):
            return clarification
        prior_patch = prior_clarification.get("selection_patch")
        if not isinstance(prior_patch, dict):
            prior_patch = prior_clarification
        prior_replacements = [
            dict(item)
            for item in (prior_patch.get("replace_metrics") or [])
            if isinstance(item, dict) and item.get("to_id")
        ]
        if isinstance(prior_patch.get("replace_metric"), dict):
            prior_replacements.append(dict(prior_patch["replace_metric"]))
        if not prior_replacements:
            return clarification

        data = clarification.model_dump(mode="json")
        for option in data.get("options") or []:
            option_patch = dict(option.get("selection_patch") or {})
            current_replacements = [
                dict(item)
                for item in (option_patch.get("replace_metrics") or [])
                if isinstance(item, dict) and item.get("to_id")
            ]
            if isinstance(option_patch.get("replace_metric"), dict):
                current_replacements.append(dict(option_patch["replace_metric"]))
            replacements_by_term: dict[str, dict[str, Any]] = {}
            for replacement in [*prior_replacements, *current_replacements]:
                normalized_term = _normalize_term(str(replacement.get("from_name") or ""))
                selection_key = normalized_term or f"metric:{replacement.get('to_id')}"
                replacements_by_term[selection_key] = replacement
            option_patch["replace_metrics"] = list(replacements_by_term.values())
            option_patch.pop("replace_metric", None)
            option["selection_patch"] = option_patch
        return SemanticClarification(**data)

    @staticmethod
    def _has_explicit_query_shape(question: str) -> bool:
        text = _normalize_term(question)
        return bool(
            re.search(r"(top\s*\d+|前\s*\d+|最[高低少差好]|排名|排行)", question, re.I)
            or any(term in text for term in (
                "趋势", "按月", "每月", "按日", "每天", "同比", "环比",
                "对比", "比较", "相比", "分别", "统计", "汇总", "合计", "平均",
            ))
        )

    def _merge_context_intent(
        self,
        question: str,
        primary: IntentQuery,
        context: IntentQuery,
    ) -> IntentQuery:
        """Fill omissions from Planner/session context without overriding explicit user intent."""

        primary_has_objects = bool(primary.metrics or primary.dimensions or primary.tables or primary.filters)
        query_type = primary.query_type if self._has_explicit_query_shape(question) else context.query_type
        metrics = primary.metrics or context.metrics
        # A metric named in the raw utterance defines a complete aggregate
        # target. Planner wording such as "销售总金额" must not contribute
        # generic amount columns as grouping dimensions.
        dimensions = primary.dimensions or ([] if primary.metrics else context.dimensions)
        tables = primary.tables or ([] if primary.metrics or primary.dimensions else context.tables)
        explicit_dimensions = list(primary.explicit_dimensions or [])
        if not primary_has_objects:
            explicit_dimensions = list(context.explicit_dimensions or [])

        primary_filter_fields = {
            _normalize_term(str(item.get("field") or ""))
            for item in primary.filters
            if isinstance(item, dict)
        }
        filters = list(primary.filters)
        filters.extend(
            item for item in context.filters
            if isinstance(item, dict)
            and _normalize_term(str(item.get("field") or "")) not in primary_filter_fields
        )
        time_range = primary.time_range or context.time_range
        order_by = primary.order_by or context.order_by
        clarification_items = list(dict.fromkeys(primary.clarification_items + context.clarification_items))
        if metrics:
            clarification_items = [item for item in clarification_items if item != "缺少已确认指标"]
        if dimensions:
            clarification_items = [item for item in clarification_items if item != "缺少排行维度"]
        if time_range:
            clarification_items = [item for item in clarification_items if item != "缺少时间范围"]
        return IntentQuery(
            query_type=query_type,
            tables=list(dict.fromkeys(tables)),
            metrics=list(dict.fromkeys(metrics)),
            dimensions=list(dict.fromkeys(dimensions)),
            explicit_dimensions=list(dict.fromkeys(explicit_dimensions)),
            filters=filters,
            time_range=time_range,
            order_by=order_by,
            limit=primary.limit if primary.limit != self.DEFAULT_LIMIT else context.limit,
            confidence=max(primary.confidence, context.confidence),
            clarification_items=clarification_items,
            selected_time_column_id=primary.selected_time_column_id or context.selected_time_column_id,
            matching_trace=list(primary.matching_trace) + list(context.matching_trace),
        )

    @staticmethod
    def _match_sort_key(item: _SemanticMatch) -> tuple[int, int, float, int]:
        return (item.source_rank, -len(_normalize_term(item.term)), -item.confidence, item.object_id)

    @staticmethod
    def _term_is_generic(term: str, matches: list[_SemanticMatch]) -> bool:
        normalized = _normalize_term(term)
        han_length = len(re.findall(r"[\u4e00-\u9fff]", normalized))
        shared_tables = {
            item.table_id
            for item in matches
            if _normalize_term(item.term) == normalized and item.table_id
        }
        return bool((han_length and han_length <= 2) or len(shared_tables) > 1)

    def _match_semantic_objects(
        self,
        text: str,
        catalog: SemanticCatalog,
        *,
        query_type: QueryType,
    ) -> list[_SemanticMatch]:
        normalized_text = _normalize_term(text)
        output: dict[tuple[str, int, int, int], _SemanticMatch] = {}

        def add_match(
            *,
            object_type: Literal["table", "column", "metric"],
            object_id: int,
            output_name: str,
            table_id: int,
            term: str,
            source: Literal["business_name", "technical_name", "synonym", "loose_table"],
            source_rank: int,
            confidence: Optional[float],
        ) -> None:
            normalized_term = _normalize_term(term)
            if not normalized_term:
                return
            start = normalized_text.find(normalized_term)
            while start >= 0:
                candidate = _SemanticMatch(
                    object_type=object_type,
                    object_id=int(object_id),
                    output_name=output_name,
                    table_id=int(table_id),
                    term=term,
                    start=start,
                    end=start + len(normalized_term),
                    source=source,
                    source_rank=source_rank,
                    confidence=float(confidence or 0.0),
                )
                key = (object_type, int(object_id), candidate.start, candidate.end)
                previous = output.get(key)
                if previous is None or self._match_sort_key(candidate) < self._match_sort_key(previous):
                    output[key] = candidate
                start = normalized_text.find(normalized_term, start + 1)

        for metric in catalog.metrics:
            if not _is_active_semantic_metric(metric, catalog):
                continue
            add_match(object_type="metric", object_id=metric.id, output_name=metric.name, table_id=metric.table_id, term=metric.business_name, source="business_name", source_rank=0, confidence=metric.confidence)
            add_match(object_type="metric", object_id=metric.id, output_name=metric.name, table_id=metric.table_id, term=metric.name, source="technical_name", source_rank=1, confidence=metric.confidence)
            for synonym in _terms_for(synonyms=metric.synonyms):
                add_match(object_type="metric", object_id=metric.id, output_name=metric.name, table_id=metric.table_id, term=synonym, source="synonym", source_rank=2, confidence=metric.confidence)

        for column in catalog.columns:
            if not _is_active_semantic_column(column, catalog):
                continue
            add_match(object_type="column", object_id=column.id, output_name=column.business_name, table_id=column.table_id, term=column.business_name, source="business_name", source_rank=0, confidence=column.confidence)
            add_match(object_type="column", object_id=column.id, output_name=column.business_name, table_id=column.table_id, term=column.physical_name, source="technical_name", source_rank=1, confidence=column.confidence)
            for synonym in _terms_for(synonyms=column.synonyms):
                add_match(object_type="column", object_id=column.id, output_name=column.business_name, table_id=column.table_id, term=synonym, source="synonym", source_rank=2, confidence=column.confidence)

        for table in catalog.tables:
            if not _is_active_semantic_table(table):
                continue
            add_match(object_type="table", object_id=table.id, output_name=table.business_name, table_id=table.id, term=table.business_name, source="business_name", source_rank=0, confidence=table.confidence)
            add_match(object_type="table", object_id=table.id, output_name=table.business_name, table_id=table.id, term=table.physical_name, source="technical_name", source_rank=1, confidence=table.confidence)
            for synonym in _terms_for(synonyms=table.synonyms):
                add_match(object_type="table", object_id=table.id, output_name=table.business_name, table_id=table.id, term=synonym, source="synonym", source_rank=2, confidence=table.confidence)
            for term in _terms_for(table.physical_name, table.business_name, synonyms=table.synonyms):
                compact = _compact_semantic_term(term)
                if compact and compact != _normalize_term(term):
                    add_match(object_type="table", object_id=table.id, output_name=table.business_name, table_id=table.id, term=compact, source="loose_table", source_rank=3, confidence=table.confidence)
        return list(output.values())

    def _best_metric_matches(
        self,
        matches: list[_SemanticMatch],
        metric_selections: Optional[list[tuple[int, str]]] = None,
    ) -> list[_SemanticMatch]:
        candidates = sorted(
            (item for item in matches if item.object_type == "metric"),
            key=lambda item: (item.start, item.end, *self._match_sort_key(item)),
        )
        groups: list[list[_SemanticMatch]] = []
        for candidate in candidates:
            overlapping = next(
                (group for group in groups if any(candidate.start < item.end and item.start < candidate.end for item in group)),
                None,
            )
            if overlapping is None:
                groups.append([candidate])
            else:
                overlapping.append(candidate)

        selected: list[_SemanticMatch] = []
        for group in groups:
            best_key = min(self._match_sort_key(item)[:3] for item in group)
            best = [item for item in group if self._match_sort_key(item)[:3] == best_key]
            distinct = {(item.object_id, item.table_id) for item in best}
            if len(distinct) > 1:
                matched_terms = {_normalize_term(item.term) for item in best}
                selected_matches: list[_SemanticMatch] = []
                for selected_metric_id, selected_term in metric_selections or []:
                    if selected_term and selected_term not in matched_terms:
                        continue
                    selected_matches = [
                        item for item in best
                        if item.object_id == selected_metric_id
                    ]
                    if selected_matches:
                        break
                if selected_matches:
                    selected.append(sorted(selected_matches, key=self._match_sort_key)[0])
                    continue
                raise SemanticQueryError(
                    "metric_ambiguous",
                    f"指标存在多个同级候选: {best[0].term}",
                    retryable=False,
                    safe_to_fallback=False,
                    details={
                        "candidate_metric_ids": sorted({item.object_id for item in best}),
                        "matched_term": best[0].term,
                    },
                )
            selected.append(sorted(best, key=self._match_sort_key)[0])
        return selected

    def _apply_metric_selection_to_names(
        self,
        metrics: list[str],
        matches: list[_SemanticMatch],
        metric_selections: Optional[list[tuple[int, str]]],
    ) -> list[str]:
        output = list(metrics)
        for selected_metric_id, selected_term in metric_selections or []:
            term_matches = [
                item
                for item in matches
                if item.object_type == "metric"
                and (not selected_term or _normalize_term(item.term) == selected_term)
            ]
            selected_matches = [
                item for item in term_matches
                if item.object_id == selected_metric_id
            ]
            if not selected_matches:
                continue
            selected_name = sorted(selected_matches, key=self._match_sort_key)[0].output_name
            candidate_names = {item.output_name for item in term_matches}
            candidate_indexes = [
                index for index, name in enumerate(output)
                if name in candidate_names
            ]
            insert_at = candidate_indexes[0] if candidate_indexes else len(output)
            output = [name for name in output if name not in candidate_names]
            output.insert(min(insert_at, len(output)), selected_name)
        return list(dict.fromkeys(output))

    def _best_column_matches(
        self,
        matches: list[_SemanticMatch],
        *,
        metric_spans: list[tuple[int, int, int]],
        anchored_table_ids: set[int],
        analytical: bool,
    ) -> list[_SemanticMatch]:
        candidates = [item for item in matches if item.object_type == "column"]
        candidates = [
            item for item in candidates
            if not any(
                item.start >= start and item.end <= end and item.source_rank >= metric_rank
                for start, end, metric_rank in metric_spans
            )
        ]
        selected: list[_SemanticMatch] = []
        grouped: dict[tuple[int, int, str], list[_SemanticMatch]] = {}
        for item in candidates:
            grouped.setdefault((item.start, item.end, _normalize_term(item.term)), []).append(item)
        for group in grouped.values():
            generic = self._term_is_generic(group[0].term, matches)
            scoped = [item for item in group if item.table_id in anchored_table_ids]
            if anchored_table_ids and (generic or analytical):
                group = scoped
            elif generic and not anchored_table_ids:
                # Keep the semantic field name for a follow-up such as “按客户呢”,
                # but do not return multiple cross-table candidates.
                group = sorted(group, key=self._match_sort_key)[:1]
            if not group:
                continue
            selected.append(sorted(group, key=self._match_sort_key)[0])
        selected.sort(key=lambda item: (item.start, *self._match_sort_key(item)))
        return selected

    def extract_intent(
        self,
        question: str,
        catalog: SemanticCatalog,
        *,
        metric_selections: Optional[list[tuple[int, str]]] = None,
    ) -> IntentQuery:
        raw_text = str(question or "")
        current_question = self._current_question_text(raw_text)
        text = self._semantic_request_text(raw_text)
        normalized = _normalize_term(text)
        query_type: QueryType = "detail"
        if re.search(r"(top\s*\d+|前\s*\d+|前[一二三四五六七八九十]+|最[高低少差好]|排名|排行)", text, re.I):
            query_type = "topn"
        elif any(term in normalized for term in ("趋势", "按月", "每月", "按日", "每天", "同比", "环比")):
            query_type = "trend"
        elif any(term in normalized for term in ("对比", "比较", "相比", "分别")):
            query_type = "compare"
        elif any(term in normalized for term in ("统计", "总", "平均", "数量", "多少", "汇总", "合计")):
            query_type = "aggregate"

        limit = self._extract_limit(text, default=10 if query_type == "topn" else self.DEFAULT_LIMIT)
        matches = self._match_semantic_objects(text, catalog, query_type=query_type)
        explicit_matches = self._match_semantic_objects(current_question, catalog, query_type=query_type)
        metric_matches = self._best_metric_matches(matches, metric_selections)
        metric_spans = [(item.start, item.end, item.source_rank) for item in metric_matches]
        metric_table_ids = {item.table_id for item in metric_matches}
        column_matches = self._best_column_matches(
            matches,
            metric_spans=metric_spans,
            anchored_table_ids=metric_table_ids,
            analytical=query_type in {"aggregate", "topn", "trend", "compare"},
        )
        explicit_column_matches = self._best_column_matches(
            explicit_matches,
            metric_spans=[
                (item.start, item.end, item.source_rank)
                for item in self._best_metric_matches(explicit_matches, metric_selections)
            ],
            anchored_table_ids=metric_table_ids,
            analytical=query_type in {"aggregate", "topn", "trend", "compare"},
        )
        metrics = list(dict.fromkeys(item.output_name for item in metric_matches))
        dimensions = list(dict.fromkeys(item.output_name for item in column_matches))
        explicit_dimensions = list(dict.fromkeys(item.output_name for item in explicit_column_matches))
        table_matches = [item for item in matches if item.object_type == "table"]
        if metric_table_ids and query_type in {"aggregate", "topn", "trend", "compare"}:
            table_matches = [item for item in table_matches if item.table_id in metric_table_ids]
        tables = list(dict.fromkeys(
            item.output_name
            for item in sorted(table_matches, key=self._match_sort_key)
            if item.source != "loose_table" or not (metrics or dimensions)
        ))

        time_range = self._extract_time_range(text)
        filters = self._extract_rule_filters(text, catalog, tables, dimensions)

        order_by: list[dict[str, str]] = []
        if query_type == "topn":
            direction = "asc" if any(term in normalized for term in ("最低", "最少", "最差", "倒数")) else "desc"
            target = metrics[0] if metrics else ""
            order_by.append({"field": target, "direction": direction})

        query_type, tables, metrics, dimensions, filters, time_range, order_by = self._apply_business_intent_rules(
            text=text,
            query_type=query_type,
            tables=tables,
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            time_range=time_range,
            order_by=order_by,
        )
        metrics = self._apply_metric_selection_to_names(
            metrics,
            matches,
            metric_selections,
        )

        confidence = 0.82 if metrics or dimensions or tables or filters else 0.5
        clarification_items = []
        if query_type in {"aggregate", "topn", "trend", "compare"} and not metrics:
            clarification_items.append("缺少已确认指标")
        inventory_turnover_without_explicit_dimension = (
            {"outbound_quantity", "current_inventory_quantity"} <= set(metrics)
            and order_by
            and order_by[0].get("field") == "inventory_turnover_rate"
        )
        if query_type == "topn" and not dimensions and not inventory_turnover_without_explicit_dimension:
            clarification_items.append("缺少排行维度")
        if query_type == "trend" and not time_range:
            clarification_items.append("缺少时间范围")

        return IntentQuery(
            query_type=query_type,
            tables=tables,
            metrics=metrics,
            dimensions=dimensions,
            explicit_dimensions=list(dict.fromkeys(explicit_dimensions)),
            filters=filters,
            time_range=time_range,
            order_by=order_by,
            limit=limit,
            confidence=confidence,
            clarification_items=clarification_items,
            matching_trace=[
                {
                    "object_type": item.object_type,
                    "object_id": item.object_id,
                    "table_id": item.table_id,
                    "term": item.term,
                    "span": [item.start, item.end],
                    "source": item.source,
                    "source_rank": item.source_rank,
                    "confidence": item.confidence,
                }
                for item in sorted(matches, key=lambda item: (item.start, item.end, *self._match_sort_key(item)))
            ],
        )

    def _refine_business_intent(self, question: str, intent: IntentQuery) -> IntentQuery:
        query_type, tables, metrics, dimensions, filters, time_range, order_by = self._apply_business_intent_rules(
            text=question,
            query_type=intent.query_type,
            tables=intent.tables,
            metrics=intent.metrics,
            dimensions=intent.dimensions,
            filters=intent.filters,
            time_range=intent.time_range,
            order_by=intent.order_by,
        )
        clarification_items = list(intent.clarification_items)
        if query_type in {"aggregate", "topn", "trend", "compare"} and metrics:
            clarification_items = [item for item in clarification_items if item != "缺少已确认指标"]
        inventory_turnover_without_explicit_dimension = (
            {"outbound_quantity", "current_inventory_quantity"} <= set(metrics)
            and order_by
            and order_by[0].get("field") == "inventory_turnover_rate"
        )
        if query_type == "topn" and (dimensions or inventory_turnover_without_explicit_dimension):
            clarification_items = [item for item in clarification_items if item != "缺少排行维度"]
        if query_type == "trend" and time_range:
            clarification_items = [item for item in clarification_items if item != "缺少时间范围"]
        return IntentQuery(
            query_type=query_type,
            tables=tables,
            metrics=metrics,
            dimensions=dimensions,
            explicit_dimensions=intent.explicit_dimensions,
            filters=filters,
            time_range=time_range,
            order_by=order_by,
            limit=intent.limit,
            confidence=intent.confidence,
            clarification_items=clarification_items,
            matching_trace=intent.matching_trace,
        )

    def _should_use_llm_intent(self, question: str, intent: IntentQuery) -> bool:
        if intent.metrics and (intent.dimensions or intent.tables or intent.filters or intent.time_range):
            return False
        if not (intent.metrics or intent.dimensions or intent.tables):
            return True
        if self._extract_time_range(question) and not intent.time_range:
            return True
        if re.search(r"[\u4e00-\u9fff]{2,4}\s*(?:\d{4}年|\d{4}[-/])", question):
            return not intent.filters
        return False

    async def _extract_intent_with_llm(
        self,
        question: str,
        catalog: SemanticCatalog,
        rule_intent: IntentQuery,
    ) -> dict[str, Any]:
        llm = get_async_llm()
        candidates = self._semantic_candidates_for_llm(question, catalog)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是企业数据问数语义解析器。只能从候选语义目录中选择表、字段、指标，"
                    "不要创造不存在的对象。输出结构化 intent；过滤条件 field 必须使用候选字段的 business_name。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question,
                        "rule_intent": rule_intent.model_dump(mode="json"),
                        "candidates": candidates,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        return await llm.generate_structured(
            messages,
            self._semantic_intent_tool_schema(),
            temperature=0,
            max_tokens=1200,
        )

    def _semantic_intent_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "extract_semantic_intent",
                "description": "Extract governed semantic query intent from a Chinese natural-language data question.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query_type": {"type": "string", "enum": ["detail", "aggregate", "topn", "trend", "compare"]},
                        "tables": {"type": "array", "items": {"type": "string"}},
                        "metrics": {"type": "array", "items": {"type": "string"}},
                        "dimensions": {"type": "array", "items": {"type": "string"}},
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string"},
                                    "operator": {"type": "string", "enum": ["eq", "contains", "between", "gte", "lte", "in", "not_eq", "lt_today", "gt_today"]},
                                    "value": {},
                                    "values": {"type": "array", "items": {}},
                                },
                                "required": ["field", "operator"],
                            },
                        },
                        "time_range": {"type": ["string", "null"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": self.MAX_LIMIT},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "clarification_items": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["query_type", "tables", "metrics", "dimensions", "filters", "limit", "confidence"],
                },
            },
        }

    def _semantic_candidates_for_llm(self, question: str, catalog: SemanticCatalog) -> dict[str, Any]:
        text = question or ""
        confirmed_tables = [table for table in catalog.tables if _is_active_semantic_table(table)]
        scored_tables = sorted(
            confirmed_tables,
            key=lambda table: self._semantic_object_score(text, table.physical_name, table.business_name, table.synonyms),
            reverse=True,
        )[:30]
        table_ids = {table.id for table in scored_tables}
        scored_columns = sorted(
            [column for column in catalog.columns if _is_active_semantic_column(column, catalog)],
            key=lambda column: (
                column.table_id in table_ids,
                self._semantic_object_score(text, column.physical_name, column.business_name, column.synonyms),
            ),
            reverse=True,
        )[:120]
        scored_metrics = sorted(
            [metric for metric in catalog.metrics if _is_active_semantic_metric(metric, catalog)],
            key=lambda metric: self._semantic_object_score(text, metric.name, metric.business_name, metric.synonyms),
            reverse=True,
        )[:60]
        return {
            "tables": [
                {
                    "business_name": table.business_name,
                    "physical_name": table.physical_name,
                    "description": table.description,
                    "synonyms": table.synonyms,
                }
                for table in scored_tables
            ],
            "columns": [
                {
                    "business_name": column.business_name,
                    "physical_name": column.physical_name,
                    "table": catalog.table_by_id.get(column.table_id).business_name
                    if column.table_id in catalog.table_by_id
                    else column.physical_table,
                    "data_type": column.data_type,
                    "description": column.description,
                    "synonyms": column.synonyms,
                }
                for column in scored_columns
            ],
            "metrics": [
                {
                    "name": metric.name,
                    "business_name": metric.business_name,
                    "description": metric.description,
                    "synonyms": metric.synonyms,
                }
                for metric in scored_metrics
            ],
        }

    def _semantic_object_score(self, text: str, name: str, business_name: str, synonyms: list[str]) -> int:
        terms = _terms_for(name, business_name, synonyms=synonyms)
        score = 0
        for term in terms:
            normalized = _normalize_term(term)
            compact = _compact_semantic_term(term)
            if normalized and normalized in _normalize_term(text):
                score += 10
            elif compact and compact in _normalize_term(text):
                score += 6
            elif _contains_loose_term(text, [term]):
                score += 3
        return score

    def _merge_llm_intent(
        self,
        rule_intent: IntentQuery,
        payload: dict[str, Any],
        catalog: SemanticCatalog,
    ) -> IntentQuery:
        if not isinstance(payload, dict):
            return rule_intent
        query_type = payload.get("query_type") if payload.get("query_type") in {"detail", "aggregate", "topn", "trend", "compare"} else rule_intent.query_type
        tables = self._valid_names(payload.get("tables"), catalog, "table") or rule_intent.tables
        metrics = self._valid_names(payload.get("metrics"), catalog, "metric") or rule_intent.metrics
        dimensions = self._valid_names(payload.get("dimensions"), catalog, "column") or rule_intent.dimensions
        filters = self._valid_filters(payload.get("filters"), catalog) or rule_intent.filters
        time_range = self._normalize_time_range(payload.get("time_range")) or rule_intent.time_range
        limit = payload.get("limit") if isinstance(payload.get("limit"), int) else rule_intent.limit
        confidence = payload.get("confidence") if isinstance(payload.get("confidence"), (int, float)) else rule_intent.confidence
        clarification_items = payload.get("clarification_items") if isinstance(payload.get("clarification_items"), list) else rule_intent.clarification_items
        return IntentQuery(
            query_type=query_type,
            tables=tables,
            metrics=metrics,
            dimensions=dimensions,
            explicit_dimensions=rule_intent.explicit_dimensions,
            filters=filters,
            time_range=time_range,
            order_by=rule_intent.order_by,
            limit=max(1, min(int(limit), self.MAX_LIMIT)),
            confidence=float(confidence),
            clarification_items=[str(item) for item in clarification_items if item],
            selected_time_column_id=rule_intent.selected_time_column_id,
            matching_trace=rule_intent.matching_trace,
        )

    def _valid_names(self, values: Any, catalog: SemanticCatalog, object_type: str) -> list[str]:
        output = []
        for value in _as_list(values):
            name = str(value or "").strip()
            if not name:
                continue
            try:
                if object_type == "table" and self._find_table(name, catalog):
                    output.append(name)
                elif object_type == "column" and self._find_column(name, catalog):
                    output.append(name)
                elif object_type == "metric" and self._find_metric(name, catalog):
                    output.append(name)
            except SemanticQueryError:
                continue
        return list(dict.fromkeys(output))

    def _valid_filters(self, values: Any, catalog: SemanticCatalog) -> list[dict[str, Any]]:
        output = []
        for raw in _as_list(values):
            if not isinstance(raw, dict):
                continue
            field = str(raw.get("field") or "").strip()
            operator = str(raw.get("operator") or "eq").strip().lower()
            if operator not in {"eq", "contains", "between", "gte", "lte", "in", "not_eq", "lt_today", "gt_today"}:
                continue
            try:
                column = self._find_column(field, catalog)
            except SemanticQueryError:
                continue
            if not column:
                continue
            item: dict[str, Any] = {"field": column.business_name, "operator": operator}
            if operator == "between":
                raw_values = raw.get("values") if isinstance(raw.get("values"), list) else []
                if len(raw_values) < 2:
                    continue
                item["values"] = [raw_values[0], raw_values[1]]
            elif operator == "in":
                raw_values = raw.get("values") if isinstance(raw.get("values"), list) else []
                values = [value for value in raw_values if value is not None and value != ""]
                if not values:
                    continue
                item["values"] = values
            elif operator in {"lt_today", "gt_today"}:
                pass
            else:
                value = raw.get("value")
                if value is None or value == "":
                    continue
                item["value"] = value
            output.append(item)
        return output

    def _apply_business_intent_rules(
        self,
        *,
        text: str,
        query_type: QueryType,
        tables: list[str],
        metrics: list[str],
        dimensions: list[str],
        filters: list[dict[str, Any]],
        time_range: Optional[str],
        order_by: list[dict[str, str]],
    ) -> tuple[QueryType, list[str], list[str], list[str], list[dict[str, Any]], Optional[str], list[dict[str, str]]]:
        # Supervisor supplies a context envelope containing instructions,
        # summaries and prior turns.  Deterministic business keywords must be
        # evaluated only against the current utterance; otherwise phrases such
        # as “一次数据查询” accidentally contain “次数” and turn a detail query
        # into an aggregate query.
        business_text = self._current_question_text(text)
        normalized = _normalize_term(business_text)
        tables = list(tables)
        metrics = list(metrics)
        dimensions = list(dimensions)
        filters = list(filters)
        order_by = list(order_by)

        def set_tables(values: list[str]) -> None:
            nonlocal tables
            tables = values

        def ensure_metric(name: str) -> None:
            if name not in metrics:
                metrics.append(name)

        def set_metrics(values: list[str]) -> None:
            nonlocal metrics
            metrics = list(dict.fromkeys(values))

        def set_dimensions(values: list[str]) -> None:
            nonlocal dimensions
            dimensions = list(dict.fromkeys(values))

        def add_filter(item: dict[str, Any]) -> None:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            existing = {
                json.dumps(current, ensure_ascii=False, sort_keys=True)
                for current in filters
            }
            if key not in existing:
                filters.append(item)

        def extract_value(field_hint: str = "") -> str:
            return self._extract_named_entity_value(text, field_hint=field_hint)

        if "跟进" in normalized and any(term in normalized for term in ("订单", "记录", "情况", "客户", "备注", "次数")):
            query_type = "detail"
            set_tables(["订单跟进记录表"])
            set_metrics([])
            set_dimensions(["关联主码", "跟进人", "跟进时间", "跟进方式", "跟进内容", "跟进记录详情", "业务类型标识", "下次访问时间"])
            order_by = []
            # At this point the business table is unambiguous.  Extract the
            # leading person deterministically instead of relying on the LLM or
            # the earlier catalog-wide filter pass (which may see many person
            # columns and deliberately decline to guess).
            person_value = self._extract_person_value(text)
            if person_value:
                filters = [
                    item
                    for item in filters
                    if str(item.get("field") or "") != "跟进人"
                ]
                add_filter({"field": "跟进人", "operator": "contains", "value": person_value})
            if "统计" in normalized or "次数" in normalized:
                query_type = "aggregate"
                set_metrics(["follow_record_count"])
                set_dimensions(["跟进方式"] if "跟进方式" in normalized else ["跟进人"])

        if "入库" in normalized and "出库" in normalized and "当前库存" in normalized:
            query_type = "aggregate"
            set_tables(["入库明细表", "出库明细表", "当前库存明细表"])
            set_metrics(["inbound_quantity", "outbound_quantity", "current_inventory_quantity"])
            dimensions = []
            order_by = []
            item_value = extract_value("item")
            if item_value:
                filters = []
                add_filter({"field": "__item_name__", "operator": "independent_item_contains", "value": item_value})

        if "来料" in normalized and any(term in normalized for term in ("接收", "验收", "实收", "净重")):
            query_type = "aggregate"
            set_tables(["来料接收明细表"])
            set_metrics(["incoming_received_quantity", "incoming_net_weight"])
            set_dimensions(["客户名称"] if "客户" in normalized else ["品名"])
            order_by = []

        if "出库" in normalized and "明细" in normalized and "库存周转率" not in normalized:
            query_type = "detail"
            set_tables(["出库明细表"])
            set_metrics([])
            set_dimensions(["品名", "物料名称", "出库数量", "出库日期", "仓库"])
            item_value = extract_value("item")
            if item_value:
                filters = []
                add_filter({"field": "品名", "operator": "contains", "value": item_value})

        if "库存周转率" in normalized:
            query_type = "topn" if query_type == "topn" or "最" in normalized else "aggregate"
            set_tables(["出库明细表", "当前库存明细表"])
            set_metrics(["outbound_quantity", "current_inventory_quantity"])
            set_dimensions([])
            order_by = [{"field": "inventory_turnover_rate", "direction": "asc" if any(term in normalized for term in ("最差", "最低", "最少")) else "desc"}] if query_type == "topn" else []

        if "当前库存" in normalized and "库存周转率" not in normalized and not ("入库" in normalized and "出库" in normalized):
            set_tables(["当前库存明细表"])
            ensure_metric("current_inventory_quantity")
            set_dimensions(["品名", "规格", "颜色", "色号"])
            item_value = extract_value("item")
            if item_value:
                filters = []
                add_filter({"field": "品名", "operator": "contains", "value": item_value})
            if query_type == "topn":
                order_by = [{"field": "current_inventory_quantity", "direction": "asc" if "最低" in normalized or "最少" in normalized else "desc"}]

        if "成品验收" in normalized:
            set_tables(["成品验收明细表"])
            set_metrics(["unqualified_rate_clean_finish_accept_detail"])
            if any(term in normalized for term in ("产品", "品名", "商品")):
                set_dimensions(["品名"])
            if query_type == "topn" or any(term in normalized for term in ("最高", "最低", "最差", "前")):
                query_type = "topn"
                order_by = [{"field": "unqualified_rate_clean_finish_accept_detail", "direction": "desc"}]

        if "完工检验" in normalized:
            set_tables(["完工检验明细表"])
            set_metrics(["unqualified_rate"])
            product_value = extract_value("product")
            if product_value:
                filters = []
                add_filter({"field": "品名", "operator": "contains", "value": product_value})
            if "品名" in normalized or "产品" in normalized or product_value:
                set_dimensions(["品名"])
            if query_type == "topn":
                order_by = [{"field": "unqualified_rate", "direction": "desc"}]

        if "设备点检" in normalized or "点检" in normalized:
            set_tables(["设备点检记录表"])
            if any(term in normalized for term in ("报修", "异常")):
                query_type = "aggregate"
                set_metrics(["inspection_exception_count"])
                set_dimensions(["车间", "设备名称"])
                filters = []
                add_filter(
                    {
                        "operator": "raw_or",
                        "clauses": [
                            {"field": "是否报修", "operator": "eq", "value": "1"},
                            {"field": "点检结果", "operator": "not_eq", "value": "1"},
                        ],
                    }
                )

        if "打样" in normalized and "办单" in normalized:
            set_tables(["打样办单主表"])
            set_dimensions(["客户名称", "办单单号", "要求交期", "完成状态"])
            customer_value = extract_value("customer")
            if customer_value:
                filters = []
                add_filter({"field": "客户名称", "operator": "contains", "value": customer_value})
            if "急单" in normalized:
                filters = []
                add_filter({"field": "急单标记", "operator": "eq", "value": "1"})

        if "生产任务" in normalized:
            set_tables(["生产任务表"])
            produce_task_detail_requested = (
                any(term in normalized for term in ("品名", "产品", "商品"))
                and any(term in normalized for term in ("生产数量", "任务数量"))
                and not any(term in normalized for term in ("统计", "汇总", "合计", "总计", "分组", "按"))
            )
            if produce_task_detail_requested:
                query_type = "detail"
                filters = []
                set_metrics([])
                set_dimensions(["品名", "生产数量"])
            elif "统计" in normalized or "数量" in normalized:
                query_type = "aggregate"
                set_metrics(["produce_task_count"])
                set_dimensions(["生产设备"] if "设备" in normalized else ["完工状态"])
            if any(term in normalized for term in ("计划完工日期已过", "计划完工日期已经过期", "逾期", "已过")):
                filters = []
                add_filter({"field": "计划完工日期", "operator": "lt_today"})
            if "未完成" in normalized:
                add_filter({"field": "完工状态", "operator": "in", "values": ["1", "2"]})
            if query_type == "detail" and not produce_task_detail_requested:
                set_dimensions(["生产任务单号", "计划完工日期", "完工状态"])

        if "派工" in normalized and "明细" in normalized:
            query_type = "detail"
            set_tables(["派工单"])
            set_metrics([])
            set_dimensions(["单据编号", "品名", "派工数量", "生产人员", "投产设备", "制单日期"])
            device_match = re.search(r"([0-9０-９]+#\s*台)", text)
            if device_match:
                add_filter({"field": "投产设备", "operator": "eq", "value": device_match.group(1).replace(" ", "")})
        elif "派工" in normalized and any(term in normalized for term in ("生产设备", "设备")):
            query_type = "aggregate"
            set_tables(["派工单"])
            set_metrics(["dispatch_quantity", "dispatch_order_count"])
            set_dimensions(["生产设备"])
            order_by = []
        elif "派工" in normalized:
            set_tables(["派工单"])
            if "统计" in normalized or "数量" in normalized:
                query_type = "aggregate"
                set_metrics(["dispatch_quantity", "dispatch_order_count"])
                set_dimensions(["生产设备"])
            else:
                query_type = "detail"
                set_dimensions(["单据编号", "品名", "派工数量", "生产人员", "投产设备", "制单日期"])
            device_match = re.search(r"([0-9０-９]+#\s*台)", text)
            if device_match:
                add_filter({"field": "投产设备", "operator": "eq", "value": device_match.group(1).replace(" ", "")})

        if "销售" in normalized and any(term in normalized for term in ("交货日期已过", "交货日期已经过期", "逾期")):
            set_tables(["销售订单主表"])
            filters = []
            add_filter({"field": "交货日期", "operator": "lt_today"})
            if "未完成" in normalized:
                add_filter({"field": "完成状态", "operator": "in", "values": ["1", "2"]})
            set_dimensions(["订单号", "客户名称", "交货日期", "完成状态"])

        if query_type == "compare" and "销售金额" in normalized and "客户" in normalized:
            set_tables(["销售订单主表"])
            set_metrics(["sales_amount"])
            set_dimensions(["客户名称"])
            order_by = []

        if "销售" in normalized and ("销售金额" in normalized or "销售额" in normalized) and any(term in normalized for term in ("订单数", "订单数量", "订单")) and query_type != "compare":
            query_type = "aggregate"
            set_tables(["销售订单主表"])
            set_metrics(["sales_amount", "sales_order_count"])
            set_dimensions([])
            customer_value = extract_value("customer")
            if customer_value:
                filters = []
                add_filter({"field": "客户名称", "operator": "contains", "value": customer_value})

        if query_type == "trend" and "销售金额" in normalized:
            set_tables(["销售订单主表"])
            set_metrics(["sales_amount"])
            dimensions = []

        if "销售" in normalized and "订单" in normalized and "明细" in normalized and not any(term in normalized for term in ("总金额", "订单数量", "平均单价")):
            set_tables(["销售订单主表"])
            customer_value = extract_value("customer")
            if customer_value:
                filters = []
                add_filter({"field": "客户名称", "operator": "contains", "value": customer_value})
            if not metrics:
                set_dimensions(["订单号", "客户名称", "制单日期", "交货日期", "完成状态"])

        if "销售" in normalized and "客户" in normalized and query_type == "topn":
            set_tables(["销售订单主表"])
            if not metrics:
                set_metrics(["sales_amount"])
            set_dimensions(["客户名称"])
            order_by = [{"field": metrics[0] if metrics else "sales_amount", "direction": "desc"}]

        if "销售数量" in normalized and query_type == "topn":
            set_tables(["销售订单主表", "clean_jf_sale_order_1"])
            set_metrics(["sales_detail_quantity", "sum_clean_jf_sale_order_1_tax_amount"])
            set_dimensions(["product_name", "customer_item_number", "product_model"])
            order_by = [{"field": "sales_detail_quantity", "direction": "desc"}]

        return (
            query_type,
            list(dict.fromkeys(tables)),
            list(dict.fromkeys(metrics)),
            list(dict.fromkeys(dimensions)),
            filters,
            time_range,
            order_by,
        )

    def _extract_rule_filters(
        self,
        text: str,
        catalog: SemanticCatalog,
        table_names: list[str],
        dimension_names: list[str],
    ) -> list[dict[str, Any]]:
        table_ids = {
            table.id
            for table in (self._find_table(name, catalog) for name in table_names)
            if table
        }
        columns = [
            column
            for column in catalog.columns
            if column.status == "confirmed"
            and column.is_queryable
            and (not table_ids or column.table_id in table_ids)
        ]
        filters: list[dict[str, Any]] = []
        for column in columns:
            if _is_numeric_type(column.data_type) or _is_time_column(column):
                continue
            if not _is_person_column(column) and not any(
                re.search(
                    rf"{re.escape(str(term))}\s*(?:为|是|=|等于|包含|含有)",
                    text,
                )
                for term in _terms_for(column.physical_name, column.business_name, synonyms=column.synonyms)
            ):
                continue
            value = self._extract_explicit_column_value(text, column)
            if value:
                filters.append({"field": column.business_name, "operator": "contains", "value": value})

        if not any(self._find_column_filter(item, catalog) and _is_person_column(self._find_column_filter(item, catalog)) for item in filters):
            person = self._extract_person_value(text)
            person_columns = [column for column in columns if _is_person_column(column)]
            if person and len(person_columns) == 1:
                filters.append({"field": person_columns[0].business_name, "operator": "contains", "value": person})

        return self._valid_filters(filters, catalog)

    def _extract_explicit_column_value(self, text: str, column: SemanticColumn) -> str:
        for term in _terms_for(column.physical_name, column.business_name, synonyms=column.synonyms):
            term_text = re.escape(str(term))
            match = re.search(
                rf"{term_text}\s*(?:为|是|=|等于|包含|含有)?\s*"
                rf"([\u4e00-\u9fffA-Za-z0-9_\-]{{1,20}}?)"
                rf"(?=(?:在|于)?\d{{4}}\s*年|(?:在|于)?\d{{4}}[-/]|的|，|,|。|\s|$)",
                text,
            )
            if match:
                value = match.group(1).strip()
                if value and _normalize_term(value) != _normalize_term(term):
                    return value
        return ""

    @staticmethod
    def _current_question_text(text: str) -> str:
        primary = str(text or "").strip()
        current_question = re.search(
            r"(?:^|\n)用户当前问题\s*[：:]\s*(?:\r?\n)?([^\r\n]+)",
            primary,
        )
        return current_question.group(1).strip() if current_question else primary

    @classmethod
    def _semantic_request_text(cls, text: str) -> str:
        primary = str(text or "").strip()
        complete_request = re.search(
            r"(?:^|\n)完整\s*SQL\s*查询需求\s*[：:]\s*(?:\r?\n)?([^\r\n]+)",
            primary,
            flags=re.IGNORECASE,
        )
        if complete_request:
            return complete_request.group(1).strip()
        return cls._current_question_text(primary)

    def _extract_person_value(self, text: str) -> str:
        primary = self._current_question_text(text)
        # Chat execution wraps the current utterance in a multi-section context
        # envelope.  Person extraction must use the current utterance rather
        # than matching a later copy in conversation/SQL context.
        primary = re.sub(r"^\[Router修复[^\]]*\]\s*", "", primary)
        primary = re.sub(
            r"^(?:sql execution failed[，,：:\s]*)?(?:修复后重试[：:\s]*)?",
            "",
            primary,
            flags=re.IGNORECASE,
        )
        # Router retries often wrap the original question in phrases such as
        # “查询数据库中…” or “从数据库里获取…”.  Remove that transport wording
        # before extracting the person so its final location character is not
        # mistaken for part of the name (for example, “中张蒙”).
        primary = re.sub(
            r"^(?:(?:查询|查看|检索)\s*)?(?:从\s*)?数据库(?:中|内|里)?[，,]?\s*(?:查询|获取|查找|检索)?\s*",
            "",
            primary,
            count=1,
        )
        named_person = re.search(
            r"(?:员工名|员工姓名|跟进人|人员|姓名)\s*(?:为|是|=|等于)?\s*"
            r"[\"'“”‘’]?([\u4e00-\u9fff]{2,4})[\"'“”‘’]?"
            r"(?=且|在|于|，|,|\s|$)",
            primary,
        )
        if named_person:
            return named_person.group(1).strip()
        match = re.search(
            r"(?:分析|查询|查看|统计)?\s*(?:数据库)?\s*(?:获取)?\s*"
            r"([\u4e00-\u9fff]{2,4})\s*(?:在|于)?\s*(?=\d{4}年|\d{4}[-/])",
            primary,
        )
        if match:
            value = match.group(1).strip()
            value = re.sub(r"^(分析|查询|查看|统计|获取|数据库)", "", value)
            value = re.sub(r"(在|于)$", "", value)
            return value[-4:]
        return ""

    def _extract_named_entity_value(self, text: str, *, field_hint: str = "") -> str:
        primary = str(text or "").split("请先把当前问题理解为", 1)[0]
        primary = re.sub(r"\s+", "", primary)
        hint = field_hint.lower()

        def clean(value: str) -> str:
            value = re.sub(r"^(?:分析|查询|查看|统计|获取|按客户统计|按商品统计|按产品统计)", "", value or "")
            value = re.sub(r"^\d{4}年\d{1,2}月", "", value)
            value = re.sub(r"^(?:物料|商品|产品|客户|品名|名称)", "", value)
            value = value.strip(" 的，,。:：；;")
            return value

        def valid(value: str) -> bool:
            if not value:
                return False
            if re.search(r"(?:近|最近)\d+个?(?:天|日|月|年)", value):
                return False
            if re.fullmatch(r"\d{4}年?|\d{4}[-/]\d{1,2}|\d{1,2}月", value):
                return False
            if value in {"物料", "商品", "产品", "客户", "订单", "销售", "入库", "出库", "库存", "明细", "情况"}:
                return False
            business_terms = ("各物料", "销售金额", "订单数", "入库数量", "出库数量", "当前库存", "库存周转率", "不合格率", "跟进次数")
            return not any(term in value for term in business_terms)

        patterns: list[str]
        if hint in {"item", "product"}:
            patterns = [
                r"\d{4}年\d{1,2}月([^，,。]{1,80}?)的(?:当前库存|库存|入库|出库|完工检验|成品验收)",
                r"(?:查询|统计|分析|查看)?([^，,。]{1,80}?)(?:的)?(?:当前库存|库存情况|入库明细|出库明细|完工检验|成品验收)",
                r"(?:查询|统计|分析|查看)?([^，,。]{1,80}?)(?=在\d{4}年\d{1,2}月的(?:完工检验|成品验收))",
            ]
        elif hint == "customer":
            patterns = [
                r"(?:查询|统计|分析|查看)?([^，,。]{2,80}?)(?=\d{4}年\d{1,2}月(?:销售|的销售|打样|的打样))",
                r"\d{4}年\d{1,2}月([^，,。]{2,80}?)(?:的)?(?:销售|打样|办单)",
                r"(?:查询|统计|分析|查看)?([^，,。]{2,80}?)(?:的)?(?:销售订单明细|销售金额|打样办单)",
            ]
        else:
            patterns = [
                r"\d{4}年\d{1,2}月([^，,。]{1,80}?)的",
                r"(?:查询|统计|分析|查看)?([^，,。]{1,80}?)(?=\d{4}年\d{1,2}月)",
            ]

        for pattern in patterns:
            match = re.search(pattern, primary)
            if not match:
                continue
            value = clean(match.group(1))
            if valid(value):
                return value
        return ""

    def _find_column_filter(self, filter_item: dict[str, Any], catalog: SemanticCatalog) -> Optional[SemanticColumn]:
        try:
            return self._find_column(str(filter_item.get("field") or ""), catalog)
        except SemanticQueryError:
            return None

    def _column_match_rank(self, column: SemanticColumn, name: str) -> Optional[int]:
        target = _normalize_term(name)
        if not target:
            return None
        if target == _normalize_term(column.business_name):
            return 0
        if target == _normalize_term(column.physical_name):
            return 1
        if target in {_normalize_term(term) for term in column.synonyms}:
            return 2
        return None

    def _find_column_in_scope(
        self,
        name: str,
        catalog: SemanticCatalog,
        table_ids: set[int] | list[int] | tuple[int, ...] | None = None,
    ) -> Optional[SemanticColumn]:
        scope = set(table_ids or [])
        matches: list[tuple[int, SemanticColumn]] = []
        for column in catalog.columns:
            if not _is_active_semantic_column(column, catalog):
                continue
            if scope and column.table_id not in scope:
                continue
            rank = self._column_match_rank(column, name)
            if rank is not None:
                matches.append((rank, column))
        if not matches:
            return None
        best_rank = min(rank for rank, _ in matches)
        best_matches = [column for rank, column in matches if rank == best_rank]
        if len(best_matches) > 1:
            raise SemanticQueryError(
                "field_ambiguous",
                f"字段存在多个候选: {name}",
                safe_to_fallback=True,
                details={
                    "candidate_column_ids": [item.id for item in best_matches],
                    "scope_table_ids": sorted(scope),
                    "requested_name": name,
                },
            )
        return best_matches[0]

    def _extract_time_range(self, text: str) -> Optional[str]:
        relative = re.search(r"(近|最近)\s*(\d+)\s*个?\s*(天|日|月|年)", text)
        if relative:
            return "".join(relative.groups())
        month_range_same_year = re.search(
            r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(?:到|至|和|与|及|、|-|~)\s*(\d{1,2})\s*月",
            text,
        )
        if month_range_same_year:
            year = int(month_range_same_year.group(1))
            start_month = int(month_range_same_year.group(2))
            end_month = int(month_range_same_year.group(3))
            if 1 <= start_month <= 12 and 1 <= end_month <= 12:
                return f"{year:04d}-{start_month:02d}..{year:04d}-{end_month:02d}"
        month_range = re.search(
            r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(?:到|至|-|~)\s*(\d{4})\s*年\s*(\d{1,2})\s*月",
            text,
        )
        if not month_range:
            month_range = re.search(r"(\d{4})[-/](\d{1,2})\s*(?:到|至|-|~)\s*(\d{4})[-/](\d{1,2})", text)
        if month_range:
            start_year = int(month_range.group(1))
            start_month = int(month_range.group(2))
            end_year = int(month_range.group(3))
            end_month = int(month_range.group(4))
            if 1 <= start_month <= 12 and 1 <= end_month <= 12:
                return f"{start_year:04d}-{start_month:02d}..{end_year:04d}-{end_month:02d}"
        month = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
        if not month:
            month = re.search(r"(?<![A-Za-z0-9_/])(\d{4})[-/](\d{1,2})(?![\dA-Za-z_/])", text)
        if month:
            year = int(month.group(1))
            month_value = int(month.group(2))
            if 1 <= month_value <= 12:
                return f"{year:04d}-{month_value:02d}"
        year_match = re.search(r"(\d{4})\s*年", text)
        if year_match:
            return year_match.group(1)
        if "今年" in text:
            return "今年"
        if "本月" in text:
            return "本月"
        if "上月" in text or "上个月" in text:
            return "上月"
        return None

    @staticmethod
    def _parse_month_token(value: str) -> int:
        token = str(value or "").strip()
        if token.isdigit():
            month_value = int(token)
            return month_value if 1 <= month_value <= 12 else 0
        return {
            "一": 1,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
            "十一": 11,
            "十二": 12,
        }.get(token, 0)

    def _extract_yearless_month(self, text: str) -> Optional[int]:
        value = str(text or "")
        if not value or self._extract_time_range(value):
            return None
        match = re.search(
            r"(?<!\d)(1[0-2]|0?[1-9]|十一|十二|十|[一二三四五六七八九])\s*月(?:份)?(?!\d)",
            value,
        )
        if not match:
            return None
        month_value = self._parse_month_token(match.group(1))
        return month_value or None

    def _normalize_time_range(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        return self._extract_time_range(str(value)) or str(value).strip() or None

    def _month_bounds(self, value: str) -> Optional[tuple[str, str]]:
        month = re.fullmatch(r"(\d{4})-(\d{2})", str(value or ""))
        if not month:
            return None
        year = int(month.group(1))
        month_value = int(month.group(2))
        if not 1 <= month_value <= 12:
            return None
        start = datetime(year, month_value, 1)
        next_year = year + 1 if month_value == 12 else year
        next_month = 1 if month_value == 12 else month_value + 1
        end = datetime(next_year, next_month, 1)
        return start.date().isoformat(), end.date().isoformat()

    def resolve_plan(
        self,
        intent: IntentQuery,
        catalog: SemanticCatalog,
        user_access: dict[str, Any],
    ) -> SemanticPlan:
        if intent.clarification_items:
            raise SemanticQueryError(
                "clarification_required",
                "；".join(intent.clarification_items),
                retryable=False,
                safe_to_fallback=True,
                details={"intent": intent.model_dump(mode="json")},
            )

        metrics = [self._find_metric(name, catalog) for name in intent.metrics]
        metrics = [metric for metric in metrics if metric]
        if intent.query_type == "trend" and metrics and not any(metric.time_column_id for metric in metrics):
            raise SemanticQueryError(
                "time_field_missing",
                "趋势查询需要指标绑定已确认的时间字段",
                retryable=False,
                safe_to_fallback=True,
                details={"intent": intent.model_dump(mode="json")},
            )
        selected_table_ids = set()
        selected_column_ids = set()
        permission_column_ids = set()
        selected_metric_ids = set()
        for metric in metrics:
            selected_metric_ids.add(metric.id)
            selected_table_ids.add(metric.table_id)
            if metric.column_id:
                permission_column_ids.add(metric.column_id)
            if metric.time_column_id:
                permission_column_ids.add(metric.time_column_id)
            for column in self._metric_formula_columns(metric, catalog):
                permission_column_ids.add(column.id)

        dimension_tables = [self._find_table(name, catalog) for name in intent.tables]
        dimension_tables = [table for table in dimension_tables if table]
        for table in dimension_tables:
            selected_table_ids.add(table.id)

        dimension_scope = set(selected_table_ids)
        dimension_columns = []
        implicit_permission_omissions: list[dict[str, Any]] = []
        explicit_dimension_names = (
            intent.dimensions
            if intent.explicit_dimensions is None
            else intent.explicit_dimensions
        )
        explicit_dimension_keys = {
            _normalize_term(name)
            for name in explicit_dimension_names
            if str(name or "").strip()
        }
        for name in intent.dimensions:
            column = self._find_column_in_scope(name, catalog, dimension_scope or None)
            requested_keys = {
                _normalize_term(value)
                for value in (name,)
                if value
            }
            is_explicit_dimension = bool(requested_keys & explicit_dimension_keys)
            if not column and dimension_scope:
                # A dimension outside the metric table is a multi-table request;
                # resolve it globally so the confirmed Join graph must authorize it.
                if intent.query_type in {"aggregate", "topn", "trend", "compare"} and not is_explicit_dimension:
                    continue
                column = self._find_column_in_scope(name, catalog, None)
            if column and intent.query_type == "detail" and intent.explicit_dimensions is not None:
                column_keys = {
                    _normalize_term(value)
                    for value in (column.business_name, column.physical_name, *column.synonyms)
                    if value
                }
                is_explicit = bool(column_keys & explicit_dimension_keys)
                if not is_explicit:
                    decision = get_semantic_access_policy_service().check_object_access(
                        "column",
                        column,
                        user_access,
                    )
                    if not decision.get("allowed"):
                        implicit_permission_omissions.append(
                            {
                                "object_type": "column",
                                "object_id": int(column.id),
                                "action": "omit_implicit_hidden",
                                "policy_ids": decision.get("policy_ids") or [],
                            }
                        )
                        continue
            dimension_columns.append(column)
        dimension_columns = [column for column in dimension_columns if column]
        for column in dimension_columns:
            selected_column_ids.add(column.id)
            permission_column_ids.add(column.id)
            selected_table_ids.add(column.table_id)

        filter_scope = set(selected_table_ids)
        filter_columns = [
            column
            for item in intent.filters
            for column in self._filter_columns_for_item(item, catalog, filter_scope or None)
        ]
        for column in filter_columns:
            selected_table_ids.add(column.table_id)
            permission_column_ids.add(column.id)

        if not selected_table_ids:
            confirmed_tables = [table for table in catalog.tables if _is_active_semantic_table(table)]
            if len(confirmed_tables) == 1 and intent.query_type == "detail":
                selected_table_ids.add(confirmed_tables[0].id)
            else:
                raise SemanticQueryError(
                    "semantic_object_not_found",
                    "无法从已确认语义模型中确定要查询的表、字段或指标",
                    retryable=False,
                    safe_to_fallback=True,
                    details={"intent": intent.model_dump(mode="json")},
                )

        independent_metric_plan = self._supports_independent_metric_plan(intent, metrics, dimension_columns)
        if intent.time_range and selected_table_ids and not independent_metric_plan:
            time_column = self._select_time_column(selected_table_ids, intent, catalog)
            if time_column:
                permission_column_ids.add(time_column.id)

        relationship_ids = (
            []
            if independent_metric_plan
            else self._resolve_relationship_ids(selected_table_ids, catalog)
        )
        objects = self._objects_for_permission(
            selected_table_ids,
            permission_column_ids,
            selected_metric_ids,
            catalog,
        )
        self._check_row_scope_conflicts(
            selected_table_ids=selected_table_ids,
            intent=intent,
            catalog=catalog,
            user_access=user_access,
        )
        permission_actions = self._check_semantic_permissions(objects, user_access, catalog, intent)
        permission_actions.extend(implicit_permission_omissions)

        referenced_tables = [
            catalog.table_by_id[table_id].physical_name
            for table_id in selected_table_ids
            if table_id in catalog.table_by_id
        ]
        return SemanticPlan(
            intent=intent,
            datasource_id=catalog.datasource.id,
            table_ids=sorted(selected_table_ids),
            metric_ids=sorted(selected_metric_ids),
            column_ids=sorted(selected_column_ids),
            permission_column_ids=sorted(permission_column_ids),
            relationship_ids=relationship_ids,
            permission_actions=permission_actions,
            user_access=user_access,
            referenced_tables=sorted(referenced_tables),
        )

    def _check_row_scope_conflicts(
        self,
        *,
        selected_table_ids: set[int],
        intent: IntentQuery,
        catalog: SemanticCatalog,
        user_access: dict[str, Any],
    ) -> None:
        """Reject a query when its explicit value is provably outside row scope.

        The SQL predicate remains the enforcement boundary.  This preflight only
        recognizes simple static ``=``/``IN`` row policies, so it cannot turn an
        ordinary empty result into a false permission denial.
        """
        runtime = user_access.get("semantic_access") or {}
        if runtime.get("is_admin"):
            return
        effects_by_asset = runtime.get("effects_by_asset") or {}

        for filter_item in intent.filters:
            operator = str(filter_item.get("operator") or "eq").lower()
            if operator not in {"eq", "contains", "in"}:
                continue
            columns = self._filter_columns_for_item(filter_item, catalog, selected_table_ids)
            if len(columns) != 1:
                continue
            column = columns[0]
            table = catalog.table_by_id.get(column.table_id)
            if not table:
                continue
            table_decision = get_semantic_access_policy_service().check_object_access(
                "table",
                table,
                user_access,
            )
            if not table_decision.get("allowed"):
                continue

            row_effects = [
                item
                for item in effects_by_asset.get(f"table:{column.table_id}", [])
                if item.get("effect_type") == "row_filter"
            ]
            visible_effects = [
                item
                for item in effects_by_asset.get(f"table:{column.table_id}", [])
                if item.get("effect_type") == "visible"
            ]
            if any(
                (item.get("condition_json") or {}).get("row_scope", {}).get("type") == "all"
                for item in visible_effects
            ):
                continue
            if not row_effects:
                continue

            allowed_values: set[str] = set()
            fully_understood = True
            for effect in row_effects:
                condition = effect.get("condition_json") or {}
                try:
                    condition_column_id = int(condition.get("column_id") or 0)
                except (TypeError, ValueError):
                    fully_understood = False
                    break
                row_operator = str(condition.get("operator") or "").strip().lower()
                if (
                    condition_column_id != int(column.id)
                    or row_operator not in {"=", "in"}
                    or condition.get("value_source")
                ):
                    fully_understood = False
                    break
                raw_value = condition.get("value")
                if row_operator == "in":
                    values = (
                        [item.strip() for item in re.split(r"[,，]", raw_value) if item.strip()]
                        if isinstance(raw_value, str)
                        else _as_list(raw_value)
                    )
                else:
                    values = [raw_value]
                normalized = {
                    str(value).strip().casefold()
                    for value in values
                    if value is not None and str(value).strip()
                }
                if not normalized:
                    fully_understood = False
                    break
                allowed_values.update(normalized)

            if not fully_understood or not allowed_values:
                continue

            requested_raw = filter_item.get("values") if operator == "in" else [filter_item.get("value")]
            requested_values = [
                str(value).strip()
                for value in _as_list(requested_raw)
                if value is not None and str(value).strip()
            ]
            if not requested_values:
                continue
            requested_normalized = {value.casefold() for value in requested_values}
            if operator == "contains":
                has_overlap = any(
                    requested in allowed
                    for requested in requested_normalized
                    for allowed in allowed_values
                )
            else:
                has_overlap = bool(requested_normalized & allowed_values)
            if has_overlap:
                continue

            requested_label = "、".join(requested_values)
            table_label = str(table.business_name or table.physical_name or "相关数据").strip()
            if table_label.endswith("表"):
                table_label = table_label[:-1]
            raise SemanticQueryError(
                "permission_denied",
                f"当前用户无权查询“{requested_label}”的{table_label}。",
                safe_to_fallback=False,
                details={
                    "reason": "row_scope_conflict",
                    "table_id": int(table.id),
                    "column_id": int(column.id),
                    "requested_values": requested_values,
                },
            )

    def compile_sql(self, plan: SemanticPlan, catalog: SemanticCatalog) -> str:
        try:
            import sqlglot
        except Exception as exc:  # noqa: BLE001
            raise SemanticQueryError("compiler_unavailable", f"SQLGlot 不可用: {exc}") from exc

        metric_items = [catalog.metric_by_id[mid] for mid in plan.metric_ids if mid in catalog.metric_by_id]
        table_ids = list(plan.table_ids)
        independent_sql = self._compile_independent_metric_sql(plan, catalog, metric_items)
        if independent_sql:
            try:
                parsed = sqlglot.parse_one(independent_sql, read="mysql")
                return parsed.sql(dialect="mysql")
            except Exception as exc:  # noqa: BLE001
                raise SemanticQueryError(
                    "sql_compile_failed",
                    f"语义跨表指标计划编译 SQL 失败: {exc}",
                    retryable=True,
                    safe_to_fallback=True,
                    details=plan.model_dump(mode="json"),
                ) from exc
        grain_safe_sql = self._compile_grain_safe_metric_sql(plan, catalog, metric_items)
        if grain_safe_sql:
            try:
                parsed = sqlglot.parse_one(grain_safe_sql, read="mysql")
                return parsed.sql(dialect="mysql")
            except Exception as exc:  # noqa: BLE001
                raise SemanticQueryError(
                    "sql_compile_failed",
                    f"跨粒度指标计划编译 SQL 失败: {exc}",
                    retryable=True,
                    safe_to_fallback=True,
                    details=plan.model_dump(mode="json"),
                ) from exc
        metric_table_id = next((metric.table_id for metric in metric_items if metric.table_id in table_ids), None)
        if metric_table_id is not None:
            table_ids = [metric_table_id, *[table_id for table_id in table_ids if table_id != metric_table_id]]
        tables = [catalog.table_by_id[table_id] for table_id in table_ids]
        if not tables:
            raise SemanticQueryError("semantic_object_not_found", "查询计划缺少表")

        base_table = tables[0]
        aliases = {base_table.id: "t0"}
        joins = []
        relationships_by_id = {
            item.id: item for item in self._relationships_for_catalog(catalog)
        }
        pending_relationships = [
            relationships_by_id[rel_id]
            for rel_id in plan.relationship_ids
            if rel_id in relationships_by_id
        ]
        next_alias_index = 1
        while pending_relationships:
            progress = False
            next_pending = []
            for rel in pending_relationships:
                left_in = rel.left_table_id in aliases
                right_in = rel.right_table_id in aliases
                if left_in and right_in:
                    continue
                if left_in and not right_in:
                    aliases[rel.right_table_id] = f"t{next_alias_index}"
                    left_alias = aliases[rel.left_table_id]
                    right_alias = aliases[rel.right_table_id]
                    right_table = catalog.table_by_id[rel.right_table_id]
                    left_col = catalog.column_by_id[rel.left_column_id]
                    right_col = catalog.column_by_id[rel.right_column_id]
                elif right_in and not left_in:
                    aliases[rel.left_table_id] = f"t{next_alias_index}"
                    left_alias = aliases[rel.right_table_id]
                    right_alias = aliases[rel.left_table_id]
                    right_table = catalog.table_by_id[rel.left_table_id]
                    left_col = catalog.column_by_id[rel.right_column_id]
                    right_col = catalog.column_by_id[rel.left_column_id]
                else:
                    next_pending.append(rel)
                    continue
                next_alias_index += 1
                progress = True
                joins.append(
                    f"JOIN {_quote_ident(right_table.physical_name)} {right_alias} "
                    f"ON {left_alias}.{_quote_ident(left_col.physical_name)} = "
                    f"{right_alias}.{_quote_ident(right_col.physical_name)}"
                )
            if not progress:
                break
            pending_relationships = next_pending

        missing_table_ids = sorted(set(table_ids) - set(aliases))
        if missing_table_ids:
            raise SemanticQueryError(
                "semantic_model_incomplete",
                "已确认 Join 路径无法从查询主表连接所有引用表",
                retryable=False,
                safe_to_fallback=False,
                details={
                    "base_table_id": base_table.id,
                    "missing_table_ids": missing_table_ids,
                    "relationship_ids": plan.relationship_ids,
                },
            )

        compare_sql = self._compile_compare_sql(plan, catalog, aliases, joins, base_table)
        if compare_sql:
            try:
                parsed = sqlglot.parse_one(compare_sql, read="mysql")
                return parsed.sql(dialect="mysql")
            except Exception as exc:  # noqa: BLE001
                raise SemanticQueryError(
                    "sql_compile_failed",
                    f"语义对比查询计划编译 SQL 失败: {exc}",
                    retryable=True,
                    safe_to_fallback=True,
                    details=plan.model_dump(mode="json"),
                ) from exc

        select_items = []
        group_items = []
        where_items = []
        column_items = [catalog.column_by_id[cid] for cid in plan.column_ids if cid in catalog.column_by_id]

        if plan.intent.query_type == "trend" and metric_items:
            metric = next((item for item in metric_items if item.time_column_id), None)
            time_column = catalog.column_by_id.get(metric.time_column_id) if metric and metric.time_column_id else None
            if not time_column:
                raise SemanticQueryError(
                    "time_field_missing",
                    "趋势查询需要指标绑定已确认的时间字段",
                    safe_to_fallback=True,
                    details=plan.model_dump(mode="json"),
                )
            time_alias = aliases.get(time_column.table_id, "t0")
            time_expr = self._compile_time_bucket_expr(time_alias, time_column, metric.default_grain)
            select_items.append(f"{time_expr} AS {_quote_ident('时间')}")
            group_items.append(time_expr)
            time_predicate = self._compile_time_range_predicate(time_alias, time_column, plan.intent.time_range)
            if time_predicate:
                where_items.append(time_predicate)
        elif plan.intent.time_range:
            time_column = self._select_time_column(set(plan.table_ids), plan.intent, catalog)
            if time_column:
                time_alias = aliases.get(time_column.table_id, "t0")
                time_predicate = self._compile_time_range_predicate(time_alias, time_column, plan.intent.time_range)
                if time_predicate:
                    where_items.append(time_predicate)

        for filter_item in plan.intent.filters:
            predicate = self._compile_filter_item(aliases, catalog, set(plan.table_ids), filter_item)
            if predicate:
                where_items.append(predicate)

        where_items.extend(self._compile_row_scope_predicates(plan, catalog, aliases))

        for column in column_items:
            alias = aliases.get(column.table_id, "t0")
            select_items.append(
                f"{alias}.{_quote_ident(column.physical_name)} AS {_quote_ident(_safe_alias(column.business_name))}"
            )
            if plan.intent.query_type in {"aggregate", "topn", "trend", "compare"} and metric_items:
                group_items.append(f"{alias}.{_quote_ident(column.physical_name)}")

        for metric in metric_items:
            alias = aliases.get(metric.table_id, "t0")
            formula = self._qualify_metric_formula(metric, catalog, alias)
            select_items.append(f"{formula} AS {_quote_ident(_safe_alias(metric.business_name))}")

        if not select_items:
            base_columns = [
                column
                for column in catalog.columns_by_table.get(base_table.id, [])
                if _is_active_semantic_column(column, catalog)
                and get_semantic_access_policy_service().check_object_access(
                    "column",
                    column,
                    plan.user_access,
                ).get("allowed")
            ]
            if not base_columns:
                raise SemanticQueryError("semantic_object_not_found", "该表没有可问数的已确认字段")
            for column in base_columns[:20]:
                select_items.append(
                    f"t0.{_quote_ident(column.physical_name)} AS {_quote_ident(_safe_alias(column.business_name))}"
                )

        sql_parts = [
            "SELECT " + ", ".join(select_items),
            f"FROM {_quote_ident(base_table.physical_name)} t0",
            *joins,
        ]
        if where_items:
            sql_parts.append("WHERE " + " AND ".join(where_items))
        if group_items:
            sql_parts.append("GROUP BY " + ", ".join(group_items))

        if plan.intent.order_by:
            first = plan.intent.order_by[0]
            direction = "ASC" if first.get("direction") == "asc" else "DESC"
            order_field = first.get("field") or ""
            order_metric = next(
                (
                    metric
                    for metric in metric_items
                    if order_field
                    in {
                        metric.name,
                        metric.business_name,
                        _safe_alias(metric.business_name),
                    }
                ),
                None,
            )
            order_alias = (
                order_metric.business_name
                if order_metric
                else metric_items[0].business_name if metric_items else order_field
            )
            if order_alias:
                sql_parts.append(f"ORDER BY {_quote_ident(_safe_alias(order_alias))} {direction}")

        sql_parts.append(f"LIMIT {max(1, min(plan.intent.limit, self.MAX_LIMIT))}")
        raw_sql = "\n".join(sql_parts)
        try:
            parsed = sqlglot.parse_one(raw_sql, read="mysql")
            return parsed.sql(dialect="mysql")
        except Exception as exc:  # noqa: BLE001
            raise SemanticQueryError(
                "sql_compile_failed",
                f"语义查询计划编译 SQL 失败: {exc}",
                retryable=True,
                safe_to_fallback=True,
                details=plan.model_dump(mode="json"),
            ) from exc

    def _qualify_metric_formula(self, metric: SemanticMetric, catalog: SemanticCatalog, alias: str) -> str:
        text = str(metric.formula or "").strip()
        if not text:
            raise SemanticQueryError("metric_formula_missing", "指标缺少公式")
        formula_columns = {
            _normalize_term(name): column
            for column in self._metric_formula_columns(metric, catalog)
            for name in (column.physical_name, column.business_name, *column.synonyms)
            if name
        }
        return re.sub(
            r"\{([A-Za-z_][\w$]*|[\u4e00-\u9fff][\w\u4e00-\u9fff]*)\}",
            lambda match: f"{alias}.{_quote_ident(formula_columns[_normalize_term(match.group(1))].physical_name)}",
            text,
        )

    def _compile_compare_sql(
        self,
        plan: SemanticPlan,
        catalog: SemanticCatalog,
        aliases: dict[int, str],
        joins: list[str],
        base_table: SemanticTable,
    ) -> str:
        if plan.intent.query_type != "compare" or ".." not in str(plan.intent.time_range or ""):
            return ""
        metric_items = [catalog.metric_by_id[mid] for mid in plan.metric_ids if mid in catalog.metric_by_id]
        metric = next((item for item in metric_items if item.time_column_id), None) or (metric_items[0] if metric_items else None)
        if not metric:
            return ""
        amount_columns = self._metric_formula_columns(metric, catalog)
        if len(amount_columns) != 1 or not metric.time_column_id:
            return ""
        amount_column = amount_columns[0]
        time_column = catalog.column_by_id.get(metric.time_column_id)
        if not time_column:
            return ""
        dimension_columns = [catalog.column_by_id[cid] for cid in plan.column_ids if cid in catalog.column_by_id]
        if not dimension_columns:
            customer = self._find_column_in_scope("客户名称", catalog, set(plan.table_ids))
            if customer:
                dimension_columns = [customer]
        if not dimension_columns:
            return ""

        start_value, end_value = str(plan.intent.time_range).split("..", 1)
        left_bounds = self._month_bounds(start_value.strip())
        right_bounds = self._month_bounds(end_value.strip())
        if not left_bounds or not right_bounds:
            return ""

        metric_alias = aliases.get(metric.table_id, "t0")
        amount_expr = f"{metric_alias}.{_quote_ident(amount_column.physical_name)}"
        time_expr = f"{aliases.get(time_column.table_id, metric_alias)}.{_quote_ident(time_column.physical_name)}"
        left_condition = (
            f"{time_expr} >= {_sql_literal(left_bounds[0])} "
            f"AND {time_expr} < {_sql_literal(left_bounds[1])}"
        )
        right_condition = (
            f"{time_expr} >= {_sql_literal(right_bounds[0])} "
            f"AND {time_expr} < {_sql_literal(right_bounds[1])}"
        )
        left_sum = f"SUM(CASE WHEN {left_condition} THEN {amount_expr} ELSE 0 END)"
        right_sum = f"SUM(CASE WHEN {right_condition} THEN {amount_expr} ELSE 0 END)"

        select_items = []
        group_items = []
        for column in dimension_columns:
            alias = aliases.get(column.table_id, "t0")
            expr = f"{alias}.{_quote_ident(column.physical_name)}"
            select_items.append(f"{expr} AS {_quote_ident(_safe_alias(column.business_name))}")
            group_items.append(expr)
        select_items.extend(
            [
                f"{left_sum} AS {_quote_ident(start_value.strip() + metric.business_name)}",
                f"{right_sum} AS {_quote_ident(end_value.strip() + metric.business_name)}",
                f"({right_sum} - {left_sum}) AS {_quote_ident('变化金额')}",
                f"(({right_sum} - {left_sum}) / NULLIF({left_sum}, 0)) AS {_quote_ident('变化率')}",
            ]
        )
        where_items = [
            f"{time_expr} >= {_sql_literal(left_bounds[0])} AND {time_expr} < {_sql_literal(right_bounds[1])}",
            *self._compile_row_scope_predicates(plan, catalog, aliases),
        ]
        sql_parts = [
            "SELECT " + ", ".join(select_items),
            f"FROM {_quote_ident(base_table.physical_name)} t0",
            *joins,
            "WHERE " + " AND ".join(where_items),
            "GROUP BY " + ", ".join(group_items),
            f"LIMIT {max(1, min(plan.intent.limit, self.MAX_LIMIT))}",
        ]
        return "\n".join(sql_parts)

    def _compile_grain_safe_metric_sql(
        self,
        plan: SemanticPlan,
        catalog: SemanticCatalog,
        metric_items: list[SemanticMetric],
    ) -> str:
        """Aggregate each fact grain before combining metrics from multiple tables.

        A direct many-to-one join is safe for a metric on the many side, but it
        duplicates metrics owned by the one side.  This compiler creates one
        aggregate subquery per metric table, lets a fact table traverse only
        towards confirmed parent tables for dimensions/time/filtering, and
        combines the already-aggregated results afterwards.
        """

        if plan.intent.query_type not in {"aggregate", "topn"}:
            return ""
        metric_table_ids = list(dict.fromkeys(metric.table_id for metric in metric_items))
        if len(metric_table_ids) <= 1:
            return ""

        dimensions = [
            catalog.column_by_id[column_id]
            for column_id in plan.column_ids
            if column_id in catalog.column_by_id
        ]
        filter_columns = {
            column.id: column
            for item in plan.intent.filters
            for column in self._filter_columns_for_item(item, catalog, set(plan.table_ids))
        }
        time_column = (
            self._select_time_column(set(plan.table_ids), plan.intent, catalog)
            if plan.intent.time_range
            else None
        )
        relationships = {
            relationship.id: relationship
            for relationship in self._relationships_for_catalog(catalog)
            if relationship.id in set(plan.relationship_ids)
        }

        # Directed edges describe the only traversal that preserves the metric
        # table grain.  For many_to_one the many/left side may safely reference
        # parent/right attributes; the reverse direction would fan out rows.
        safe_edges: dict[int, list[tuple[int, SemanticRelationship]]] = {}
        for relationship in relationships.values():
            relation_type = str(relationship.relationship_type or "").lower()
            safe_edges.setdefault(relationship.left_table_id, []).append(
                (relationship.right_table_id, relationship)
            )
            if relation_type in {"one_to_one", "1:1", "one-to-one"}:
                safe_edges.setdefault(relationship.right_table_id, []).append(
                    (relationship.left_table_id, relationship)
                )

        metric_groups: list[tuple[int, list[SemanticMetric]]] = []
        for table_id in metric_table_ids:
            metric_groups.append((table_id, [metric for metric in metric_items if metric.table_id == table_id]))

        subqueries: list[dict[str, Any]] = []
        for index, (metric_table_id, metrics) in enumerate(metric_groups):
            required_table_ids = {metric_table_id}
            required_table_ids.update(column.table_id for column in dimensions)
            required_table_ids.update(column.table_id for column in filter_columns.values())
            if time_column:
                required_table_ids.add(time_column.table_id)

            aliases = {metric_table_id: "m0"}
            joins: list[str] = []
            queue = [metric_table_id]
            while queue:
                current = queue.pop(0)
                for target, relationship in safe_edges.get(current, []):
                    if target in aliases:
                        continue
                    aliases[target] = f"m{len(aliases)}"
                    queue.append(target)
                    current_alias = aliases[current]
                    target_alias = aliases[target]
                    if relationship.left_table_id == current:
                        current_column = catalog.column_by_id[relationship.left_column_id]
                        target_column = catalog.column_by_id[relationship.right_column_id]
                    else:
                        current_column = catalog.column_by_id[relationship.right_column_id]
                        target_column = catalog.column_by_id[relationship.left_column_id]
                    target_table = catalog.table_by_id[target]
                    joins.append(
                        f"JOIN {_quote_ident(target_table.physical_name)} {target_alias} "
                        f"ON {current_alias}.{_quote_ident(current_column.physical_name)} = "
                        f"{target_alias}.{_quote_ident(target_column.physical_name)}"
                    )

            missing_required = sorted(required_table_ids - set(aliases))
            if missing_required:
                raise SemanticQueryError(
                    "metric_grain_conflict",
                    "跨表指标无法在不重复聚合的前提下应用当前维度或筛选条件",
                    retryable=False,
                    safe_to_fallback=False,
                    details={
                        "metric_table_id": metric_table_id,
                        "missing_table_ids": missing_required,
                        "relationship_ids": plan.relationship_ids,
                    },
                )

            select_items: list[str] = []
            group_items: list[str] = []
            key_aliases: list[str] = []
            for dimension_index, column in enumerate(dimensions):
                alias = aliases[column.table_id]
                expression = f"{alias}.{_quote_ident(column.physical_name)}"
                key_alias = f"k{dimension_index}"
                select_items.append(f"{expression} AS {_quote_ident(key_alias)}")
                group_items.append(expression)
                key_aliases.append(key_alias)

            metric_aliases: list[str] = []
            for metric in metrics:
                metric_alias = _safe_alias(metric.business_name)
                select_items.append(
                    f"{self._qualify_metric_formula(metric, catalog, aliases[metric.table_id])} "
                    f"AS {_quote_ident(metric_alias)}"
                )
                metric_aliases.append(metric_alias)

            where_items: list[str] = []
            if time_column:
                predicate = self._compile_time_range_predicate(
                    aliases[time_column.table_id],
                    time_column,
                    plan.intent.time_range,
                )
                if predicate:
                    where_items.append(predicate)
            for filter_item in plan.intent.filters:
                predicate = self._compile_filter_item(
                    aliases,
                    catalog,
                    set(required_table_ids),
                    filter_item,
                )
                if predicate:
                    where_items.append(predicate)
            for table_id, alias in aliases.items():
                row_scope = self._compile_row_scope_predicate_for_table(
                    catalog.table_by_id[table_id],
                    alias,
                    catalog,
                    plan.user_access,
                )
                if row_scope:
                    where_items.append(row_scope)

            metric_table = catalog.table_by_id[metric_table_id]
            sql_parts = [
                "SELECT " + ", ".join(select_items),
                f"FROM {_quote_ident(metric_table.physical_name)} m0",
                *joins,
            ]
            if where_items:
                sql_parts.append("WHERE " + " AND ".join(where_items))
            if group_items:
                sql_parts.append("GROUP BY " + ", ".join(group_items))
            subqueries.append(
                {
                    "alias": f"g{index}",
                    "sql": "\n".join(sql_parts),
                    "keys": key_aliases,
                    "metrics": metric_aliases,
                }
            )

        limit = max(1, min(plan.intent.limit, self.MAX_LIMIT))
        if not dimensions:
            select_items = [
                f"{subquery['alias']}.{_quote_ident(metric_alias)} AS {_quote_ident(metric_alias)}"
                for subquery in subqueries
                for metric_alias in subquery["metrics"]
            ]
            return "\n".join(
                [
                    "SELECT " + ", ".join(select_items),
                    f"FROM ({subqueries[0]['sql']}) {subqueries[0]['alias']}",
                    *[
                        f"CROSS JOIN ({subquery['sql']}) {subquery['alias']}"
                        for subquery in subqueries[1:]
                    ],
                    f"LIMIT {limit}",
                ]
            )

        key_selects = [
            "SELECT " + ", ".join(_quote_ident(key) for key in subquery["keys"])
            + f" FROM ({subquery['sql']}) {subquery['alias']}_keys"
            for subquery in subqueries
        ]
        key_source = "\nUNION\n".join(key_selects)
        output_items = [
            f"keys.{_quote_ident(f'k{index}')} AS {_quote_ident(_safe_alias(column.business_name))}"
            for index, column in enumerate(dimensions)
        ]
        output_items.extend(
            f"{subquery['alias']}.{_quote_ident(metric_alias)} AS {_quote_ident(metric_alias)}"
            for subquery in subqueries
            for metric_alias in subquery["metrics"]
        )
        sql_parts = [
            "SELECT " + ", ".join(output_items),
            f"FROM ({key_source}) keys",
        ]
        for subquery in subqueries:
            conditions = " AND ".join(
                f"{subquery['alias']}.{_quote_ident(key)} <=> keys.{_quote_ident(key)}"
                for key in subquery["keys"]
            )
            sql_parts.append(
                f"LEFT JOIN ({subquery['sql']}) {subquery['alias']} ON {conditions}"
            )
        if plan.intent.order_by:
            first = plan.intent.order_by[0]
            direction = "ASC" if first.get("direction") == "asc" else "DESC"
            order_field = str(first.get("field") or "")
            order_metric = next(
                (
                    metric
                    for metric in metric_items
                    if order_field in {metric.name, metric.business_name, _safe_alias(metric.business_name)}
                ),
                metric_items[0] if metric_items else None,
            )
            if order_metric:
                sql_parts.append(
                    f"ORDER BY {_quote_ident(_safe_alias(order_metric.business_name))} {direction}"
                )
        sql_parts.append(f"LIMIT {limit}")
        return "\n".join(sql_parts)

    def _metric_formula_columns(self, metric: SemanticMetric, catalog: SemanticCatalog) -> list[SemanticColumn]:
        placeholders = re.findall(
            r"\{([A-Za-z_][\w$]*|[\u4e00-\u9fff][\w\u4e00-\u9fff]*)\}",
            metric.formula or "",
        )
        columns: list[SemanticColumn] = []
        for name in placeholders:
            matches = [
                column
                for column in catalog.columns_by_table.get(metric.table_id, [])
                if _normalize_term(column.physical_name) == _normalize_term(name)
                or _normalize_term(column.business_name) == _normalize_term(name)
            ]
            if len(matches) != 1:
                raise SemanticQueryError(
                    "metric_formula_invalid",
                    f"指标公式字段无法唯一确定: {metric.business_name}.{name}",
                    safe_to_fallback=True,
                    details={"metric_id": metric.id, "field": name},
                )
            columns.append(matches[0])
        return columns

    def _supports_independent_metric_plan(
        self,
        intent: IntentQuery,
        metrics: list[SemanticMetric],
        dimension_columns: list[SemanticColumn],
    ) -> bool:
        metric_names = {metric.name for metric in metrics}
        if {"inbound_quantity", "outbound_quantity", "current_inventory_quantity"} <= metric_names and not dimension_columns:
            return True
        if {"outbound_quantity", "current_inventory_quantity"} <= metric_names and intent.query_type in {"aggregate", "topn"}:
            return True
        return False

    def _compile_independent_metric_sql(
        self,
        plan: SemanticPlan,
        catalog: SemanticCatalog,
        metric_items: list[SemanticMetric],
    ) -> str:
        metric_by_name = {metric.name: metric for metric in metric_items}
        metric_names = set(metric_by_name)
        if {"inbound_quantity", "outbound_quantity", "current_inventory_quantity"} <= metric_names:
            inbound = metric_by_name["inbound_quantity"]
            outbound = metric_by_name["outbound_quantity"]
            inventory = metric_by_name["current_inventory_quantity"]
            inbound_column = self._single_metric_formula_column(inbound, catalog)
            outbound_column = self._single_metric_formula_column(outbound, catalog)
            inventory_column = self._single_metric_formula_column(inventory, catalog)
            inbound_table = catalog.table_by_id[inbound.table_id]
            outbound_table = catalog.table_by_id[outbound.table_id]
            inventory_table = catalog.table_by_id[inventory.table_id]
            inbound_dim = self._find_column_in_scope("item_name", catalog, {inbound.table_id}) or self._find_column_in_scope(
                "品名", catalog, {inbound.table_id}
            )
            outbound_dim = self._find_column_in_scope("item_name", catalog, {outbound.table_id}) or self._find_column_in_scope(
                "品名", catalog, {outbound.table_id}
            )
            inventory_dim = self._find_column_in_scope(
                "item_name", catalog, {inventory.table_id}
            ) or self._find_column_in_scope("品名", catalog, {inventory.table_id})
            if not inbound_dim or not outbound_dim or not inventory_dim:
                return ""

            item_filter = next(
                (
                    str(item.get("value") or "").strip()
                    for item in plan.intent.filters
                    if (
                        str(item.get("operator") or "").lower() == "independent_item_contains"
                        or (
                            str(item.get("operator") or "").lower() in {"contains", "eq"}
                            and _normalize_term(str(item.get("field") or ""))
                            in {"品名", "物料名称", "item_name", "mname"}
                        )
                    )
                    and str(item.get("value") or "").strip()
                ),
                "",
            )

            def where_with(conditions: list[str]) -> str:
                return "WHERE " + " AND ".join(conditions) if conditions else ""

            inbound_where = ""
            inbound_time = catalog.column_by_id.get(inbound.time_column_id) if inbound.time_column_id else None
            inbound_conditions: list[str] = []
            inbound_row_scope = self._compile_row_scope_predicate_for_table(inbound_table, "i", catalog, plan.user_access)
            if inbound_row_scope:
                inbound_conditions.append(inbound_row_scope)
            if inbound_time:
                predicate = self._compile_time_range_predicate("i", inbound_time, plan.intent.time_range)
                if predicate:
                    inbound_conditions.append(predicate)
            if item_filter:
                inbound_conditions.append(
                    f"i.{_quote_ident(inbound_dim.physical_name)} LIKE {_sql_literal('%' + item_filter + '%')}"
                )
            inbound_where = where_with(inbound_conditions)
            outbound_where = ""
            outbound_time = catalog.column_by_id.get(outbound.time_column_id) if outbound.time_column_id else None
            outbound_conditions: list[str] = []
            outbound_row_scope = self._compile_row_scope_predicate_for_table(outbound_table, "o", catalog, plan.user_access)
            if outbound_row_scope:
                outbound_conditions.append(outbound_row_scope)
            if outbound_time:
                predicate = self._compile_time_range_predicate("o", outbound_time, plan.intent.time_range)
                if predicate:
                    outbound_conditions.append(predicate)
            if item_filter:
                outbound_conditions.append(
                    f"o.{_quote_ident(outbound_dim.physical_name)} LIKE {_sql_literal('%' + item_filter + '%')}"
                )
            outbound_where = where_with(outbound_conditions)
            inventory_conditions: list[str] = []
            inventory_row_scope = self._compile_row_scope_predicate_for_table(inventory_table, "n", catalog, plan.user_access)
            if inventory_row_scope:
                inventory_conditions.append(inventory_row_scope)
            if item_filter:
                inventory_conditions.append(
                    f"n.{_quote_ident(inventory_dim.physical_name)} LIKE {_sql_literal('%' + item_filter + '%')}"
                )
            inventory_where = where_with(inventory_conditions)
            inbound_alias = _safe_alias(inbound.business_name)
            outbound_alias = _safe_alias(outbound.business_name)
            inventory_alias = _safe_alias(inventory.business_name)
            limit = max(1, min(plan.intent.limit, self.MAX_LIMIT))
            return "\n".join(
                [
                    "SELECT "
                    f"k.`item_name` AS {_quote_ident(_safe_alias(inventory_dim.business_name))}, "
                    f"COALESCE(i.{_quote_ident(inbound_alias)}, 0) AS {_quote_ident(inbound_alias)}, "
                    f"COALESCE(o.{_quote_ident(outbound_alias)}, 0) AS {_quote_ident(outbound_alias)}, "
                    f"COALESCE(n.{_quote_ident(inventory_alias)}, 0) AS {_quote_ident(inventory_alias)}",
                    "FROM (",
                    f"  SELECT i.{_quote_ident(inbound_dim.physical_name)} AS `item_name` FROM {_quote_ident(inbound_table.physical_name)} i {inbound_where}".rstrip(),
                    "  UNION",
                    f"  SELECT o.{_quote_ident(outbound_dim.physical_name)} AS `item_name` FROM {_quote_ident(outbound_table.physical_name)} o {outbound_where}".rstrip(),
                    "  UNION",
                    f"  SELECT n.{_quote_ident(inventory_dim.physical_name)} AS `item_name` FROM {_quote_ident(inventory_table.physical_name)} n {inventory_where}".rstrip(),
                    ") k",
                    "LEFT JOIN (",
                    f"  SELECT i.{_quote_ident(inbound_dim.physical_name)} AS `item_name`, "
                    f"SUM(i.{_quote_ident(inbound_column.physical_name)}) AS {_quote_ident(inbound_alias)}",
                    f"  FROM {_quote_ident(inbound_table.physical_name)} i",
                    f"  {inbound_where}".rstrip(),
                    f"  GROUP BY i.{_quote_ident(inbound_dim.physical_name)}",
                    ") i ON i.`item_name` = k.`item_name`",
                    "LEFT JOIN (",
                    f"  SELECT o.{_quote_ident(outbound_dim.physical_name)} AS `item_name`, "
                    f"SUM(o.{_quote_ident(outbound_column.physical_name)}) AS {_quote_ident(outbound_alias)}",
                    f"  FROM {_quote_ident(outbound_table.physical_name)} o",
                    f"  {outbound_where}".rstrip(),
                    f"  GROUP BY o.{_quote_ident(outbound_dim.physical_name)}",
                    ") o ON o.`item_name` = k.`item_name`",
                    "LEFT JOIN (",
                    f"  SELECT n.{_quote_ident(inventory_dim.physical_name)} AS `item_name`, "
                    f"SUM(n.{_quote_ident(inventory_column.physical_name)}) AS {_quote_ident(inventory_alias)}",
                    f"  FROM {_quote_ident(inventory_table.physical_name)} n",
                    f"  {inventory_where}".rstrip(),
                    f"  GROUP BY n.{_quote_ident(inventory_dim.physical_name)}",
                    ") n ON n.`item_name` = k.`item_name`",
                    f"LIMIT {limit}",
                ]
            )

        if {"outbound_quantity", "current_inventory_quantity"} <= metric_names and plan.intent.query_type == "topn":
            outbound = metric_by_name["outbound_quantity"]
            inventory = metric_by_name["current_inventory_quantity"]
            outbound_column = self._single_metric_formula_column(outbound, catalog)
            inventory_column = self._single_metric_formula_column(inventory, catalog)
            outbound_table = catalog.table_by_id[outbound.table_id]
            inventory_table = catalog.table_by_id[inventory.table_id]
            outbound_dim = self._find_column_in_scope("item_name", catalog, {outbound.table_id}) or self._find_column_in_scope("品名", catalog, {outbound.table_id})
            inventory_dim = self._find_column_in_scope("item_name", catalog, {inventory.table_id}) or self._find_column_in_scope("品名", catalog, {inventory.table_id})
            if not outbound_dim or not inventory_dim:
                return ""
            outbound_conditions: list[str] = []
            outbound_row_scope = self._compile_row_scope_predicate_for_table(outbound_table, "o", catalog, plan.user_access)
            if outbound_row_scope:
                outbound_conditions.append(outbound_row_scope)
            outbound_time = catalog.column_by_id.get(outbound.time_column_id) if outbound.time_column_id else None
            if outbound_time:
                predicate = self._compile_time_range_predicate("o", outbound_time, plan.intent.time_range)
                if predicate:
                    outbound_conditions.append(predicate)
            outbound_where = "WHERE " + " AND ".join(outbound_conditions) if outbound_conditions else ""
            inventory_conditions: list[str] = []
            inventory_row_scope = self._compile_row_scope_predicate_for_table(inventory_table, "i", catalog, plan.user_access)
            if inventory_row_scope:
                inventory_conditions.append(inventory_row_scope)
            inventory_where = "WHERE " + " AND ".join(inventory_conditions) if inventory_conditions else ""
            direction = "ASC" if (plan.intent.order_by and plan.intent.order_by[0].get("direction") == "asc") else "DESC"
            limit = max(1, min(plan.intent.limit, self.MAX_LIMIT))
            outbound_alias = _safe_alias(outbound.business_name)
            inventory_alias = _safe_alias(inventory.business_name)
            turnover_alias = "库存周转率"
            return "\n".join(
                [
                    "SELECT "
                    f"i.{_quote_ident(inventory_dim.physical_name)} AS {_quote_ident(_safe_alias(inventory_dim.business_name))}, "
                    f"COALESCE(o.{_quote_ident(outbound_alias)}, 0) AS {_quote_ident(outbound_alias)}, "
                    f"SUM(i.{_quote_ident(inventory_column.physical_name)}) AS {_quote_ident(inventory_alias)}, "
                    f"COALESCE(o.{_quote_ident(outbound_alias)}, 0) / NULLIF(SUM(i.{_quote_ident(inventory_column.physical_name)}), 0) AS {_quote_ident(turnover_alias)}",
                    f"FROM {_quote_ident(inventory_table.physical_name)} i",
                    "LEFT JOIN (",
                    f"  SELECT o.{_quote_ident(outbound_dim.physical_name)} AS {_quote_ident(outbound_dim.physical_name)}, "
                    f"SUM(o.{_quote_ident(outbound_column.physical_name)}) AS {_quote_ident(outbound_alias)}",
                    f"  FROM {_quote_ident(outbound_table.physical_name)} o",
                    f"  {outbound_where}".rstrip(),
                    f"  GROUP BY o.{_quote_ident(outbound_dim.physical_name)}",
            f") o ON o.{_quote_ident(outbound_dim.physical_name)} = i.{_quote_ident(inventory_dim.physical_name)}",
            f"{inventory_where}".rstrip(),
            f"GROUP BY i.{_quote_ident(inventory_dim.physical_name)}, o.{_quote_ident(outbound_alias)}",
            f"HAVING SUM(i.{_quote_ident(inventory_column.physical_name)}) > 0",
            f"ORDER BY {_quote_ident(turnover_alias)} {direction}",
            f"LIMIT {limit}",
        ]
    )
        return ""

    def _single_metric_formula_column(self, metric: SemanticMetric, catalog: SemanticCatalog) -> SemanticColumn:
        columns = self._metric_formula_columns(metric, catalog)
        if len(columns) != 1:
            raise SemanticQueryError(
                "metric_formula_invalid",
                f"指标公式字段无法唯一确定: {metric.business_name}",
                safe_to_fallback=True,
                details={"metric_id": metric.id},
            )
        return columns[0]

    def _compile_metric_subquery(
        self,
        metric: SemanticMetric,
        catalog: SemanticCatalog,
        time_range: Optional[str],
    ) -> str:
        table = catalog.table_by_id.get(metric.table_id)
        if not table:
            raise SemanticQueryError("semantic_object_not_found", "指标缺少所属表", details={"metric_id": metric.id})
        formula = self._qualify_metric_formula(metric, catalog, "m")
        sql = f"SELECT {formula} FROM {_quote_ident(table.physical_name)} m"
        time_column = catalog.column_by_id.get(metric.time_column_id) if metric.time_column_id else None
        if time_column and time_range:
            predicate = self._compile_time_range_predicate("m", time_column, time_range)
            if predicate:
                sql += f" WHERE {predicate}"
        return sql

    def _compile_time_bucket_expr(
        self,
        alias: str,
        column: SemanticColumn,
        default_grain: Optional[str],
    ) -> str:
        grain = (default_grain or "month").lower()
        fmt = "%Y-%m-%d" if grain in {"day", "daily", "date"} else "%Y-%m"
        return f"DATE_FORMAT({alias}.{_quote_ident(column.physical_name)}, '{fmt}')"

    def _compile_row_scope_predicates(
        self,
        plan: SemanticPlan,
        catalog: SemanticCatalog,
        aliases: dict[int, str],
    ) -> list[str]:
        predicates: list[str] = []
        for table_id in plan.table_ids:
            table = catalog.table_by_id.get(table_id)
            if not table:
                continue
            alias = aliases.get(table_id)
            if not alias:
                if self._table_requires_row_scope(table, plan.user_access):
                    raise SemanticQueryError(
                        "row_permission_missing",
                        f"无法为语义表应用行级权限: {table.business_name}",
                        safe_to_fallback=False,
                        details={"table_id": table.id},
                    )
                continue
            predicate = self._compile_row_scope_predicate_for_table(
                table,
                alias,
                catalog,
                plan.user_access,
            )
            if predicate:
                predicates.append(predicate)
        return predicates

    def _compile_row_scope_predicate_for_table(
        self,
        table: SemanticTable,
        alias: str,
        catalog: SemanticCatalog,
        user_access: dict[str, Any],
    ) -> str:
        try:
            return get_semantic_access_policy_service().compile_table_predicate(
                table,
                catalog.columns_by_table.get(table.id, []),
                user_access,
                alias=alias,
            )
        except AccessPolicyError as exc:
            raise SemanticQueryError(
                "row_permission_missing",
                exc.message,
                safe_to_fallback=False,
                details={"table_id": table.id, "code": exc.code},
            ) from exc

    def _table_requires_row_scope(self, table: SemanticTable, user_access: dict[str, Any]) -> bool:
        return get_semantic_access_policy_service().table_requires_row_filter(table, user_access)

    def _row_scope_column(
        self,
        table: SemanticTable,
        column_id: Optional[int],
        catalog: SemanticCatalog,
        label: str,
    ) -> SemanticColumn:
        column = catalog.column_by_id.get(int(column_id)) if column_id else None
        if not column or column.table_id != table.id:
            raise SemanticQueryError(
                "row_scope_missing",
                f"语义表未配置{label}行级过滤字段: {table.business_name}",
                safe_to_fallback=False,
                details={"table_id": table.id, "required_column": label},
            )
        return column

    def _select_time_column(
        self,
        table_ids: set[int],
        intent: IntentQuery,
        catalog: SemanticCatalog,
    ) -> Optional[SemanticColumn]:
        if intent.selected_time_column_id:
            column = catalog.column_by_id.get(intent.selected_time_column_id)
            if column and column.table_id in table_ids and _is_active_semantic_column(column, catalog) and _is_time_column(column):
                return column
        metric_time_columns = []
        for metric_name in intent.metrics:
            metric = self._find_metric(metric_name, catalog)
            if not metric or not metric.time_column_id:
                continue
            column = catalog.column_by_id.get(metric.time_column_id)
            if (
                column
                and column.table_id in table_ids
                and _is_active_semantic_column(column, catalog)
                and _is_time_column(column)
            ):
                metric_time_columns.append(column)
        unique_metric_columns = {
            column.id: column for column in metric_time_columns
        }
        if len(unique_metric_columns) == 1:
            return next(iter(unique_metric_columns.values()))

        preferred = self._preferred_time_column(table_ids, intent, catalog)
        if preferred:
            return preferred

        candidate_columns = [
            column
            for table_id in table_ids
            for column in catalog.columns_by_table.get(table_id, [])
            if _is_active_semantic_column(column, catalog) and _is_time_column(column)
        ]
        if not candidate_columns:
            return None
        if len(candidate_columns) == 1:
            return candidate_columns[0]
        text = _normalize_term(
            " ".join(
                [
                    *intent.tables,
                    *intent.dimensions,
                    *[str(item.get("field") or "") for item in intent.filters],
                ]
            )
        )
        scored = []
        for column in candidate_columns:
            terms = _terms_for(column.physical_name, column.business_name, synonyms=column.synonyms)
            score = 0
            for term in terms:
                normalized = _normalize_term(term)
                compact = _compact_semantic_term(term)
                if normalized and normalized in text:
                    score += 10
                elif compact and compact in text:
                    score += 6
                elif compact and any(token in compact for token in ("跟进", "下单", "制单", "创建", "访问")):
                    for token in ("跟进", "下单", "制单", "创建", "访问"):
                        if token in compact and token in text:
                            score += 4
            scored.append((score, column))
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1]
        raise SemanticQueryError(
            "time_field_ambiguous",
            "存在多个候选时间字段，请明确时间口径",
            retryable=False,
            safe_to_fallback=True,
            details={
                "intent": intent.model_dump(mode="json"),
                "candidate_time_columns": [
                    {
                        "id": column.id,
                        "business_name": column.business_name,
                        "physical_name": column.physical_name,
                    }
                    for _, column in scored
                ],
            },
        )

    def _preferred_time_column(
        self,
        table_ids: set[int],
        intent: IntentQuery,
        catalog: SemanticCatalog,
    ) -> Optional[SemanticColumn]:
        metric_names = set(intent.metrics)
        if metric_names & {
            "sales_quantity",
            "sales_detail_amount",
            "sales_detail_quantity",
            "sales_order_line_count",
            "avg_sales_unit_price",
            "sum_clean_jf_sale_order_1_quantity",
            "sum_clean_jf_sale_order_1_tax_amount",
            "avg_unit_price_clean_jf_sale_order_1",
        }:
            column = self._find_physical_column_in_tables("make_date", table_ids, catalog)
            if column:
                return column
        if metric_names & {"dispatch_quantity", "dispatch_order_count"}:
            column = self._find_physical_column_in_tables("make_date", table_ids, catalog)
            if column:
                return column
        resolved_table_names = [
            value
            for table_id in table_ids
            for table in [catalog.table_by_id.get(table_id)]
            if table
            for value in (table.business_name, table.physical_name)
        ]
        if any("销售订单" in name for name in [*intent.tables, *resolved_table_names]):
            column = self._find_physical_column_in_tables("make_date", table_ids, catalog)
            if column:
                return column
        if metric_names & {"inbound_quantity", "outbound_quantity"} and len(metric_names) == 1:
            column = self._find_physical_column_in_tables("create_time", table_ids, catalog)
            if column:
                return column
        return None

    def _find_physical_column_in_tables(
        self,
        physical_name: str,
        table_ids: set[int],
        catalog: SemanticCatalog,
    ) -> Optional[SemanticColumn]:
        for table_id in table_ids:
            for column in catalog.columns_by_table.get(table_id, []):
                if (
                    _is_active_semantic_column(column, catalog)
                    and _normalize_term(column.physical_name) == _normalize_term(physical_name)
                ):
                    return column
        return None

    def _filter_columns_for_item(
        self,
        filter_item: dict[str, Any],
        catalog: SemanticCatalog,
        table_ids: set[int] | list[int] | tuple[int, ...] | None = None,
    ) -> list[SemanticColumn]:
        if str(filter_item.get("operator") or "").lower() == "independent_item_contains":
            return []
        if str(filter_item.get("operator") or "").lower() == "raw_or":
            columns = []
            for clause in filter_item.get("clauses") or []:
                if not isinstance(clause, dict):
                    continue
                column = self._find_column_in_scope(str(clause.get("field") or ""), catalog, table_ids)
                if column:
                    columns.append(column)
            return list({column.id: column for column in columns}.values())
        column = self._find_column_in_scope(str(filter_item.get("field") or ""), catalog, table_ids)
        return [column] if column else []

    def _compile_filter_item(
        self,
        aliases: dict[int, str],
        catalog: SemanticCatalog,
        table_ids: set[int],
        filter_item: dict[str, Any],
    ) -> str:
        operator = str(filter_item.get("operator") or "eq").lower()
        if operator == "independent_item_contains":
            return ""
        if operator == "raw_or":
            predicates = []
            for clause in filter_item.get("clauses") or []:
                if not isinstance(clause, dict):
                    continue
                predicate = self._compile_filter_item(aliases, catalog, table_ids, clause)
                if predicate:
                    predicates.append(predicate)
            return "(" + " OR ".join(predicates) + ")" if predicates else ""

        column = self._find_column_in_scope(str(filter_item.get("field") or ""), catalog, table_ids)
        if not column:
            return ""
        alias = aliases.get(column.table_id, "t0")
        return self._compile_filter_predicate(alias, column, filter_item)

    def _compile_filter_predicate(self, alias: str, column: SemanticColumn, filter_item: dict[str, Any]) -> str:
        field = f"{alias}.{_quote_ident(column.physical_name)}"
        operator = str(filter_item.get("operator") or "eq").lower()
        if operator == "eq":
            return f"{field} = {_sql_literal(filter_item.get('value'))}"
        if operator == "not_eq":
            return f"{field} <> {_sql_literal(filter_item.get('value'))}"
        if operator == "in":
            values = filter_item.get("values") if isinstance(filter_item.get("values"), list) else []
            cleaned = [value for value in values if value is not None and value != ""]
            if not cleaned:
                return ""
            return f"{field} IN (" + ", ".join(_sql_literal(value) for value in cleaned) + ")"
        if operator == "contains":
            value = str(filter_item.get("value") or "")
            if not value:
                return ""
            return f"{field} LIKE {_sql_literal('%' + value + '%')}"
        if operator == "gte":
            return f"{field} >= {_sql_literal(filter_item.get('value'))}"
        if operator == "lte":
            return f"{field} <= {_sql_literal(filter_item.get('value'))}"
        if operator == "between":
            values = filter_item.get("values") if isinstance(filter_item.get("values"), list) else []
            if len(values) < 2:
                return ""
            end_value = values[1]
            try:
                start_date = datetime.strptime(str(values[0]), "%Y-%m-%d").date()
                end_date = datetime.strptime(str(values[1]), "%Y-%m-%d").date()
                if start_date.day == 1 and end_date.day >= 28:
                    end_value = (end_date + timedelta(days=1)).isoformat()
            except ValueError:
                end_value = values[1]
            return f"{field} >= {_sql_literal(values[0])} AND {field} < {_sql_literal(end_value)}"
        if operator == "lt_today":
            return f"{field} < CURDATE()"
        if operator == "gt_today":
            return f"{field} > CURDATE()"
        return ""

    def _compile_time_range_predicate(
        self,
        alias: str,
        column: SemanticColumn,
        time_range: Optional[str],
    ) -> str:
        if not time_range:
            return ""
        field = f"{alias}.{_quote_ident(column.physical_name)}"
        text = str(time_range)
        if ".." in text:
            start_value, end_value = text.split("..", 1)
            start_bounds = self._month_bounds(start_value.strip())
            end_bounds = self._month_bounds(end_value.strip())
            if start_bounds and end_bounds:
                return f"{field} >= {_sql_literal(start_bounds[0])} AND {field} < {_sql_literal(end_bounds[1])}"
        match = re.search(r"(近|最近)(\d+)个?(天|日|月|年)", text)
        if match:
            amount = max(1, int(match.group(2)))
            unit_map = {"天": "DAY", "日": "DAY", "月": "MONTH", "年": "YEAR"}
            return f"{field} >= DATE_SUB(CURDATE(), INTERVAL {amount} {unit_map[match.group(3)]})"
        if text == "今年":
            return f"{field} >= MAKEDATE(YEAR(CURDATE()), 1)"
        if text == "本月":
            return f"{field} >= DATE_FORMAT(CURDATE(), '%Y-%m-01')"
        if text == "上月":
            return (
                f"{field} >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01') "
                f"AND {field} < DATE_FORMAT(CURDATE(), '%Y-%m-01')"
            )
        month = re.fullmatch(r"(\d{4})-(\d{2})", text)
        if month:
            bounds = self._month_bounds(text)
            if bounds:
                return f"{field} >= {_sql_literal(bounds[0])} AND {field} < {_sql_literal(bounds[1])}"
        year = re.fullmatch(r"(\d{4})", text)
        if year:
            start = f"{int(year.group(1)):04d}-01-01"
            end = f"{int(year.group(1)) + 1:04d}-01-01"
            return f"{field} >= {_sql_literal(start)} AND {field} < {_sql_literal(end)}"
        return ""

    def _validate_compiled_sql(self, sql: str) -> None:
        try:
            import sqlglot

            statements = sqlglot.parse(sql, read="mysql")
        except Exception as exc:  # noqa: BLE001
            raise SemanticQueryError("unsafe_sql", f"SQL 解析失败: {exc}", safe_to_fallback=False) from exc
        if len(statements) != 1:
            raise SemanticQueryError("unsafe_sql", "拒绝执行多语句 SQL", safe_to_fallback=False)
        if statements[0].__class__.__name__.lower() != "select":
            raise SemanticQueryError("unsafe_sql", "只允许执行 SELECT 查询", safe_to_fallback=False)
        if " LIMIT " not in f" {sql.upper()} ":
            raise SemanticQueryError("unsafe_sql", "语义查询必须带 LIMIT", safe_to_fallback=False)

    async def _load_user_access(self, user_id: str, workspace_id: str) -> dict[str, Any]:
        try:
            db_manager = get_async_db_manager()
            async with db_manager.session_scope() as session:
                user = (
                    await session.execute(
                        select(UserModel).where(
                            UserModel.id == user_id,
                            UserModel.workspace_id == workspace_id,
                        )
                        .options(selectinload(UserModel.roles).selectinload(RoleModel.permissions))
                    )
                ).scalar_one_or_none()
                role_ids = [str(role.id) for role in (user.roles if user else [])]
                role_names = [str(role.name) for role in (user.roles if user else [])]
                data_scope = 1 if (user and user.is_admin) else 4
                if user and not user.is_admin:
                    for role in user.roles:
                        if role.data_scope and int(role.data_scope) < data_scope:
                            data_scope = int(role.data_scope)
                dept_id = getattr(user, "department_id", None) if user else None
                scope_dept_ids = await self._load_scope_dept_ids(
                    session,
                    workspace_id,
                    dept_id,
                    data_scope,
                )
                return {
                    "user_id": user_id,
                    "username": user.username if user else None,
                    "role_ids": role_ids,
                    "role_names": role_names,
                    "is_admin": bool(user and user.is_admin),
                    "data_scope": data_scope,
                    "dept_id": dept_id,
                    "scope_dept_ids": scope_dept_ids,
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("语义权限上下文加载失败: %s", exc)
            raise SemanticQueryError(
                "permission_check_failed",
                "权限上下文加载失败，已按 fail closed 拒绝执行",
                safe_to_fallback=False,
            ) from exc

    async def _load_scope_dept_ids(
        self,
        session,
        workspace_id: str,
        dept_id: Optional[int],
        data_scope: int,
    ) -> list[int]:
        if not dept_id or data_scope not in (2, 3):
            return []
        if data_scope == 3:
            return [int(dept_id)]
        result = await session.execute(
            select(DepartmentModel.id).where(
                DepartmentModel.workspace_id == workspace_id,
                DepartmentModel.status == True,  # noqa: E712
                or_(
                    DepartmentModel.id == int(dept_id),
                    DepartmentModel.ancestors.like(f"%/{int(dept_id)}/%"),
                ),
            )
        )
        return sorted({int(item) for item in result.scalars().all()})

    def _check_semantic_permissions(
        self,
        objects: list[tuple[str, Any]],
        user_access: dict[str, Any],
        catalog: SemanticCatalog,
        intent: IntentQuery,
    ) -> list[dict[str, Any]]:
        actions = []
        blocked: list[dict[str, Any]] = []
        for object_type, obj in objects:
            label = getattr(obj, "business_name", None) or getattr(obj, "physical_name", None) or getattr(obj, "name", "")
            decision = get_semantic_access_policy_service().check_object_access(
                object_type,
                obj,
                user_access,
            )
            if decision.get("allowed"):
                actions.append(
                    {
                        "object_type": object_type,
                        "object_id": obj.id,
                        "action": decision.get("action") or "allow",
                        "policy_ids": decision.get("policy_ids") or [],
                    }
                )
                continue
            if decision.get("code") == "sensitive_object":
                raise SemanticQueryError(
                    "sensitive_object",
                    f"字段或指标被标记为敏感，当前用户不可访问: {label}",
                    safe_to_fallback=False,
                    details={"object_type": object_type, "object_id": obj.id},
                )
            blocked.append(
                {
                    "object_type": object_type,
                    "object_id": obj.id,
                    "label": label,
                    "reason": decision.get("reason") or "permission_denied",
                }
            )
        if blocked:
            clarification = self._build_permission_rewrite_clarification(
                blocked=blocked,
                catalog=catalog,
                intent=intent,
                user_access=user_access,
            )
            if clarification and clarification.options:
                raise SemanticQueryError(
                    "permission_rewrite_required",
                    clarification.message,
                    safe_to_fallback=False,
                    details={"clarification": clarification.model_dump(mode="json")},
                )
            labels = ", ".join(item["label"] for item in blocked if item.get("label"))
            raise SemanticQueryError(
                "permission_denied",
                f"当前用户无权访问语义对象: {labels or '未授权对象'}",
                safe_to_fallback=False,
                details={"blocked_objects": blocked},
            )
        return actions

    def _semantic_permission_action(
        self,
        object_type: str,
        obj: Any,
        user_access: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        decision = get_semantic_access_policy_service().check_object_access(
            object_type,
            obj,
            user_access,
        )
        if not decision.get("allowed"):
            return None
        return {
            "object_type": object_type,
            "object_id": obj.id,
            "action": decision.get("action") or "allow",
            "policy_ids": decision.get("policy_ids") or [],
        }

    def _build_permission_rewrite_clarification(
        self,
        *,
        blocked: list[dict[str, Any]],
        catalog: SemanticCatalog,
        intent: IntentQuery,
        user_access: dict[str, Any],
    ) -> Optional[SemanticClarification]:
        option_groups: dict[str, SemanticClarificationOption] = {}
        option_coverage: dict[str, set[tuple[str, int]]] = {}
        blocked_keys = {
            (str(item.get("object_type") or ""), int(item.get("object_id") or 0))
            for item in blocked
        }
        for blocked_item in blocked:
            blocked_key = (
                str(blocked_item.get("object_type") or ""),
                int(blocked_item.get("object_id") or 0),
            )
            for option in self._permission_rewrite_options(blocked_item, catalog, intent, user_access):
                existing = option_groups.get(option.label)
                if existing is None:
                    option_groups[option.label] = option.model_copy(deep=True)
                    option_coverage[option.label] = {blocked_key}
                    continue
                existing.selection_patch = self._merge_permission_rewrite_patches(
                    existing.selection_patch,
                    option.selection_patch,
                )
                option_coverage[option.label].add(blocked_key)
        options = [
            option
            for label, option in option_groups.items()
            if option_coverage.get(label) == blocked_keys
        ]
        if not options:
            return None
        base_intent = intent.model_dump(mode="json")
        for option in options:
            option.selection_patch["base_intent"] = base_intent
        blocked_labels = "、".join(item.get("label") or "未授权对象" for item in blocked)
        return SemanticClarification(
            kind="permission_rewrite_required",
            message=f"当前问题涉及您无权访问的对象：{blocked_labels}。请选择您想查询的替代内容",
            options=options[:8],
            blocked_objects=blocked,
            original_intent=base_intent,
        )

    def _merge_permission_rewrite_patches(
        self,
        current: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(current)
        for singular, plural in (
            ("replace_column", "replace_columns"),
            ("replace_metric", "replace_metrics"),
            ("replace_table", "replace_tables"),
        ):
            replacements: list[dict[str, Any]] = []
            for source in (current, incoming):
                many = source.get(plural)
                if isinstance(many, list):
                    replacements.extend(item for item in many if isinstance(item, dict))
                one = source.get(singular)
                if isinstance(one, dict):
                    replacements.append(one)
            if not replacements:
                continue
            unique: list[dict[str, Any]] = []
            seen: set[tuple[Any, Any]] = set()
            for replacement in replacements:
                key = (replacement.get("from_id"), replacement.get("to_id"))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(replacement)
            merged.pop(singular, None)
            merged[plural] = unique
        for key, value in incoming.items():
            if key not in {
                "replace_column",
                "replace_columns",
                "replace_metric",
                "replace_metrics",
                "replace_table",
                "replace_tables",
            }:
                merged[key] = value
        return merged

    def _permission_rewrite_options(
        self,
        blocked_item: dict[str, Any],
        catalog: SemanticCatalog,
        intent: IntentQuery,
        user_access: dict[str, Any],
    ) -> list[SemanticClarificationOption]:
        object_type = str(blocked_item.get("object_type") or "")
        object_id = int(blocked_item.get("object_id") or 0)
        if object_type == "metric":
            metric = catalog.metric_by_id.get(object_id)
            if not metric:
                return []
            candidates = [
                item
                for item in catalog.metrics
                if item.id != metric.id
                and _is_active_semantic_metric(item, catalog)
                and self._metric_can_be_rewrite_target(item, catalog, user_access)
            ]
            ranked = sorted(
                candidates,
                key=lambda item: self._alternative_score(
                    source=metric,
                    candidate=item,
                    intent=intent,
                    same_table=item.table_id == metric.table_id,
                ),
                reverse=True,
            )
            return [
                SemanticClarificationOption(
                    id=f"metric:{metric.id}->{candidate.id}",
                    label=f"改用指标：{candidate.business_name}",
                    selection_patch={
                        "replace_metric": {
                            "from_id": metric.id,
                            "from_name": metric.name,
                            "to_id": candidate.id,
                            "to_name": candidate.name,
                            "to_business_name": candidate.business_name,
                        }
                    },
                )
                for candidate in ranked[:5]
            ]
        if object_type == "column":
            column = catalog.column_by_id.get(object_id)
            if not column or not self._column_can_be_rewritten(column, intent):
                return []
            candidates = [
                item
                for item in catalog.columns
                if item.id != column.id
                and _is_active_semantic_column(item, catalog)
                and self._semantic_permission_action("column", item, user_access)
            ]
            ranked = sorted(
                candidates,
                key=lambda item: self._alternative_score(
                    source=column,
                    candidate=item,
                    intent=intent,
                    same_table=item.table_id == column.table_id,
                    same_type=str(item.data_type or "").lower() == str(column.data_type or "").lower(),
                ),
                reverse=True,
            )
            options = []
            for candidate in ranked[:5]:
                patch: dict[str, Any] = {
                    "replace_column": {
                        "from_id": column.id,
                        "from_name": column.physical_name,
                        "from_business_name": column.business_name,
                        "to_id": candidate.id,
                        "to_name": candidate.physical_name,
                        "to_business_name": candidate.business_name,
                    }
                }
                if intent.selected_time_column_id == column.id or _is_time_column(column):
                    patch["selected_time_column_id"] = candidate.id
                options.append(
                    SemanticClarificationOption(
                        id=f"column:{column.id}->{candidate.id}",
                        label=f"改用字段：{candidate.business_name}",
                        selection_patch=patch,
                    )
                )
            return options
        if object_type == "table":
            table = catalog.table_by_id.get(object_id)
            if not table:
                return []
            candidates = [
                item
                for item in catalog.tables
                if item.id != table.id
                and _is_active_semantic_table(item)
                and self._semantic_permission_action("table", item, user_access)
            ]
            ranked = sorted(
                candidates,
                key=lambda item: self._alternative_score(source=table, candidate=item, intent=intent),
                reverse=True,
            )
            return [
                SemanticClarificationOption(
                    id=f"table:{table.id}->{candidate.id}",
                    label=f"改用数据表：{candidate.business_name}",
                    selection_patch={
                        "replace_table": {
                            "from_id": table.id,
                            "from_name": table.physical_name,
                            "from_business_name": table.business_name,
                            "to_id": candidate.id,
                            "to_name": candidate.physical_name,
                            "to_business_name": candidate.business_name,
                        }
                    },
                )
                for candidate in ranked[:5]
            ]
        return []

    def _alternative_score(
        self,
        *,
        source: Any,
        candidate: Any,
        intent: IntentQuery,
        same_table: bool = False,
        same_type: bool = False,
    ) -> int:
        text = " ".join(
            [
                *intent.tables,
                *intent.metrics,
                *intent.dimensions,
                *[str(item.get("field") or "") for item in intent.filters],
                getattr(source, "business_name", "") or "",
                getattr(source, "physical_name", "") or getattr(source, "name", "") or "",
            ]
        )
        score = self._semantic_object_score(
            text,
            getattr(candidate, "physical_name", None) or getattr(candidate, "name", "") or "",
            getattr(candidate, "business_name", "") or "",
            getattr(candidate, "synonyms", []) or [],
        )
        if same_table:
            score += 20
        if same_type:
            score += 10
        return score

    def _metric_can_be_rewrite_target(
        self,
        metric: SemanticMetric,
        catalog: SemanticCatalog,
        user_access: dict[str, Any],
    ) -> bool:
        if not self._semantic_permission_action("metric", metric, user_access):
            return False
        try:
            column_ids = {item.id for item in self._metric_formula_columns(metric, catalog)}
        except SemanticQueryError:
            return False
        if metric.column_id:
            column_ids.add(metric.column_id)
        if metric.time_column_id:
            column_ids.add(metric.time_column_id)
        for column_id in column_ids:
            column = catalog.column_by_id.get(column_id)
            if column and not self._semantic_permission_action("column", column, user_access):
                return False
        return True

    def _column_can_be_rewritten(self, column: SemanticColumn, intent: IntentQuery) -> bool:
        labels = {
            _normalize_term(column.physical_name),
            _normalize_term(column.business_name),
            *{_normalize_term(item) for item in (column.synonyms or [])},
        }
        if intent.selected_time_column_id == column.id:
            return True
        for value in intent.dimensions:
            if _normalize_term(value) in labels:
                return True
        for item in intent.filters:
            if _normalize_term(str(item.get("field") or "")) in labels:
                return True
        for item in intent.order_by:
            if _normalize_term(str(item.get("field") or "")) in labels:
                return True
        return False

    def _apply_semantic_clarification(
        self,
        intent: IntentQuery,
        catalog: SemanticCatalog,
        semantic_clarification: Optional[dict[str, Any]],
    ) -> IntentQuery:
        if not isinstance(semantic_clarification, dict) or not semantic_clarification:
            return intent
        patch = semantic_clarification.get("selection_patch")
        if not isinstance(patch, dict):
            patch = semantic_clarification
        patch = dict(patch)
        free_text_time_range = self._extract_time_range(
            str(semantic_clarification.get("free_text") or "")
        )
        if free_text_time_range:
            free_text_intent_patch = dict(patch.get("intent_patch") or {})
            free_text_intent_patch["time_range"] = free_text_time_range
            patch["intent_patch"] = free_text_intent_patch
        legacy_column_choice = patch.get("replace_column")
        if (
            isinstance(legacy_column_choice, dict)
            and legacy_column_choice.get("to_id")
            and not legacy_column_choice.get("from_id")
            and not legacy_column_choice.get("from_name")
        ):
            legacy_column_choice = dict(legacy_column_choice)
            selected_column = catalog.column_by_id.get(int(legacy_column_choice["to_id"]))
            legacy_column_choice["from_name"] = str(
                legacy_column_choice.get("to_business_name")
                or (selected_column.business_name if selected_column else "")
            )
            legacy_column_choice.pop("to_business_name", None)
            patch["replace_column"] = legacy_column_choice
            legacy_intent_patch = dict(patch.get("intent_patch") or {})
            legacy_intent_patch.pop("dimensions", None)
            selected_table = (
                catalog.table_by_id.get(selected_column.table_id)
                if selected_column
                else None
            )
            if selected_table:
                legacy_intent_patch["tables"] = [selected_table.physical_name]
            patch["intent_patch"] = legacy_intent_patch
        base_intent = patch.get("base_intent")
        if isinstance(base_intent, dict):
            try:
                base_data = IntentQuery(**base_intent).model_dump(mode="json")
                current_data = intent.model_dump(mode="json")
                default_data = IntentQuery().model_dump(mode="json")
                data = (
                    current_data
                    if base_data == default_data and current_data != default_data
                    else base_data
                )
            except Exception:  # noqa: BLE001
                data = intent.model_dump(mode="json")
        else:
            data = intent.model_dump(mode="json")
        intent_patch = patch.get("intent_patch")
        if isinstance(intent_patch, dict):
            for key, value in intent_patch.items():
                if key in data:
                    data[key] = value
        if patch.get("selected_time_column_id"):
            data["selected_time_column_id"] = int(patch["selected_time_column_id"])
        replace_metrics = [
            item
            for item in (patch.get("replace_metrics") or [])
            if isinstance(item, dict)
        ]
        if isinstance(patch.get("replace_metric"), dict):
            replace_metrics.append(patch["replace_metric"])
        for replace_metric in replace_metrics:
            from_id = replace_metric.get("from_id")
            to_name = str(replace_metric.get("to_name") or replace_metric.get("to_business_name") or "")
            from_metric = catalog.metric_by_id.get(int(from_id)) if from_id else None
            from_labels = self._semantic_object_labels(from_metric) if from_metric else {_normalize_term(str(replace_metric.get("from_name") or ""))}
            data["metrics"] = [
                to_name if _normalize_term(item) in from_labels else item
                for item in data.get("metrics", [])
            ]
        replace_tables = [
            item
            for item in (patch.get("replace_tables") or [])
            if isinstance(item, dict)
        ]
        if isinstance(patch.get("replace_table"), dict):
            replace_tables.append(patch["replace_table"])
        for replace_table in replace_tables:
            from_id = replace_table.get("from_id")
            to_name = str(replace_table.get("to_business_name") or replace_table.get("to_name") or "")
            from_table = catalog.table_by_id.get(int(from_id)) if from_id else None
            from_labels = self._semantic_object_labels(from_table) if from_table else {_normalize_term(str(replace_table.get("from_name") or ""))}
            data["tables"] = [
                to_name if _normalize_term(item) in from_labels else item
                for item in data.get("tables", [])
            ]
        replace_columns = [
            item
            for item in (patch.get("replace_columns") or [])
            if isinstance(item, dict)
        ]
        if isinstance(patch.get("replace_column"), dict):
            replace_columns.append(patch["replace_column"])
        for replace_column in replace_columns:
            from_id = replace_column.get("from_id")
            to_name = str(replace_column.get("to_business_name") or replace_column.get("to_name") or "")
            from_column = catalog.column_by_id.get(int(from_id)) if from_id else None
            from_labels = self._semantic_object_labels(from_column) if from_column else {_normalize_term(str(replace_column.get("from_name") or ""))}
            data["dimensions"] = [
                to_name if _normalize_term(item) in from_labels else item
                for item in data.get("dimensions", [])
            ]
            for item in data.get("filters", []):
                if isinstance(item, dict) and _normalize_term(str(item.get("field") or "")) in from_labels:
                    item["field"] = to_name
            for item in data.get("order_by", []):
                if isinstance(item, dict) and _normalize_term(str(item.get("field") or "")) in from_labels:
                    item["field"] = to_name
        for key in ("tables", "metrics", "dimensions"):
            data[key] = list(dict.fromkeys(data.get(key, [])))
        return IntentQuery(**data)

    def _semantic_object_labels(self, obj: Any) -> set[str]:
        if not obj:
            return set()
        values = [
            getattr(obj, "physical_name", None),
            getattr(obj, "name", None),
            getattr(obj, "business_name", None),
            *list(getattr(obj, "synonyms", []) or []),
        ]
        return {_normalize_term(str(value)) for value in values if value}

    def _clarification_from_error(
        self,
        exc: SemanticQueryError,
        catalog: SemanticCatalog,
        intent: IntentQuery,
        user_access: dict[str, Any],
    ) -> Optional[SemanticClarification]:
        existing = exc.details.get("clarification") if isinstance(exc.details, dict) else None
        if isinstance(existing, dict):
            return SemanticClarification(**existing)
        if exc.error_type == "permission_rewrite_required":
            return SemanticClarification(**existing) if isinstance(existing, dict) else None
        if exc.error_type in {"metric_ambiguous", "field_ambiguous", "table_ambiguous", "time_field_ambiguous", "clarification_required"}:
            if exc.error_type == "metric_ambiguous" and exc.details.get("candidate_metric_ids"):
                return self._object_choice_clarification(
                    kind="metric_ambiguous",
                    message="指标存在多个候选，请选择本次查询要使用的指标。",
                    object_type="metric",
                    ids=exc.details.get("candidate_metric_ids") or [],
                    catalog=catalog,
                    intent=intent,
                    user_access=user_access,
                    requested_name=str(exc.details.get("matched_term") or ""),
                )
            if exc.error_type in {"time_field_ambiguous", "clarification_required"} and exc.details.get("candidate_time_columns"):
                return self._time_field_clarification(exc, catalog, intent, user_access)
            if exc.error_type == "field_ambiguous" and exc.details.get("candidate_column_ids"):
                return self._object_choice_clarification(
                    kind="field_ambiguous",
                    message="字段存在多个候选，请选择本次查询要使用的字段。",
                    object_type="column",
                    ids=exc.details.get("candidate_column_ids") or [],
                    catalog=catalog,
                    intent=intent,
                    user_access=user_access,
                    requested_name=str(exc.details.get("requested_name") or ""),
                )
            if exc.error_type == "table_ambiguous" and exc.details.get("candidate_table_ids"):
                return self._object_choice_clarification(
                    kind="table_ambiguous",
                    message="表存在多个候选，请选择本次查询要使用的数据表。",
                    object_type="table",
                    ids=exc.details.get("candidate_table_ids") or [],
                    catalog=catalog,
                    intent=intent,
                    user_access=user_access,
                    requested_name=str(exc.details.get("requested_name") or ""),
                )
        return None

    def _time_field_clarification(
        self,
        exc: SemanticQueryError,
        catalog: SemanticCatalog,
        intent: IntentQuery,
        user_access: dict[str, Any],
    ) -> Optional[SemanticClarification]:
        options: list[SemanticClarificationOption] = []
        for item in exc.details.get("candidate_time_columns") or []:
            column_id = int(item.get("id") or 0)
            column = catalog.column_by_id.get(column_id)
            if not column or not self._semantic_permission_action("column", column, user_access):
                continue
            table = catalog.table_by_id.get(column.table_id)
            table_label = table.business_name if table else column.physical_table
            options.append(
                SemanticClarificationOption(
                    id=f"time_column:{column.id}",
                    label=f"{table_label}.{column.business_name}",
                    selection_patch={"selected_time_column_id": column.id},
                )
            )
        if not options:
            return None
        return SemanticClarification(
            kind="time_field_ambiguous",
            message="存在多个候选时间字段，请选择本次查询使用的时间口径。",
            options=options[:8],
            original_intent=intent.model_dump(mode="json"),
        )

    def _object_choice_clarification(
        self,
        *,
        kind: str,
        message: str,
        object_type: str,
        ids: list[Any],
        catalog: SemanticCatalog,
        intent: IntentQuery,
        user_access: dict[str, Any],
        requested_name: str = "",
    ) -> Optional[SemanticClarification]:
        options: list[SemanticClarificationOption] = []
        base_intent = intent.model_dump(mode="json")
        base_patch = (
            {}
            if base_intent == IntentQuery().model_dump(mode="json")
            else {"base_intent": base_intent}
        )
        for raw_id in ids:
            object_id = int(raw_id)
            if object_type == "column":
                obj = catalog.column_by_id.get(object_id)
            elif object_type == "metric":
                obj = catalog.metric_by_id.get(object_id)
            else:
                obj = catalog.table_by_id.get(object_id)
            if not obj or not self._semantic_permission_action(object_type, obj, user_access):
                continue
            if object_type == "column":
                table = catalog.table_by_id.get(obj.table_id)
                label = f"{table.business_name if table else obj.physical_table}.{obj.business_name}"
                patch = {
                    **base_patch,
                    "replace_column": {
                        "from_name": requested_name or obj.business_name,
                        "to_id": obj.id,
                        "to_name": obj.physical_name,
                    },
                    "intent_patch": {
                        "tables": [table.physical_name if table else obj.physical_table],
                    },
                }
            elif object_type == "metric":
                table = catalog.table_by_id.get(obj.table_id)
                label = f"{table.business_name if table else obj.table_id}.{obj.business_name}"
                patch = {
                    **base_patch,
                    "replace_metric": {
                        "from_name": requested_name or obj.business_name,
                        "to_id": obj.id,
                        "to_name": obj.name,
                    },
                    "intent_patch": {
                        "tables": [table.physical_name] if table else list(intent.tables),
                    },
                }
            else:
                label = obj.business_name
                patch = {
                    **base_patch,
                    "replace_table": {
                        "from_name": requested_name or obj.business_name,
                        "to_id": obj.id,
                        "to_name": obj.physical_name,
                    },
                }
            options.append(
                SemanticClarificationOption(
                    id=f"{object_type}:{obj.id}",
                    label=label,
                    selection_patch=patch,
                )
            )
        if not options:
            return None
        return SemanticClarification(
            kind=kind,
            message=message,
            options=options[:8],
            original_intent=intent.model_dump(mode="json"),
        )

    def _objects_for_permission(
        self,
        table_ids: set[int],
        column_ids: set[int],
        metric_ids: set[int],
        catalog: SemanticCatalog,
    ) -> list[tuple[str, Any]]:
        objects: list[tuple[str, Any]] = []
        for table_id in sorted(table_ids):
            if table_id in catalog.table_by_id:
                objects.append(("table", catalog.table_by_id[table_id]))
        for column_id in sorted(column_ids):
            if column_id in catalog.column_by_id:
                objects.append(("column", catalog.column_by_id[column_id]))
        for metric_id in sorted(metric_ids):
            if metric_id in catalog.metric_by_id:
                objects.append(("metric", catalog.metric_by_id[metric_id]))
        return objects

    def _resolve_relationship_ids(self, table_ids: set[int], catalog: SemanticCatalog) -> list[int]:
        if len(table_ids) <= 1:
            return []
        resolved = set()
        remaining = set(table_ids)
        current = min(remaining)
        remaining.remove(current)
        resolved.add(current)
        relationship_ids = []
        while remaining:
            match = None
            for rel in self._relationships_for_catalog(catalog):
                if rel.status != "confirmed" or not rel.is_queryable or getattr(rel, "sync_state", "current") != "current":
                    continue
                left_in = rel.left_table_id in resolved
                right_in = rel.right_table_id in resolved
                if left_in and rel.right_table_id in remaining:
                    match = rel
                    break
                if right_in and rel.left_table_id in remaining:
                    match = rel
                    break
            if not match:
                raise SemanticQueryError(
                    "semantic_model_incomplete",
                    "多表查询缺少已确认 Join 路径",
                    retryable=False,
                    safe_to_fallback=True,
                    details={"table_ids": sorted(table_ids)},
                )
            relationship_ids.append(match.id)
            resolved.add(match.left_table_id)
            resolved.add(match.right_table_id)
            remaining.discard(match.left_table_id)
            remaining.discard(match.right_table_id)
        return relationship_ids

    def _relationships_for_catalog(self, catalog: SemanticCatalog) -> list[SemanticRelationship]:
        relationships = list(catalog.relationships)
        relationships.extend(self._canonical_relationships(catalog))
        return relationships

    def _canonical_metrics(self, catalog: SemanticCatalog) -> list[SemanticMetric]:
        canonical: list[SemanticMetric] = []

        def table_by_physical(name: str) -> Optional[SemanticTable]:
            return next(
                (
                    table
                    for table in catalog.tables
                    if _is_active_semantic_table(table)
                    and _normalize_term(table.physical_name) == _normalize_term(name)
                ),
                None,
            )

        def column_by_physical(table_id: int, name: str) -> Optional[SemanticColumn]:
            return next(
                (
                    column
                    for column in catalog.columns_by_table.get(table_id, [])
                    if _is_active_semantic_column(column, catalog)
                    and _normalize_term(column.physical_name) == _normalize_term(name)
                ),
                None,
            )

        def add_metric(
            *,
            metric_id: int,
            table: SemanticTable,
            name: str,
            business_name: str,
            formula: str,
            column: Optional[SemanticColumn],
            time_column: Optional[SemanticColumn],
            synonyms: list[str],
            aggregation: str,
        ) -> None:
            canonical.append(
                SemanticMetric(
                    id=metric_id,
                    workspace_id=table.workspace_id,
                    datasource_id=table.datasource_id,
                    name=name,
                    business_name=business_name,
                    description=f"Canonical runtime metric for {business_name}",
                    formula=formula,
                    aggregation=aggregation,
                    table_id=table.id,
                    column_id=column.id if column else None,
                    time_column_id=time_column.id if time_column else None,
                    default_grain="month",
                    synonyms=synonyms,
                    status="confirmed",
                    is_queryable=True,
                    confidence=0.99,
                )
            )

        follow = table_by_physical("clean_clue_follow_records")
        if follow:
            follow_id = column_by_physical(follow.id, "id")
            follow_time = column_by_physical(follow.id, "follow_time")
            if follow_id:
                add_metric(
                    metric_id=-2001,
                    table=follow,
                    name="follow_record_count",
                    business_name="跟进次数",
                    formula="COUNT(DISTINCT {id})",
                    column=follow_id,
                    time_column=follow_time,
                    synonyms=["跟进记录数", "订单跟进次数", "跟进次数"],
                    aggregation="count_distinct",
                )

        produce_task = table_by_physical("clean_produce_task")
        if produce_task:
            document_no = column_by_physical(produce_task.id, "document_no")
            document_date = column_by_physical(produce_task.id, "document_date")
            if document_no:
                add_metric(
                    metric_id=-2002,
                    table=produce_task,
                    name="produce_task_count",
                    business_name="生产任务数量",
                    formula="COUNT(DISTINCT {document_no})",
                    column=document_no,
                    time_column=document_date,
                    synonyms=["生产任务数", "任务数量", "任务数"],
                    aggregation="count_distinct",
                )

        incoming = table_by_physical("clean_incoming_material_acceptance_detail")
        if incoming:
            incoming_time = column_by_physical(incoming.id, "incoming_date")
            received = column_by_physical(incoming.id, "received_number")
            net_weight = column_by_physical(incoming.id, "net_weight")
            if received:
                add_metric(
                    metric_id=-2003,
                    table=incoming,
                    name="incoming_received_quantity",
                    business_name="实收数量",
                    formula="SUM({received_number})",
                    column=received,
                    time_column=incoming_time,
                    synonyms=["来料实收数量", "接收数量", "来料数量"],
                    aggregation="sum",
                )
            if net_weight:
                add_metric(
                    metric_id=-2004,
                    table=incoming,
                    name="incoming_net_weight",
                    business_name="净重",
                    formula="SUM({net_weight})",
                    column=net_weight,
                    time_column=incoming_time,
                    synonyms=["来料净重", "接收净重"],
                    aggregation="sum",
                )

        return canonical

    def _canonical_relationships(self, catalog: SemanticCatalog) -> list[SemanticRelationship]:
        canonical: list[SemanticRelationship] = []
        existing_pairs = {
            (rel.left_table_id, rel.right_table_id, rel.left_column_id, rel.right_column_id)
            for rel in catalog.relationships
            if rel.status == "confirmed" and rel.is_queryable
        }

        def table_by_physical(name: str) -> Optional[SemanticTable]:
            return next(
                (
                    table
                    for table in catalog.tables
                    if _is_active_semantic_table(table)
                    and _normalize_term(table.physical_name) == _normalize_term(name)
                ),
                None,
            )

        def column_by_physical(table_id: int, name: str) -> Optional[SemanticColumn]:
            return next(
                (
                    column
                    for column in catalog.columns_by_table.get(table_id, [])
                    if _is_active_semantic_column(column, catalog)
                    and _normalize_term(column.physical_name) == _normalize_term(name)
                ),
                None,
            )

        sale_detail = table_by_physical("clean_jf_sale_order_1")
        sale_order = table_by_physical("clean_jf_sale_order")
        if sale_detail and sale_order:
            left = column_by_physical(sale_detail.id, "main_code")
            right = column_by_physical(sale_order.id, "sale_order_no")
            if left and right and (sale_detail.id, sale_order.id, left.id, right.id) not in existing_pairs:
                canonical.append(
                    SemanticRelationship(
                        id=-1001,
                        workspace_id=sale_detail.workspace_id,
                        datasource_id=sale_detail.datasource_id,
                        left_table_id=sale_detail.id,
                        right_table_id=sale_order.id,
                        left_column_id=left.id,
                        right_column_id=right.id,
                        relationship_type="many_to_one",
                        confidence=0.99,
                        status="confirmed",
                        is_queryable=True,
                        description="Canonical sales detail relation: clean_jf_sale_order_1.main_code -> clean_jf_sale_order.sale_order_no",
                    )
                )
        return canonical

    def _find_metric(self, name: str, catalog: SemanticCatalog) -> Optional[SemanticMetric]:
        for metric in catalog.metrics:
            if not _is_active_semantic_metric(metric, catalog):
                continue
            if _normalize_term(name) in {
                _normalize_term(term)
                for term in _terms_for(metric.name, metric.business_name, synonyms=metric.synonyms)
            }:
                return metric
        return None

    def _find_column(self, name: str, catalog: SemanticCatalog) -> Optional[SemanticColumn]:
        matches: list[tuple[int, SemanticColumn]] = []
        for column in catalog.columns:
            if not _is_active_semantic_column(column, catalog):
                continue
            rank = self._column_match_rank(column, name)
            if rank is not None:
                matches.append((rank, column))
        if not matches:
            return None
        best_rank = min(rank for rank, _ in matches)
        best_matches = [column for rank, column in matches if rank == best_rank]
        if len(best_matches) > 1:
            raise SemanticQueryError(
                "field_ambiguous",
                f"字段存在多个候选: {name}",
                safe_to_fallback=True,
                details={"candidate_column_ids": [item.id for item in best_matches]},
            )
        return best_matches[0]

    def _find_table(self, name: str, catalog: SemanticCatalog) -> Optional[SemanticTable]:
        matches = []
        for table in catalog.tables:
            if table.status != "confirmed" or not table.is_queryable:
                continue
            if _normalize_term(name) in {
                _normalize_term(term)
                for term in _terms_for(table.physical_name, table.business_name, synonyms=table.synonyms)
            }:
                matches.append(table)
        if len(matches) > 1:
            raise SemanticQueryError(
                "table_ambiguous",
                f"表存在多个候选: {name}",
                safe_to_fallback=True,
                details={"candidate_table_ids": [item.id for item in matches]},
            )
        return matches[0] if matches else None

    def _extract_limit(self, text: str, default: int) -> int:
        match = re.search(r"(?:top|前)\s*(\d+)", text, re.I)
        if not match:
            match = re.search(r"(\d+)\s*(?:条|名|个(?![年月天日]))", text)
        if not match:
            return default
        return max(1, min(int(match.group(1)), self.MAX_LIMIT))

    async def record_query_run(
        self,
        *,
        workspace_id: str,
        user_id: Optional[str],
        session_id: Optional[str],
        question: str,
        semantic_enabled: bool,
        fallback_used: bool,
        status: str,
        error_type: Optional[str],
        intent: Optional[dict[str, Any]],
        plan: Optional[dict[str, Any]],
        sql: Optional[str],
        referenced_tables: list[str],
        row_count: int,
        execution_time_ms: int,
        legacy_sql: Optional[str] = None,
        runtime_mode: str = "disabled",
        correlation_id: Optional[str] = None,
        semantic_result: Optional[dict[str, Any]] = None,
        legacy_result: Optional[dict[str, Any]] = None,
        comparison: Optional[dict[str, Any]] = None,
        returned_chain: Optional[str] = None,
    ) -> int:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            model = SemanticQueryRunModel(
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                question=question,
                semantic_enabled=semantic_enabled,
                fallback_used=fallback_used,
                status=status,
                error_type=error_type,
                intent_json=intent,
                plan_json=plan,
                sql=sql,
                legacy_sql=legacy_sql,
                referenced_tables=referenced_tables,
                row_count=row_count,
                execution_time_ms=execution_time_ms,
                runtime_mode=runtime_mode,
                correlation_id=correlation_id,
                semantic_result_json=semantic_result,
                legacy_result_json=legacy_result,
                comparison_json=comparison,
                returned_chain=returned_chain,
            )
            session.add(model)
            await session.flush()
            run_id = int(model.id)
        try:
            from app.services.semantic_auto_governance_service import get_semantic_auto_governance_service

            await get_semantic_auto_governance_service().maybe_trigger_runtime_governance(workspace_id, user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("semantic runtime governance threshold check failed: %s", exc)
        return run_id


_semantic_query_service: Optional[SemanticQueryService] = None


def get_semantic_query_service() -> SemanticQueryService:
    global _semantic_query_service
    if _semantic_query_service is None:
        _semantic_query_service = SemanticQueryService()
    return _semantic_query_service
