"""Workspace business context and department semantic-profile APIs."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security.auth import User
from app.core.security.rbac_deps import CheckWorkspacePerm
from app.services.organization_semantic_service import (
    OrganizationSemanticError,
    get_organization_semantic_service,
)


router = APIRouter(prefix="/authorization")


class BusinessContextWrite(BaseModel):
    content: str = Field(default="", max_length=8000)
    industry: str = Field(default="", max_length=200)
    core_offerings: list[str] = Field(default_factory=list, max_length=50)
    business_objects: list[str] = Field(default_factory=list, max_length=50)
    business_processes: list[str] = Field(default_factory=list, max_length=50)
    customer_types: list[str] = Field(default_factory=list, max_length=50)
    operating_regions: list[str] = Field(default_factory=list, max_length=50)
    special_terms: list[str] = Field(default_factory=list, max_length=50)
    data_governance_constraints: list[str] = Field(default_factory=list, max_length=50)
    expected_revision: int | None = Field(default=None, ge=0)


class ProfileDraftWrite(BaseModel):
    content: dict[str, Any]
    expected_revision: int = Field(ge=0)


class ProfileConfirmWrite(BaseModel):
    expected_revision: int = Field(ge=0)
    # Retained for one compatibility cycle; confirmation no longer depends on
    # users reviewing internal AI inference metadata.
    acknowledged_unknowns: bool = False


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, OrganizationSemanticError):
        return HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        )
    return HTTPException(status_code=500, detail={
        "code": "organization_semantic_error", "message": "部门画像服务异常",
    })


def _both_permissions(
    org_admin: User = Depends(CheckWorkspacePerm("org:manage")),
    semantic_admin: User = Depends(CheckWorkspacePerm("semantic_access:manage")),
) -> User:
    if org_admin.id != semantic_admin.id:
        raise HTTPException(status_code=403, detail="permission context mismatch")
    return org_admin


@router.get("/business-context")
async def get_business_context(
    admin: User = Depends(_both_permissions),
):
    return await get_organization_semantic_service().get_business_context(admin.workspace_id)


@router.put("/business-context")
async def put_business_context(
    request: BusinessContextWrite,
    admin: User = Depends(_both_permissions),
):
    try:
        return await get_organization_semantic_service().save_business_context(
            admin.workspace_id,
            str(admin.id),
            request.model_dump(exclude={"expected_revision"}),
            request.expected_revision,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/org-semantic-profiles")
async def list_org_semantic_profiles(
    admin: User = Depends(_both_permissions),
):
    return await get_organization_semantic_service().list_profiles(admin.workspace_id)


@router.patch("/org-semantic-profiles/{org_id}/draft")
async def patch_org_semantic_profile_draft(
    org_id: int,
    request: ProfileDraftWrite,
    admin: User = Depends(_both_permissions),
):
    try:
        return await get_organization_semantic_service().patch_draft(
            admin.workspace_id, org_id, str(admin.id), request.content, request.expected_revision,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/org-semantic-profiles/{org_id}/confirm")
async def confirm_org_semantic_profile(
    org_id: int,
    request: ProfileConfirmWrite,
    admin: User = Depends(_both_permissions),
):
    try:
        return await get_organization_semantic_service().confirm(
            admin.workspace_id,
            org_id,
            str(admin.id),
            request.expected_revision,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/org-semantic-profiles/{org_id}/retry", status_code=202)
async def retry_org_semantic_profile(
    org_id: int,
    admin: User = Depends(_both_permissions),
):
    try:
        return await get_organization_semantic_service().retry(
            admin.workspace_id, org_id, str(admin.id),
        )
    except Exception as exc:
        raise _error(exc) from exc
