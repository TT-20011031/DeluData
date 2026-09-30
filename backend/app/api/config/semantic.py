"""Semantic model governance APIs."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_current_admin
from app.core.security.auth import User
from app.core.security.rbac_deps import CheckPerm
from app.services.semantic_query_service import (
    SemanticQueryError,
    get_semantic_query_service,
)
from app.services.semantic_access_policy_service import (
    AccessPolicyError,
    get_semantic_access_policy_service,
)
from app.services.semantic_evaluation_service import (
    SemanticEvaluationRunRequest,
    get_semantic_evaluation_service,
)
from app.services.semantic_governance_service import get_semantic_governance_service
from app.services.semantic_auto_governance_service import get_semantic_auto_governance_service

logger = logging.getLogger(__name__)
router = APIRouter()


class SemanticModelsResponse(BaseModel):
    datasource: Optional[dict[str, Any]] = None
    tables: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    business_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    recent_runs: list[dict[str, Any]] = Field(default_factory=list)
    matching_diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class SemanticModelOverviewResponse(BaseModel):
    datasource: Optional[dict[str, Any]] = None
    counts: dict[str, int] = Field(default_factory=dict)


def _semantic_access_asset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose only currently enabled assets needed by the access workspace."""
    tables = [
        row for row in payload.get("tables") or []
        if row.get("status") == "confirmed"
        and bool(row.get("is_queryable"))
        and (row.get("sync_state") or "current") == "current"
    ]
    table_ids = {int(row["id"]) for row in tables}

    def enabled_child(row: dict[str, Any]) -> bool:
        return (
            int(row.get("table_id") or 0) in table_ids
            and row.get("status") == "confirmed"
            and bool(row.get("is_queryable"))
            and (row.get("sync_state") or "current") == "current"
        )

    return {
        "datasource": payload.get("datasource"),
        "tables": tables,
        "columns": [
            row for row in payload.get("columns") or [] if enabled_child(row)
        ],
        "metrics": [
            row for row in payload.get("metrics") or [] if enabled_child(row)
        ],
        "relationships": [],
        "business_suggestions": [],
        "recent_runs": [],
        "matching_diagnostics": [],
    }


class SemanticPatchRequest(BaseModel):
    business_name: Optional[str] = None
    description: Optional[str] = None
    synonyms: Optional[list[str]] = None
    status: Optional[str] = None
    is_queryable: Optional[bool] = None
    is_sensitive: Optional[bool] = None
    semantic_sql_enabled: Optional[bool] = None
    semantic_sql_fallback_enabled: Optional[bool] = None
    name: Optional[str] = None
    formula: Optional[str] = None
    aggregation: Optional[str] = None
    table_id: Optional[int] = None
    column_id: Optional[int] = None
    time_column_id: Optional[int] = None
    default_grain: Optional[str] = None
    left_table_id: Optional[int] = None
    right_table_id: Optional[int] = None
    left_column_id: Optional[int] = None
    right_column_id: Optional[int] = None
    relationship_type: Optional[str] = None
    confidence: Optional[float] = None
    sync_state: Optional[str] = None


class SemanticMetricCreateRequest(BaseModel):
    name: str
    business_name: Optional[str] = None
    description: Optional[str] = None
    formula: str
    aggregation: Optional[str] = None
    table_id: int
    column_id: Optional[int] = None
    time_column_id: Optional[int] = None
    default_grain: Optional[str] = None
    synonyms: list[str] = Field(default_factory=list)
    status: str = "suggested"
    is_queryable: bool = True
    is_sensitive: bool = False


class SemanticAccessRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticAccessSubjectRequest(SemanticAccessRequestModel):
    type: Literal["all", "role", "user"]
    id: Optional[str] = None
    label: Optional[str] = None


class SemanticAccessRowScopeRequest(SemanticAccessRequestModel):
    type: Literal["all", "self", "department", "custom"] = "all"
    column_id: Optional[int] = None
    identity: Optional[Literal["user_id", "username"]] = None
    include_descendants: bool = True
    condition: Optional[dict[str, Any]] = None


class SemanticAccessTableRuleRequest(SemanticAccessRequestModel):
    table_id: int
    decision: Optional[Literal["visible", "hidden"]] = None
    hidden_column_ids: list[int] = Field(default_factory=list)
    hidden_metric_ids: list[int] = Field(default_factory=list)
    row_scope: Optional[SemanticAccessRowScopeRequest] = None


class SemanticAccessPolicyDraftRequest(SemanticAccessRequestModel):
    datasource_id: Optional[int] = None
    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    source_text: Optional[str] = Field(default=None, max_length=8000)
    subject: SemanticAccessSubjectRequest
    tables: list[SemanticAccessTableRuleRequest] = Field(default_factory=list)


class SemanticAccessPolicyPatchRequest(SemanticAccessRequestModel):
    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    source_text: Optional[str] = Field(default=None, max_length=8000)
    subject: Optional[SemanticAccessSubjectRequest] = None
    tables: Optional[list[SemanticAccessTableRuleRequest]] = None


class SemanticAccessNaturalLanguageRequest(SemanticAccessRequestModel):
    datasource_id: Optional[int] = None
    source_text: str = Field(min_length=1, max_length=8000)


class SemanticAccessPolicyPreviewRequest(BaseModel):
    datasource_id: Optional[int] = None
    policy_id: Optional[int] = None
    user_id: str


class SemanticAssetTagsRequest(BaseModel):
    datasource_id: Optional[int] = None
    asset_type: Literal["table", "column", "metric"]
    asset_id: int
    tags: list[dict[str, str]] = Field(default_factory=list)


class SemanticRelationshipCreateRequest(BaseModel):
    left_table_id: int
    right_table_id: int
    left_column_id: int
    right_column_id: int
    relationship_type: str = "many_to_one"
    confidence: float = 0.8
    status: str = "suggested"
    is_queryable: bool = True
    description: Optional[str] = None


class SemanticPreviewRequest(BaseModel):
    question: str


class SemanticBusinessSuggestionGenerateRequest(BaseModel):
    scope: str = "datasource"
    table_id: Optional[int] = None
    force: bool = False
    use_llm: bool = True


class SemanticBusinessSuggestionPatchRequest(BaseModel):
    suggested_business_name: Optional[str] = None
    suggested_description: Optional[str] = None
    suggested_synonyms: Optional[list[str]] = None
    confidence: Optional[float] = None
    status: Optional[str] = None


class SemanticBusinessSuggestionBatchAcceptRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)


class SemanticQuestionReadinessRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class SemanticRuntimeModeRequest(BaseModel):
    runtime_mode: str


class SemanticGovernanceRunRequest(BaseModel):
    trigger_type: str = "manual"


class SemanticGovernanceCandidateAcceptRequest(BaseModel):
    edited_patch: Optional[dict[str, Any]] = None
    reason: Optional[str] = None


class SemanticGovernanceCandidateRejectRequest(BaseModel):
    category: str
    reason: str = Field(min_length=1, max_length=2000)


class SemanticGovernanceBatchRequest(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=100)
    action: str
    reason: str = ""
    category: str = "not_relevant"


class SemanticGovernancePolicyRequest(BaseModel):
    enabled: Optional[bool] = None
    observe_only: Optional[bool] = None
    exact_row_threshold: Optional[int] = Field(default=None, ge=1, le=10000000)
    sample_row_limit: Optional[int] = Field(default=None, ge=100, le=100000)
    table_timeout_sec: Optional[int] = Field(default=None, ge=1, le=60)
    max_tables_per_run: Optional[int] = Field(default=None, ge=1, le=200)
    max_run_seconds: Optional[int] = Field(default=None, ge=30, le=3600)
    review_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    high_confidence_threshold: Optional[float] = Field(default=None, ge=0, le=1)
    auto_apply_threshold: Optional[float] = Field(default=None, ge=0.98, le=1)
    min_auto_evidence_sources: Optional[int] = Field(default=None, ge=2, le=8)
    auto_action_types: Optional[list[str]] = None


def _semantic_error(exc: SemanticQueryError) -> HTTPException:
    status_code = (
        403
        if exc.error_type in {"permission_denied", "sensitive_object"}
        else 404 if exc.error_type == "not_found"
        else 409 if exc.error_type in {"scan_conflict", "scan_expired", "candidate_conflict"}
        else 400
    )
    return HTTPException(
        status_code=status_code,
        detail={
            "error_type": exc.error_type,
            "message": exc.message,
            "retryable": exc.retryable,
            "safe_to_fallback": exc.safe_to_fallback,
            "details": exc.details,
        },
    )


def _access_policy_error(exc: AccessPolicyError) -> HTTPException:
    status_code = (
        404
        if exc.code == "not_found"
        else 409 if exc.code in {"policy_blocked", "policy_conflict"}
        else 400
    )
    return HTTPException(
        status_code=status_code,
        detail={"error_type": exc.code, "message": exc.message, "details": exc.details},
    )


@router.post("/semantic/scan", summary="扫描数据库生成语义模型建议")
async def scan_semantic_models(admin: User = Depends(get_current_admin)):
    service = get_semantic_governance_service()
    try:
        preview = await service.preview_scan(admin.workspace_id, str(admin.id))
        return await service.apply_scan(admin.workspace_id, preview["scan_id"], str(admin.id))
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/scans/preview", summary="预览数据库 Schema 变化")
async def preview_semantic_scan(admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_governance_service().preview_scan(admin.workspace_id, str(admin.id))
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.get("/semantic/scans", summary="获取 Schema 扫描历史")
async def list_semantic_scans(limit: int = 20, admin: User = Depends(get_current_admin)):
    return {"scans": await get_semantic_governance_service().list_scans(admin.workspace_id, limit)}


@router.get("/semantic/scans/{scan_id}", summary="获取 Schema 扫描详情")
async def get_semantic_scan(scan_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_governance_service().get_scan(admin.workspace_id, scan_id)
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/scans/{scan_id}/apply", summary="原子应用 Schema 扫描")
async def apply_semantic_scan(scan_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_governance_service().apply_scan(admin.workspace_id, scan_id, str(admin.id))
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.get("/semantic/readiness", summary="获取可信问数就绪度")
async def get_semantic_readiness(admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_governance_service().get_readiness(admin.workspace_id, str(admin.id))
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/readiness/check-question", summary="检查单个问题的语义可回答性")
async def check_semantic_question(request: SemanticQuestionReadinessRequest, admin: User = Depends(get_current_admin)):
    return await get_semantic_governance_service().check_question(admin.workspace_id, str(admin.id), request.question)


@router.patch("/semantic/runtime-mode", summary="切换语义问数运行模式")
async def update_semantic_runtime_mode(request: SemanticRuntimeModeRequest, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_governance_service().set_runtime_mode(admin.workspace_id, str(admin.id), request.runtime_mode)
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.get("/semantic/models", response_model=SemanticModelsResponse, summary="获取语义模型")
async def get_semantic_models(admin: User = Depends(get_current_admin)):
    service = get_semantic_query_service()
    return await service.list_models(admin.workspace_id)


@router.get(
    "/semantic/overview",
    response_model=SemanticModelOverviewResponse,
    summary="获取语义模型轻量概览",
)
async def get_semantic_model_overview(admin: User = Depends(get_current_admin)):
    return await get_semantic_query_service().get_model_overview(admin.workspace_id)


@router.get(
    "/semantic/access-assets",
    response_model=SemanticModelsResponse,
    summary="获取问数权限可配置资产",
)
async def get_semantic_access_assets(
    admin: User = Depends(CheckPerm("semantic_access:view")),
):
    payload = await get_semantic_query_service().list_models(admin.workspace_id)
    return _semantic_access_asset_payload(payload)


@router.get("/semantic/access-policies", summary="获取语义访问策略")
async def list_semantic_access_policies(
    status: Optional[str] = Query(default=None),
    datasource_id: Optional[int] = Query(default=None),
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_access_policy_service().list_policies(
            admin.workspace_id,
            status=status,
            datasource_id=datasource_id,
        )
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.post("/semantic/access-policies/drafts", summary="生成语义访问策略草案")
async def create_semantic_access_policy_draft(
    request: SemanticAccessPolicyDraftRequest,
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_access_policy_service().create_draft(
            admin.workspace_id,
            str(admin.id),
            request.model_dump(exclude_none=True),
        )
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.post("/semantic/access-policies/parse-natural-language", summary="将自然语言解析为权限表单补丁")
async def parse_semantic_access_policy_natural_language(
    request: SemanticAccessNaturalLanguageRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_policy_service().parse_natural_language(
            admin.workspace_id,
            request.model_dump(exclude_none=True),
        )
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.post("/semantic/access-policies/effective-preview", summary="预览用户有效语义权限")
async def preview_effective_semantic_access(
    request: SemanticAccessPolicyPreviewRequest,
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_access_policy_service().effective_preview(
            admin.workspace_id,
            request.model_dump(exclude_none=True),
        )
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.get("/semantic/access-policies/{policy_id}", summary="获取语义访问策略详情")
async def get_semantic_access_policy(policy_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_access_policy_service().get_policy(admin.workspace_id, policy_id)
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.patch("/semantic/access-policies/{policy_id}", summary="更新语义访问策略草案")
async def update_semantic_access_policy(
    policy_id: int,
    request: SemanticAccessPolicyPatchRequest,
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_access_policy_service().update_policy(
            admin.workspace_id,
            policy_id,
            request.model_dump(exclude_unset=True),
        )
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.post("/semantic/access-policies/{policy_id}/compile", summary="编译并预览语义访问策略")
async def compile_semantic_access_policy(policy_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_access_policy_service().compile_policy(admin.workspace_id, policy_id)
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.post("/semantic/access-policies/{policy_id}/activate", summary="确认并启用语义访问策略")
async def activate_semantic_access_policy(policy_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_access_policy_service().activate_policy(
            admin.workspace_id,
            policy_id,
            str(admin.id),
        )
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.post("/semantic/access-policies/{policy_id}/disable", summary="停用语义访问策略")
async def disable_semantic_access_policy(policy_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_access_policy_service().disable_policy(admin.workspace_id, policy_id)
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.get("/semantic/asset-tags", summary="获取语义资产标签")
async def list_semantic_asset_tags(
    datasource_id: Optional[int] = Query(default=None),
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_access_policy_service().list_asset_tags(
            admin.workspace_id,
            datasource_id=datasource_id,
        )
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.put("/semantic/asset-tags", summary="替换语义资产标签")
async def replace_semantic_asset_tags(
    request: SemanticAssetTagsRequest,
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_access_policy_service().replace_asset_tags(
            admin.workspace_id,
            str(admin.id),
            request.model_dump(exclude_none=True),
        )
    except AccessPolicyError as exc:
        raise _access_policy_error(exc) from exc


@router.get("/semantic/row-permissions", summary="获取语义表行级权限规则")
async def get_semantic_row_permissions(admin: User = Depends(get_current_admin)):
    raise HTTPException(
        status_code=410,
        detail={
            "error_type": "semantic_access_policy_required",
            "message": "旧行级权限接口已停用，请使用语义访问策略中心。",
        },
    )


@router.post("/semantic/row-permissions", summary="创建语义表行级权限规则")
async def create_semantic_row_permission(
    admin: User = Depends(get_current_admin),
):
    raise HTTPException(status_code=410, detail={"error_type": "semantic_access_policy_required", "message": "请使用语义访问策略中心。"})


@router.patch("/semantic/row-permissions/{rule_id}", summary="更新语义表行级权限规则")
async def update_semantic_row_permission(
    rule_id: int,
    admin: User = Depends(get_current_admin),
):
    raise HTTPException(status_code=410, detail={"error_type": "semantic_access_policy_required", "message": "请使用语义访问策略中心。"})


@router.delete("/semantic/row-permissions/{rule_id}", summary="删除语义表行级权限规则")
async def delete_semantic_row_permission(rule_id: int, admin: User = Depends(get_current_admin)):
    raise HTTPException(status_code=410, detail={"error_type": "semantic_access_policy_required", "message": "请使用语义访问策略中心。"})


@router.patch("/semantic/tables/{table_id}/row-permission-mode", summary="更新语义表行级权限模式")
async def update_semantic_row_permission_mode(
    table_id: int,
    admin: User = Depends(get_current_admin),
):
    raise HTTPException(status_code=410, detail={"error_type": "semantic_access_policy_required", "message": "请使用语义访问策略中心。"})


@router.get("/semantic/row-permissions/effective", summary="预览用户有效行级权限")
async def get_effective_semantic_row_permission(
    admin: User = Depends(get_current_admin),
):
    raise HTTPException(status_code=410, detail={"error_type": "semantic_access_policy_required", "message": "请使用语义访问策略中心。"})


@router.get("/semantic/evaluation/cases", summary="List semantic evaluation cases")
async def get_semantic_evaluation_cases(admin: User = Depends(get_current_admin)):
    service = get_semantic_evaluation_service()
    return {"cases": service.list_cases()}


@router.get("/semantic/evaluation/runs", summary="List semantic evaluation runs")
async def get_semantic_evaluation_runs(admin: User = Depends(get_current_admin)):
    service = get_semantic_evaluation_service()
    return {"runs": await service.list_runs(admin.workspace_id)}


@router.post("/semantic/evaluation/run", summary="Run semantic evaluation")
async def run_semantic_evaluation(
    request: SemanticEvaluationRunRequest,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_evaluation_service()
    return await service.run_cases(
        workspace_id=admin.workspace_id,
        user_id=str(admin.id),
        case_ids=request.case_ids,
    )


# Compatibility endpoints for the legacy business-suggestion workflow.
# The governance candidate center is the primary administrator review surface.
@router.post("/semantic/business-suggestions/generate", summary="生成表字段业务语义建议")
async def generate_business_suggestions(
    request: SemanticBusinessSuggestionGenerateRequest,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_query_service()
    try:
        return await service.generate_business_suggestions(
            admin.workspace_id,
            scope=request.scope,
            table_id=request.table_id,
            force=request.force,
            use_llm=request.use_llm,
        )
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.get(
    "/semantic/business-suggestions/tables/{table_id}",
    summary="获取当前表待处理业务语义建议",
)
async def list_table_business_suggestions(
    table_id: int,
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_query_service().list_table_business_suggestions(
            admin.workspace_id,
            table_id,
        )
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/business-suggestions/tables/{table_id}/accept-all", summary="按表接受业务语义建议")
async def accept_table_business_suggestions(
    table_id: int,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_query_service()
    try:
        return await service.accept_table_business_suggestions(admin.workspace_id, table_id, str(admin.id))
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/metrics/suggestions/generate", summary="生成指标语义建议")
async def generate_metric_suggestions(admin: User = Depends(get_current_admin)):
    service = get_semantic_query_service()
    try:
        return await service.generate_metric_suggestions(admin.workspace_id)
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/relationships/suggestions/generate", summary="生成关系语义建议")
async def generate_relationship_suggestions(admin: User = Depends(get_current_admin)):
    service = get_semantic_query_service()
    try:
        return await service.generate_relationship_suggestions(admin.workspace_id)
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.patch("/semantic/business-suggestions/{suggestion_id}", summary="编辑业务语义建议")
async def update_business_suggestion(
    suggestion_id: int,
    request: SemanticBusinessSuggestionPatchRequest,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_query_service()
    try:
        return await service.update_business_suggestion(
            admin.workspace_id,
            suggestion_id,
            request.model_dump(exclude_unset=True),
        )
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/governance/runs", status_code=202, summary="启动证据治理运行")
async def create_semantic_governance_run(
    request: SemanticGovernanceRunRequest,
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_auto_governance_service().trigger_and_schedule(
            admin.workspace_id,
            str(admin.id),
            trigger_type=request.trigger_type,
        )
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.get("/semantic/governance/runs", summary="获取治理候选生成任务")
async def list_semantic_governance_runs(
    limit: int = Query(default=30, ge=1, le=100),
    admin: User = Depends(get_current_admin),
):
    return {"runs": await get_semantic_auto_governance_service().list_runs(admin.workspace_id, limit)}


@router.get("/semantic/governance/runs/{run_id}", summary="获取证据治理运行详情")
async def get_semantic_governance_run(run_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_auto_governance_service().get_run(admin.workspace_id, run_id)
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/governance/runs/{run_id}/cancel", summary="取消治理候选生成任务")
async def cancel_semantic_governance_run(run_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_auto_governance_service().cancel_run(admin.workspace_id, str(admin.id), run_id)
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/governance/runs/{run_id}/retry", summary="重试治理候选生成任务")
async def retry_semantic_governance_run(run_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_auto_governance_service().retry_run(admin.workspace_id, str(admin.id), run_id)
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.get("/semantic/governance/candidates", summary="获取治理候选")
async def list_semantic_governance_candidates(
    status: Optional[str] = None,
    target_type: Optional[str] = None,
    candidate_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    min_score: float = Query(default=0, ge=0, le=1),
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(get_current_admin),
):
    candidates = await get_semantic_auto_governance_service().list_candidates(
        admin.workspace_id,
        status=status,
        target_type=target_type,
        candidate_type=candidate_type,
        risk_level=risk_level,
        min_score=min_score,
        limit=limit,
    )
    return {"candidates": candidates}


@router.get("/semantic/governance/candidates/{candidate_id}", summary="获取治理候选详情")
async def get_semantic_governance_candidate(candidate_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_auto_governance_service().get_candidate(admin.workspace_id, candidate_id)
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/governance/candidates/{candidate_id}/accept", summary="接受治理候选")
async def accept_semantic_governance_candidate(
    candidate_id: int,
    request: SemanticGovernanceCandidateAcceptRequest,
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_auto_governance_service().accept_candidate(
            admin.workspace_id,
            str(admin.id),
            candidate_id,
            edited_patch=request.edited_patch,
            reason=request.reason,
        )
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/governance/candidates/{candidate_id}/reject", summary="拒绝治理候选")
async def reject_semantic_governance_candidate(
    candidate_id: int,
    request: SemanticGovernanceCandidateRejectRequest,
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_auto_governance_service().reject_candidate(
            admin.workspace_id,
            str(admin.id),
            candidate_id,
            request.reason,
            request.category,
        )
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/governance/candidates/{candidate_id}/rollback", summary="回滚自动治理变更")
async def rollback_semantic_governance_candidate(candidate_id: int, admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_auto_governance_service().rollback_candidate(admin.workspace_id, str(admin.id), candidate_id)
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/governance/candidates/batch", summary="批量处理治理候选")
async def batch_semantic_governance_candidates(
    request: SemanticGovernanceBatchRequest,
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_auto_governance_service().batch_decide(
            admin.workspace_id,
            str(admin.id),
            request.ids,
            request.action,
            reason=request.reason,
            category=request.category,
        )
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.get("/semantic/governance/evidence/{object_type}/{object_id}", summary="获取语义资产证据")
async def get_semantic_governance_evidence(
    object_type: str,
    object_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(get_current_admin),
):
    return {"evidence": await get_semantic_auto_governance_service().evidence_for_object(admin.workspace_id, object_type, object_id, limit)}


@router.get("/semantic/governance/policy", summary="获取自动治理策略")
async def get_semantic_governance_policy(admin: User = Depends(get_current_admin)):
    try:
        return await get_semantic_auto_governance_service().get_policy(admin.workspace_id)
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.patch("/semantic/governance/policy", summary="更新自动治理策略")
async def update_semantic_governance_policy(
    request: SemanticGovernancePolicyRequest,
    admin: User = Depends(get_current_admin),
):
    try:
        return await get_semantic_auto_governance_service().update_policy(
            admin.workspace_id,
            str(admin.id),
            request.model_dump(exclude_unset=True),
        )
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.patch("/semantic/{model_type}/{model_id}", summary="更新语义对象")
async def update_semantic_model(
    model_type: str,
    model_id: int,
    request: SemanticPatchRequest,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_query_service()
    try:
        payload = request.model_dump(exclude_unset=True)
        return await service.update_model(
            admin.workspace_id,
            model_type,
            model_id,
            payload,
            actor_id=str(admin.id),
        )
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc




@router.post("/semantic/business-suggestions/{suggestion_id}/accept", summary="接受业务语义建议")
async def accept_business_suggestion(
    suggestion_id: int,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_query_service()
    try:
        return await service.accept_business_suggestion(admin.workspace_id, suggestion_id, str(admin.id))
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/business-suggestions/batch-accept", summary="批量接受业务语义建议")
async def batch_accept_business_suggestions(
    request: SemanticBusinessSuggestionBatchAcceptRequest,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_query_service()
    try:
        return await service.batch_accept_business_suggestions(admin.workspace_id, request.ids, str(admin.id))
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/business-suggestions/{suggestion_id}/reject", summary="拒绝业务语义建议")
async def reject_business_suggestion(
    suggestion_id: int,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_query_service()
    try:
        return await service.reject_business_suggestion(admin.workspace_id, suggestion_id, str(admin.id))
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/metrics", summary="创建语义指标")
async def create_semantic_metric(
    request: SemanticMetricCreateRequest,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_query_service()
    try:
        return await service.create_metric(admin.workspace_id, request.model_dump())
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/relationships", summary="创建表关系")
async def create_semantic_relationship(
    request: SemanticRelationshipCreateRequest,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_query_service()
    try:
        return await service.create_relationship(admin.workspace_id, request.model_dump())
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


@router.post("/semantic/preview-query", summary="预览语义查询计划和 SQL")
async def preview_semantic_query(
    request: SemanticPreviewRequest,
    admin: User = Depends(get_current_admin),
):
    service = get_semantic_query_service()
    try:
        result = await service.execute_semantic_query(
            question=request.question,
            user_id=str(admin.id),
            workspace_id=admin.workspace_id,
            session_id="semantic-preview",
        )
        return result.model_dump(mode="json")
    except SemanticQueryError as exc:
        raise _semantic_error(exc) from exc


# ========== ERP authorization-bound semantic access API ==========

class SemanticBindingCreateRequest(BaseModel):
    datasource_id: int
    target_type: Literal["baseline", "org_unit", "position", "user"]
    target_id: str
    include_descendants: bool = False


class SemanticBindingSaveRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    definition: dict[str, Any]
    source_text: Optional[str] = None
    confirm_warnings: bool = False


class SemanticTargetPolicySaveRequest(SemanticBindingSaveRequest):
    datasource_id: int
    include_descendants: bool = False


class SemanticOwnershipMappingRequest(BaseModel):
    org_column_id: Optional[int] = None
    org_value_kind: Literal["id", "code", "external"] = "id"
    org_value_mapping: dict[str, Any] = Field(default_factory=dict)
    user_column_id: Optional[int] = None
    user_value_kind: Literal["id", "username"] = "id"


class SemanticBoundPreviewRequest(BaseModel):
    datasource_id: int
    user_id: str
    as_of: Optional[datetime] = None
    candidate_binding_id: Optional[int] = None
    candidate_definition: Optional[dict[str, Any]] = None
    candidate_target_type: Optional[Literal["baseline", "org_unit", "position", "user"]] = None
    candidate_target_id: Optional[str] = None
    candidate_include_descendants: Optional[bool] = None


class SemanticBindingRollbackRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class SemanticBindingSuggestionRequest(BaseModel):
    source_text: str = Field(min_length=1, max_length=8000)


class SemanticSimilarSuggestionPreviewRequest(BaseModel):
    datasource_id: int
    org_unit_id: int
    source_table_id: int
    source_before_rule: Optional[dict[str, Any]] = None
    expected_binding_revision: int = Field(ge=0)
    bootstrap_run_id: Optional[int] = None
    expected_bootstrap_revision: Optional[int] = Field(default=None, ge=0)


class SemanticSimilarSuggestionApplyRequest(SemanticSimilarSuggestionPreviewRequest):
    batch_fingerprint: str = Field(min_length=64, max_length=64)
    candidate_ids: list[str] = Field(min_length=1, max_length=50)


def _semantic_binding_http_error(exc: Exception) -> HTTPException:
    from app.services.semantic_policy_binding_service import SemanticBindingError

    if isinstance(exc, SemanticBindingError):
        detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            detail["details"] = exc.details
        return HTTPException(status_code=exc.status_code, detail=detail)
    return HTTPException(status_code=500, detail="semantic_binding_error")


@router.get("/semantic/access-matrix", summary="获取表与部门权限矩阵")
async def get_semantic_access_matrix(
    datasource_id: int,
    admin: User = Depends(CheckPerm("semantic_access:view")),
):
    from app.services.permission_evidence_service import (
        PermissionEvidenceError,
        get_permission_evidence_service,
    )

    try:
        return await get_permission_evidence_service().access_matrix(
            admin.workspace_id, datasource_id, str(admin.id),
        )
    except PermissionEvidenceError as exc:
        detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            detail["details"] = exc.details
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.get("/semantic/access-targets", summary="获取组织角色授权目标")
async def get_semantic_access_targets(
    datasource_id: int,
    target_type: Literal["baseline", "org_unit", "position", "user"] = "org_unit",
    org_unit_id: Optional[int] = None,
    search: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    unassigned: bool = False,
    admin: User = Depends(CheckPerm("semantic_access:view")),
):
    from app.services.semantic_policy_binding_service import list_organization_targets
    try:
        return await list_organization_targets(
            admin.workspace_id,
            datasource_id,
            str(admin.id),
            target_type,
            org_unit_id,
            search,
            page,
            page_size,
            unassigned,
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.post(
    "/semantic/access-policy-similar-suggestions/preview",
    summary="生成部门问数权限相似修改建议",
)
async def preview_semantic_access_similar_suggestions(
    request: SemanticSimilarSuggestionPreviewRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    from app.services.semantic_access_similar_suggestion_service import (
        get_semantic_access_similar_suggestion_service,
    )

    try:
        return await get_semantic_access_similar_suggestion_service().preview(
            admin.workspace_id,
            str(admin.id),
            request.model_dump(),
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.post(
    "/semantic/access-policy-similar-suggestions/apply",
    summary="原子应用部门问数权限相似修改建议",
)
async def apply_semantic_access_similar_suggestions(
    request: SemanticSimilarSuggestionApplyRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    from app.services.semantic_access_similar_suggestion_service import (
        get_semantic_access_similar_suggestion_service,
    )

    try:
        return await get_semantic_access_similar_suggestion_service().apply(
            admin.workspace_id,
            str(admin.id),
            request.model_dump(),
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.get(
    "/semantic/access-policies/{target_type}/{target_id}",
    summary="读取授权对象的直接问数策略",
)
async def get_semantic_target_policy(
    target_type: Literal["baseline", "org_unit", "position", "user"],
    target_id: str,
    datasource_id: int,
    admin: User = Depends(CheckPerm("semantic_access:view")),
):
    from app.services.semantic_policy_binding_service import get_target_policy

    try:
        return await get_target_policy(
            admin.workspace_id,
            datasource_id,
            target_type,
            target_id,
            str(admin.id),
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.put(
    "/semantic/access-policies/{target_type}/{target_id}",
    summary="原子保存并生效授权对象的问数策略",
)
async def put_semantic_target_policy(
    target_type: Literal["baseline", "org_unit", "position", "user"],
    target_id: str,
    request: SemanticTargetPolicySaveRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    from app.services.semantic_policy_binding_service import save_target_policy

    try:
        return await save_target_policy(
            admin.workspace_id,
            request.datasource_id,
            target_type,
            target_id,
            request.expected_revision,
            request.definition,
            str(admin.id),
            request.include_descendants,
            request.source_text,
            request.confirm_warnings,
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.post("/semantic/access-bindings", summary="创建或获取授权策略绑定")
async def create_semantic_access_binding(
    request: SemanticBindingCreateRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    from app.services.semantic_policy_binding_service import ensure_binding
    try:
        return await ensure_binding(
            admin.workspace_id, request.datasource_id, request.target_type,
            request.target_id, str(admin.id), request.include_descendants,
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.get("/semantic/access-bindings/{binding_id}", summary="获取授权策略绑定")
async def get_semantic_access_binding(
    binding_id: int,
    admin: User = Depends(CheckPerm("semantic_access:view")),
):
    from app.services.semantic_policy_binding_service import get_binding
    try:
        return await get_binding(admin.workspace_id, binding_id, str(admin.id))
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.put("/semantic/access-bindings/{binding_id}", summary="校验并立即发布授权策略版本")
async def save_semantic_access_binding(
    binding_id: int,
    request: SemanticBindingSaveRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    from app.services.semantic_policy_binding_service import save_and_activate
    try:
        return await save_and_activate(
            admin.workspace_id, binding_id, request.expected_revision,
            request.definition, str(admin.id), request.source_text,
            request.confirm_warnings,
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.post("/semantic/access-bindings/{binding_id}/suggestion", summary="生成并校验当前授权绑定的表单建议")
async def suggest_semantic_access_binding_patch(
    binding_id: int,
    request: SemanticBindingSuggestionRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    from app.services.semantic_policy_binding_service import suggest_binding_patch
    try:
        return await suggest_binding_patch(
            admin.workspace_id, binding_id, request.source_text, str(admin.id),
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.get("/semantic/access-bindings/{binding_id}/versions", summary="获取授权策略版本历史")
async def get_semantic_access_binding_versions(
    binding_id: int,
    admin: User = Depends(CheckPerm("semantic_access:view")),
):
    from app.services.semantic_policy_binding_service import list_versions
    try:
        return await list_versions(admin.workspace_id, binding_id, str(admin.id))
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.post("/semantic/access-bindings/{binding_id}/versions/{version_id}/rollback", summary="复制历史版本并立即激活")
async def rollback_semantic_access_binding_version(
    binding_id: int,
    version_id: int,
    request: SemanticBindingRollbackRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    from app.services.semantic_policy_binding_service import rollback_version
    try:
        return await rollback_version(
            admin.workspace_id,
            binding_id,
            version_id,
            request.expected_revision,
            str(admin.id),
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.get("/semantic/ownership-mappings", summary="获取语义表数据归属映射")
async def list_semantic_ownership_mappings(
    datasource_id: int,
    admin: User = Depends(CheckPerm("semantic_access:view")),
):
    from app.services.semantic_policy_binding_service import get_ownership_mappings
    try:
        return await get_ownership_mappings(admin.workspace_id, datasource_id)
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.put("/semantic/ownership-mappings/{table_id}", summary="保存语义表数据归属映射")
async def put_semantic_ownership_mapping(
    table_id: int,
    datasource_id: int,
    request: SemanticOwnershipMappingRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    from app.services.semantic_policy_binding_service import save_ownership_mapping
    try:
        return await save_ownership_mapping(
            admin.workspace_id, datasource_id, table_id,
            request.model_dump(), str(admin.id),
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc


@router.post("/semantic/access-preview", summary="预览组织任职下的最终问数权限")
async def preview_bound_semantic_access(
    request: SemanticBoundPreviewRequest,
    admin: User = Depends(CheckPerm("semantic_access:view")),
):
    from app.services.semantic_policy_binding_service import preview_bound_access

    try:
        return await preview_bound_access(
            admin.workspace_id,
            request.datasource_id,
            request.user_id,
            str(admin.id),
            request.as_of,
            request.candidate_binding_id,
            request.candidate_definition,
            request.candidate_target_type,
            request.candidate_target_id,
            request.candidate_include_descendants,
        )
    except Exception as exc:
        raise _semantic_binding_http_error(exc) from exc
