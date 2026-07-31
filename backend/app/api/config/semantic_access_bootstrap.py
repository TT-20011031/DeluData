"""AI-assisted first-time semantic access configuration API."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.security.auth import User
from app.core.security.rbac_deps import CheckPerm
from app.services.semantic_access_bootstrap_service import (
    SemanticAccessBootstrapError,
    get_semantic_access_bootstrap_service,
)
from app.services.permission_evidence_service import PermissionEvidenceError


router = APIRouter(prefix="/semantic/access-bootstrap")


class BootstrapCreateRequest(BaseModel):
    datasource_id: int
    evidence_set_id: Optional[int] = Field(default=None, gt=0)
    evidence_revision: Optional[int] = Field(default=None, ge=0)
    # One-release compatibility only. It is not sent to, or used by, permission compilation.
    business_context: Optional[str] = Field(default=None, max_length=8000)


class BootstrapRevisionRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class BootstrapTargetUpdateRequest(BootstrapRevisionRequest):
    included: bool = True
    definition: dict[str, Any]


class BootstrapTargetDraftUpdateRequest(BootstrapRevisionRequest):
    definition: dict[str, Any]
    include_descendants: bool = False
    reason: Optional[str] = Field(default=None, max_length=2000)


class BootstrapMappingUpdateRequest(BootstrapRevisionRequest):
    accepted: bool
    proposed_mapping: dict[str, Any]


class BootstrapReviewDecision(BaseModel):
    target_suggestion_id: int = Field(gt=0)
    table_id: int = Field(gt=0)
    action: Literal["accept", "reject", "reset"]


class BootstrapReviewDecisionsRequest(BootstrapRevisionRequest):
    decisions: list[BootstrapReviewDecision] = Field(min_length=1, max_length=1000)


class BootstrapPreviewRequest(BaseModel):
    user_ids: list[str] = Field(default_factory=list, max_length=50)


class BootstrapApplyRequest(BootstrapRevisionRequest):
    confirm_warnings: bool = False


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SemanticAccessBootstrapError):
        detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            detail["details"] = exc.details
        return HTTPException(status_code=exc.status_code, detail=detail)
    if isinstance(exc, PermissionEvidenceError):
        detail = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            detail["details"] = exc.details
        return HTTPException(status_code=exc.status_code, detail=detail)
    return HTTPException(status_code=500, detail={
        "code": "access_bootstrap_error",
        "message": "AI 首次配置服务异常",
    })


@router.get("/readiness", summary="检查 AI 问数权限首次配置就绪度")
async def get_readiness(
    datasource_id: int,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().readiness(
            admin.workspace_id, datasource_id, str(admin.id),
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/runs", summary="获取 AI 问数权限首次配置历史")
async def list_runs(
    datasource_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().list_runs(
            admin.workspace_id, datasource_id, str(admin.id), limit,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/runs", status_code=202, summary="启动 AI 问数权限首次配置")
async def create_run(
    request: BootstrapCreateRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        if request.evidence_set_id is not None:
            if request.evidence_revision is None:
                raise SemanticAccessBootstrapError(
                    "依据集修订号不能为空", code="evidence_revision_required",
                )
            from app.services.permission_evidence_service import get_permission_evidence_service
            evidence = await get_permission_evidence_service().confirm(
                admin.workspace_id,
                request.evidence_set_id,
                str(admin.id),
                request.evidence_revision,
            )
            return {"status": "applied", "source_type": "evidence_bootstrap", "evidence": evidence}
        return await get_semantic_access_bootstrap_service().create_run(
            admin.workspace_id,
            request.datasource_id,
            str(admin.id),
            request.business_context,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/runs/{run_id}", summary="获取 AI 问数权限首次配置运行")
async def get_run(
    run_id: int,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().get_run(
            admin.workspace_id, run_id, str(admin.id),
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/cancel", summary="取消 AI 问数权限首次配置运行")
async def cancel_run(
    run_id: int,
    request: BootstrapRevisionRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().cancel_run(
            admin.workspace_id, run_id, str(admin.id), request.expected_revision,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/retry", status_code=202, summary="重试 AI 问数权限首次配置运行")
async def retry_run(
    run_id: int,
    request: BootstrapRevisionRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().retry_run(
            admin.workspace_id, run_id, str(admin.id), request.expected_revision,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/runs/{run_id}/suggestions", summary="获取 AI 首次配置审核方案")
async def get_suggestions(
    run_id: int,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().get_suggestions(
            admin.workspace_id, run_id, str(admin.id),
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post(
    "/evidence-sets/{evidence_set_id}/materialize",
    summary="将访问依据投影为可编辑的首次权限草稿",
)
async def materialize_evidence_draft(
    evidence_set_id: int,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().materialize_evidence_draft(
            admin.workspace_id,
            evidence_set_id,
            str(admin.id),
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get(
    "/runs/{run_id}/targets/{target_type}/{target_id}",
    summary="读取指定授权对象的首次权限草稿",
)
async def get_target_draft(
    run_id: int,
    target_type: Literal["baseline", "org_unit", "position", "user"],
    target_id: str,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().get_target_draft(
            admin.workspace_id,
            run_id,
            target_type,
            target_id,
            str(admin.id),
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.put(
    "/runs/{run_id}/targets/{target_type}/{target_id}",
    summary="保存指定授权对象的首次权限草稿",
)
async def put_target_draft(
    run_id: int,
    target_type: Literal["baseline", "org_unit", "position", "user"],
    target_id: str,
    request: BootstrapTargetDraftUpdateRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().upsert_target_draft(
            admin.workspace_id,
            run_id,
            target_type,
            target_id,
            str(admin.id),
            request.expected_revision,
            definition=request.definition,
            include_descendants=request.include_descendants,
            reason=request.reason,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/runs/{run_id}/targets/{suggestion_id}", summary="更新 AI 目标策略草案")
async def update_target(
    run_id: int,
    suggestion_id: int,
    request: BootstrapTargetUpdateRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().update_target(
            admin.workspace_id,
            run_id,
            suggestion_id,
            str(admin.id),
            request.expected_revision,
            included=request.included,
            definition=request.definition,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/runs/{run_id}/ownership-mappings/{suggestion_id}", summary="审核 AI 归属映射建议")
async def update_mapping(
    run_id: int,
    suggestion_id: int,
    request: BootstrapMappingUpdateRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().update_mapping(
            admin.workspace_id,
            run_id,
            suggestion_id,
            str(admin.id),
            request.expected_revision,
            accepted=request.accepted,
            proposed_mapping=request.proposed_mapping,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/runs/{run_id}/review-decisions", summary="批量接受或拒绝 AI 表权限建议")
async def review_decisions(
    run_id: int,
    request: BootstrapReviewDecisionsRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().review_decisions(
            admin.workspace_id,
            run_id,
            str(admin.id),
            request.expected_revision,
            [row.model_dump() for row in request.decisions],
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/preview", summary="预览 AI 首配方案下的最终权限")
async def preview_run(
    run_id: int,
    request: BootstrapPreviewRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().preview_run(
            admin.workspace_id,
            run_id,
            str(admin.id),
            request.user_ids or None,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/runs/{run_id}/apply", summary="原子发布 AI 问数权限首次配置")
async def apply_run(
    run_id: int,
    request: BootstrapApplyRequest,
    admin: User = Depends(CheckPerm("semantic_access:manage")),
):
    try:
        return await get_semantic_access_bootstrap_service().apply_run(
            admin.workspace_id,
            run_id,
            str(admin.id),
            request.expected_revision,
            request.confirm_warnings,
        )
    except Exception as exc:
        raise _http_error(exc) from exc
