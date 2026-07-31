"""Auditable semantic access-evidence APIs."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.security.auth import User
from app.core.security.rbac_deps import CheckWorkspacePerm
from app.services.permission_evidence_service import (
    PermissionEvidenceError,
    get_permission_evidence_service,
)


router = APIRouter(prefix="/semantic/access-evidence")


class RelationPatch(BaseModel):
    expected_revision: int = Field(ge=0)
    access_level: Literal["hidden", "visible", "partial"] | None = None
    field_decision: Literal["visible", "hidden"] | None = None
    # Legacy v1 request fields accepted during the dual-protocol rollout.
    relation_role: Literal[
        "owner", "producer", "required_consumer", "conditional_consumer", "none",
    ] | None = None
    access_decision: Literal["inherit", "visible", "hidden"] | None = None
    row_scope: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=2000)
    review_status: Literal["accepted", "modified", "rejected"] = "modified"
    override_reason: str | None = Field(default=None, max_length=2000)


class ReviewDecision(BaseModel):
    kind: Literal["asset", "relation"]
    asset_id: int | None = None
    relation_id: int | None = None
    action: Literal["accepted", "modified", "rejected"]
    baseline_access: Literal["workspace_visible", "controlled"] | None = None
    requires_individual_review: bool | None = None
    # Legacy v1 request field accepted during the dual-protocol rollout.
    access_class: Literal["workspace_public", "department_scoped", "restricted"] | None = None
    reason: str | None = Field(default=None, max_length=2000)


class ReviewDecisionsRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    decisions: list[ReviewDecision] = Field(min_length=1, max_length=1000)


class ConfirmRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class GenerateRequest(BaseModel):
    datasource_id: int = Field(gt=0)


class TargetConfirmRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    mode: Literal["table", "ordinary_target", "target"]
    table_id: int | None = Field(default=None, gt=0)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionEvidenceError):
        detail = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            detail["details"] = exc.details
        return HTTPException(status_code=exc.status_code, detail=detail)
    return HTTPException(status_code=500, detail={
        "code": "permission_evidence_error", "message": "访问依据服务异常",
    })


@router.get("/readiness")
async def readiness(
    datasource_id: int,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().readiness(
            admin.workspace_id, datasource_id,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/sets/current")
async def current_set(
    datasource_id: int,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().current(
            admin.workspace_id, datasource_id,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/generate", status_code=202)
async def generate(
    request: GenerateRequest,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().generate(
            admin.workspace_id, request.datasource_id, str(admin.id),
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/runs/{run_id}")
async def get_run(
    run_id: int,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().get_run(
            admin.workspace_id, run_id,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/sets/{set_id}")
async def get_set(
    set_id: int,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().get_set(admin.workspace_id, set_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/sets/{set_id}/targets/{target_type}/{target_id}/confirm")
async def confirm_target(
    set_id: int,
    target_type: Literal["baseline", "org_unit"],
    target_id: str,
    request: TargetConfirmRequest,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().confirm_target(
            admin.workspace_id,
            set_id,
            str(admin.id),
            target_type,
            target_id,
            request.mode,
            request.expected_revision,
            request.table_id,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/sets/{set_id}/compile-preview")
async def compile_preview(
    set_id: int,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().compile_preview(
            admin.workspace_id, set_id,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/sets/{set_id}/relations/{relation_id}")
async def patch_relation(
    set_id: int,
    relation_id: int,
    request: RelationPatch,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        payload = request.model_dump(exclude={"expected_revision"}, exclude_none=True)
        return await get_permission_evidence_service().patch_relation(
            admin.workspace_id, set_id, relation_id, str(admin.id),
            payload, request.expected_revision,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/sets/{set_id}/review-decisions")
async def review_decisions(
    set_id: int,
    request: ReviewDecisionsRequest,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().review_decisions(
            admin.workspace_id, set_id, str(admin.id),
            [item.model_dump(exclude_none=True) for item in request.decisions],
            request.expected_revision,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/sets/{set_id}/confirm")
async def confirm_set(
    set_id: int,
    request: ConfirmRequest,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().confirm(
            admin.workspace_id, set_id, str(admin.id), request.expected_revision,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/sets/{set_id}/retry", status_code=202)
async def retry_set(
    set_id: int,
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().retry(
            admin.workspace_id, set_id, str(admin.id),
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/history")
async def history(
    datasource_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
):
    try:
        return await get_permission_evidence_service().history(
            admin.workspace_id, datasource_id, limit,
        )
    except Exception as exc:
        raise _error(exc) from exc
