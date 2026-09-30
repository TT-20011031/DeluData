"""Regression evaluation service for the first-stage semantic NL2SQL rollout."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.db.database import get_async_db_manager
from app.models.config.semantic import (
    SemanticEvaluationRun,
    SemanticEvaluationRunModel,
)
from app.services.semantic_evidence import compare_execution_results


@dataclass(frozen=True)
class SemanticEvalCase:
    case_id: str
    question: str
    test_dimension: str
    expected_focus: list[str] = field(default_factory=list)
    expected_limit: Optional[int] = None
    expected_query_type: Optional[str] = None
    expected_tables: list[str] = field(default_factory=list)
    required_sql_fragments: list[str] = field(default_factory=list)
    forbidden_sql_fragments: list[str] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "test_dimension": self.test_dimension,
            "expected_focus": self.expected_focus,
            "expected_limit": self.expected_limit,
            "expected_query_type": self.expected_query_type,
            "expected_tables": self.expected_tables,
            "required_sql_fragments": self.required_sql_fragments,
            "forbidden_sql_fragments": self.forbidden_sql_fragments,
        }


class SemanticEvaluationRunRequest(BaseModel):
    case_ids: list[str] = Field(default_factory=list)


EVALUATION_CASES: list[SemanticEvalCase] = [
    SemanticEvalCase("case_01", "查询最近 30 天销售订单明细，显示订单号、客户名称、制单日期、交货日期、订单状态，最多 50 条", "明细查询、limit、销售订单主表", ["limit", "客户名称", "日期格式"], 50),
    SemanticEvalCase("case_02", "统计 2026 年 5 月销售订单总金额、订单数量、明细数量和平均单价", "指标汇总、订单主从表", ["平均单价口径", "订单主从表"], None),
    SemanticEvalCase(
        "case_03",
        "按月统计 2025 年 3 月到 2026 年 5 月的销售金额趋势",
        "趋势分析、时间字段选择",
        ["月度趋势", "时间字段"],
        None,
        "trend",
        ["clean_jf_sale_order"],
        ["2025-03-01", "2026-06-01", "date_format"],
        [],
    ),
    SemanticEvalCase("case_04", "查询销售金额最高的 10 个客户，并显示订单数、总数量、总金额", "TopN、客户维度、聚合", ["Join fanout", "重复指标"], 10),
    SemanticEvalCase("case_05", "查询交货日期已经过期但订单状态未完成的销售订单", "状态口径、逾期判断", ["状态码", "逾期"], None),
    SemanticEvalCase("case_06", "查询近 180 天销售数量最高的 10 个品名，显示货号、规格、数量、金额", "TopN、商品维度", ["字段空值", "字段映射"], 10),
    SemanticEvalCase(
        "case_07",
        "对比 2026 年 4 月和 5 月各客户的销售金额变化",
        "对比分析、月份窗口",
        ["FULL OUTER JOIN", "MySQL 方言"],
        None,
        "compare",
        ["clean_jf_sale_order"],
        ["2026-04", "2026-05", "变化金额", "变化率"],
        ["full outer join"],
    ),
    SemanticEvalCase("case_08", "查询当前库存数量最低的 20 个物料，显示品名、规格、颜色、色号、库存数量", "库存明细、排序", ["空值提示", "库存排序"], 20),
    SemanticEvalCase("case_09", "统计近 180 天各物料的入库数量、出库数量和当前库存", "多表联查、库存口径", ["入库", "出库", "当前库存"], None),
    SemanticEvalCase("case_10", "查询近 180 天库存周转率最差的 10 个品名", "复杂指标、入出库+库存", ["库存周转率口径", "TopN"], 10),
    SemanticEvalCase("case_11", "按生产设备统计 2026 年 5 月派工数量和派工单数", "生产派工、设备维度", ["only_full_group_by", "GROUP BY"], None),
    SemanticEvalCase("case_12", "查询计划完工日期已过但完工状态未完成的生产任务", "生产任务、状态口径、逾期", ["完工状态", "逾期"], None),
    SemanticEvalCase("case_13", "统计近 90 天完工检验的不合格率，按品名排序取前 10", "质量指标、公式口径", ["不合格率", "返回行数不足"], 10),
    SemanticEvalCase("case_14", "查询设备点检中需要报修或点检结果异常的记录，按车间和设备统计", "设备点检、异常状态", ["报修", "异常状态"], None),
    SemanticEvalCase("case_15", "查询急单打样办单的完成情况，显示客户、办单单号、要求交期、完成状态", "打样流程、急单、交付风险", ["状态码翻译", "急单"], None),
]


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _compact_result(result: dict[str, Any], elapsed_ms: int) -> dict[str, Any]:
    diagnostics = result.get("diagnostics") or []
    return _jsonable(
        {
            "success": bool(result.get("success")),
            "status": "success" if result.get("success") else "error",
            "error": result.get("error"),
            "error_type": result.get("error_type"),
            "sql": result.get("sql"),
            "original_sql": result.get("original_sql"),
            "sql_repaired": bool(result.get("sql_repaired")),
            "repair_error": result.get("repair_error"),
            "row_count": result.get("row_count", 0),
            "columns": result.get("columns") or [],
            "result_preview": (result.get("data") or [])[:20],
            "referenced_tables": result.get("referenced_tables") or result.get("selected_tables") or [],
            "diagnostics": diagnostics,
            "result_text": str(result.get("result_text") or result.get("message") or "")[:6000],
            "execution_time_ms": elapsed_ms,
            "from_example": bool(result.get("from_example")),
            "from_semantic": bool(result.get("from_semantic")),
            "semantic_intent": result.get("semantic_intent") or result.get("intent") or {},
            "semantic_plan": result.get("semantic_plan") or result.get("plan") or {},
        }
    )


def _verdict(semantic: dict[str, Any], legacy: dict[str, Any]) -> str:
    return str(compare_execution_results(semantic, legacy)["verdict"])


def _contract_diagnostics(semantic: dict[str, Any], case: SemanticEvalCase) -> list[dict[str, Any]]:
    if not bool(semantic.get("success")):
        return []
    violations: list[str] = []
    intent = semantic.get("semantic_intent") or {}
    if case.expected_query_type and intent.get("query_type") != case.expected_query_type:
        violations.append(
            f"query_type expected={case.expected_query_type} actual={intent.get('query_type')}"
        )
    referenced = {str(item).lower() for item in semantic.get("referenced_tables") or []}
    for table in case.expected_tables:
        if table.lower() not in referenced:
            violations.append(f"missing_table={table}")
    sql = str(semantic.get("sql") or "")
    sql_lower = sql.lower()
    for fragment in case.required_sql_fragments:
        if fragment.lower() not in sql_lower:
            violations.append(f"missing_sql_fragment={fragment}")
    for fragment in case.forbidden_sql_fragments:
        if fragment.lower() in sql_lower:
            violations.append(f"forbidden_sql_fragment={fragment}")
    if not violations:
        return []
    return [{
        "type": "contract_violation",
        "severity": "error",
        "case_id": case.case_id,
        "violations": violations,
    }]


def _semantic_verdict(semantic: dict[str, Any]) -> str:
    """Evaluate the semantic chain on its own, independent from legacy XiYan."""

    if not bool(semantic.get("success")):
        return "fail"
    diagnostics = semantic.get("diagnostics") or []
    if any(item.get("type") == "contract_violation" for item in diagnostics if isinstance(item, dict)):
        return "fail"
    if diagnostics:
        return "pass_with_warnings"
    return "pass"


def _with_semantic_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    output = dict(payload)
    semantic_verdict = _semantic_verdict(output.get("semantic_result") or {})
    output["comparison_verdict"] = output.get("verdict")
    output["semantic_verdict"] = semantic_verdict
    output["verdict"] = semantic_verdict
    return output


class SemanticEvaluationService:
    def list_cases(self) -> list[dict[str, Any]]:
        return [case.model_dump() for case in EVALUATION_CASES]

    async def list_runs(self, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            rows = list(
                (
                    await session.execute(
                        select(SemanticEvaluationRunModel)
                        .where(SemanticEvaluationRunModel.workspace_id == workspace_id)
                        .order_by(SemanticEvaluationRunModel.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [
            _with_semantic_verdict(SemanticEvaluationRun.from_orm(row).model_dump(mode="json"))
            for row in rows
        ]

    async def run_cases(
        self,
        *,
        workspace_id: str,
        user_id: str,
        case_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        selected_ids = set(case_ids or [])
        cases = [
            case for case in EVALUATION_CASES
            if not selected_ids or case.case_id in selected_ids
        ]
        runs = []
        for case in cases:
            runs.append(await self._run_case(workspace_id=workspace_id, user_id=user_id, case=case))
        return {"runs": runs}

    async def _run_case(
        self,
        *,
        workspace_id: str,
        user_id: str,
        case: SemanticEvalCase,
    ) -> dict[str, Any]:
        from app.agents.sql_worker import get_sql_worker

        worker = get_sql_worker()
        semantic_result, semantic_elapsed = await self._run_worker(
            worker,
            case=case,
            user_id=user_id,
            workspace_id=workspace_id,
            run_mode="semantic_only",
        )
        legacy_result, legacy_elapsed = await self._run_worker(
            worker,
            case=case,
            user_id=user_id,
            workspace_id=workspace_id,
            run_mode="legacy_only",
        )
        semantic_payload = _compact_result(semantic_result, semantic_elapsed)
        semantic_payload["diagnostics"] = list(semantic_payload.get("diagnostics") or []) + _contract_diagnostics(
            semantic_payload,
            case,
        )
        legacy_payload = _compact_result(legacy_result, legacy_elapsed)
        comparison = compare_execution_results(semantic_payload, legacy_payload)
        diagnostics = _jsonable(
            (semantic_payload.get("diagnostics") or [])
            + (legacy_payload.get("diagnostics") or [])
            + [{"type": "result_comparison", "severity": "info", **comparison}]
        )
        verdict = str(comparison["verdict"])

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            model = SemanticEvaluationRunModel(
                workspace_id=workspace_id,
                user_id=user_id,
                case_id=case.case_id,
                question=case.question,
                test_dimension=case.test_dimension,
                expected_focus=case.expected_focus,
                expected_limit=case.expected_limit,
                semantic_result=semantic_payload,
                legacy_result=legacy_payload,
                verdict=verdict,
                diagnostics=diagnostics,
            )
            session.add(model)
            await session.flush()
            await session.refresh(model)
            return _with_semantic_verdict(SemanticEvaluationRun.from_orm(model).model_dump(mode="json"))

    async def _run_worker(
        self,
        worker: Any,
        *,
        case: SemanticEvalCase,
        user_id: str,
        workspace_id: str,
        run_mode: str,
    ) -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        try:
            result = await worker.execute_task(
                task_description=case.question,
                original_query=case.question,
                user_id=user_id,
                session_id=f"semantic-eval-{case.case_id}-{run_mode}",
                parent_step_id="semantic-evaluation",
                round_index=0,
                run_mode=run_mode,
                expected_limit=case.expected_limit,
            )
        except Exception as exc:  # noqa: BLE001
            result = {
                "success": False,
                "error": str(exc),
                "error_type": "evaluation_worker_error",
            }
        elapsed = int((time.perf_counter() - started) * 1000)
        return result, elapsed


_semantic_evaluation_service: Optional[SemanticEvaluationService] = None


def get_semantic_evaluation_service() -> SemanticEvaluationService:
    global _semantic_evaluation_service
    if _semantic_evaluation_service is None:
        _semantic_evaluation_service = SemanticEvaluationService()
    return _semantic_evaluation_service


