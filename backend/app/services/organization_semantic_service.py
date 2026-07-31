"""Business context and versioned top-level organization semantic profiles."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.core.llm.async_llm import get_async_llm
from app.models.auth.authorization import PositionModel, RoleBindingModel
from app.models.auth.organization import DepartmentModel
from app.models.auth.organization_semantic import (
    OrganizationSemanticProfileModel,
    OrganizationSemanticProfileVersionModel,
    WorkspaceBusinessContextModel,
)
from app.models.auth.rbac import RoleModel
from app.models.config.permission_evidence import SemanticPermissionEvidenceRunModel


PROFILE_CORE_KEYS = (
    "positioning", "responsibilities", "business_objects", "data_produced",
    "data_consumed", "collaborations", "boundaries", "keywords",
)
PROFILE_INSIGHT_KEYS = (
    "known_facts", "assumptions", "missing_information", "permission_impacts",
)
PROFILE_KEYS = PROFILE_CORE_KEYS + PROFILE_INSIGHT_KEYS

BUSINESS_CONTEXT_LIST_FIELDS = (
    "core_offerings", "business_objects", "business_processes", "customer_types",
    "operating_regions", "special_terms", "data_governance_constraints",
)
BUSINESS_CONTEXT_DB_FIELDS = {
    "core_offerings": "core_offerings_json",
    "business_objects": "business_objects_json",
    "business_processes": "business_processes_json",
    "customer_types": "customer_types_json",
    "operating_regions": "operating_regions_json",
    "special_terms": "special_terms_json",
    "data_governance_constraints": "data_governance_constraints_json",
}
RECOMMENDED_BUSINESS_CONTEXT_FIELDS = (
    "industry", "core_offerings", "business_objects",
)
BUSINESS_CONTEXT_FIELD_LABELS = {
    "industry": "所属行业",
    "core_offerings": "核心产品或服务",
    "business_objects": "核心业务对象",
}


class OrganizationSemanticError(Exception):
    def __init__(self, message: str, *, code: str = "organization_semantic_error", status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def normalize_business_context(value: dict[str, Any]) -> dict[str, Any]:
    content = str(value.get("content") or "").strip()
    industry = str(value.get("industry") or "").strip()
    if len(content) > 8000:
        raise OrganizationSemanticError(
            "企业业务背景补充说明不能超过 8000 字",
            code="business_context_too_long",
        )
    if len(industry) > 200:
        raise OrganizationSemanticError("所属行业不能超过 200 字", code="business_context_too_long")
    normalized: dict[str, Any] = {"content": content, "industry": industry}
    for field in BUSINESS_CONTEXT_LIST_FIELDS:
        raw = value.get(field) or []
        if not isinstance(raw, list):
            raise OrganizationSemanticError(
                f"{field} 必须是文本列表",
                code="invalid_business_context",
            )
        items: list[str] = []
        seen: set[str] = set()
        for item in raw:
            text_value = str(item or "").strip()
            if not text_value or text_value in seen:
                continue
            if len(text_value) > 300:
                raise OrganizationSemanticError(
                    "结构化业务事实的单项内容不能超过 300 字",
                    code="business_context_too_long",
                )
            seen.add(text_value)
            items.append(text_value)
        if len(items) > 50:
            raise OrganizationSemanticError(
                "每类结构化业务事实不能超过 50 项",
                code="business_context_too_many_items",
            )
        normalized[field] = items
    return normalized


def business_context_quality(value: dict[str, Any]) -> dict[str, Any]:
    completed = [
        field for field in RECOMMENDED_BUSINESS_CONTEXT_FIELDS
        if value.get(field)
    ]
    missing = [
        field for field in RECOMMENDED_BUSINESS_CONTEXT_FIELDS
        if field not in completed
    ]
    has_any = bool(
        value.get("content")
        or value.get("industry")
        or any(value.get(field) for field in BUSINESS_CONTEXT_LIST_FIELDS)
    )
    level = (
        "sufficient" if not missing
        else "partial" if has_any
        else "missing"
    )
    return {
        "level": level,
        "has_any": has_any,
        "required_completed": len(completed),
        "required_total": len(RECOMMENDED_BUSINESS_CONTEXT_FIELDS),
        "completeness_percent": round(
            len(completed) / len(RECOMMENDED_BUSINESS_CONTEXT_FIELDS) * 100
        ),
        "missing_fields": missing,
        "missing_field_labels": [BUSINESS_CONTEXT_FIELD_LABELS[field] for field in missing],
        "uses_general_knowledge": level != "sufficient",
    }


def business_context_snapshot(row) -> dict[str, Any]:
    value = {
        "content": (row.content or "") if row else "",
        "industry": (row.industry or "") if row else "",
        **{
            field: list(getattr(row, db_field) or []) if row else []
            for field, db_field in BUSINESS_CONTEXT_DB_FIELDS.items()
        },
    }
    value["quality"] = business_context_quality(value)
    return value


def normalize_profile_insight(value: Any) -> list[str]:
    """Safely flatten an LLM's prose/string/object insight into reviewable text."""
    if value in (None, "", [], {}):
        return []
    values = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    for item in values:
        if isinstance(item, str):
            text_value = item.strip()
        elif isinstance(item, dict):
            parts = []
            for key, nested in item.items():
                if isinstance(nested, (dict, list)):
                    rendered = json.dumps(nested, ensure_ascii=False, sort_keys=True)
                else:
                    rendered = str(nested)
                parts.append(f"{key}：{rendered}")
            text_value = "；".join(parts)
        else:
            text_value = str(item).strip()
        if text_value:
            normalized.append(text_value)
    return normalized


def restrict_profile_collaborations(
    content: dict[str, Any],
    allowed_department_names: set[str] | list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Keep collaboration entries that reference a real peer top department."""
    restricted = dict(content or {})
    raw_items = restricted.get("collaborations")
    if not isinstance(raw_items, list):
        return restricted
    allowed = sorted(
        {str(name).strip() for name in allowed_department_names if str(name).strip()},
        key=len,
        reverse=True,
    )
    kept: list[str] = []
    seen: set[str] = set()
    separators = ("（", "(", "：", ":", "—", "-", " ")
    for raw_item in raw_items:
        item = str(raw_item or "").strip()
        if not item:
            continue
        matches_real_department = any(
            item == name or any(item.startswith(f"{name}{separator}") for separator in separators)
            for name in allowed
        )
        if matches_real_department and item not in seen:
            kept.append(item)
            seen.add(item)
    restricted["collaborations"] = kept
    return restricted


def stable_fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class OrganizationSemanticService:
    def __init__(self) -> None:
        self.llm = get_async_llm()
        self.settings = get_settings()

    async def get_business_context(self, workspace_id: str) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.get_session() as session:
            row = (await session.execute(select(WorkspaceBusinessContextModel).where(
                WorkspaceBusinessContextModel.workspace_id == workspace_id,
            ))).scalar_one_or_none()
            return self._context_payload(row)

    async def save_business_context(
        self, workspace_id: str, actor_id: str, context: dict[str, Any],
        expected_revision: int | None,
    ) -> dict[str, Any]:
        normalized = normalize_business_context(context)
        db = get_async_db_manager()
        async with db.session_scope() as session:
            row = (await session.execute(select(WorkspaceBusinessContextModel).where(
                WorkspaceBusinessContextModel.workspace_id == workspace_id,
            ).with_for_update())).scalar_one_or_none()
            row_values = {
                "content": normalized["content"],
                "industry": normalized["industry"] or None,
                **{
                    db_field: normalized[field]
                    for field, db_field in BUSINESS_CONTEXT_DB_FIELDS.items()
                },
            }
            if row is None:
                if expected_revision not in (None, 0):
                    raise OrganizationSemanticError("企业背景已发生变化", code="revision_conflict", status_code=409)
                row = WorkspaceBusinessContextModel(
                    workspace_id=workspace_id,
                    revision=1,
                    updated_by=actor_id,
                    **row_values,
                )
                session.add(row)
                changed = True
            else:
                if expected_revision is not None and row.revision != expected_revision:
                    raise OrganizationSemanticError("企业背景已发生变化", code="revision_conflict", status_code=409)
                changed = any(
                    (getattr(row, field) or ([] if field.endswith("_json") else ""))
                    != (value or ([] if field.endswith("_json") else ""))
                    for field, value in row_values.items()
                )
                if changed:
                    for field, value in row_values.items():
                        setattr(row, field, value)
                    row.revision += 1
                    row.updated_by = actor_id
            await session.flush()
            if not changed:
                return self._context_payload(row)
            departments = list((await session.execute(select(DepartmentModel).where(
                DepartmentModel.workspace_id == workspace_id,
                DepartmentModel.parent_id.is_(None),
                DepartmentModel.status == True,  # noqa: E712
            ))).scalars())
            for department in departments:
                await self._enqueue_profile_in_session(
                    session, workspace_id, int(department.id), actor_id, force=True,
                )
            from app.models.config.permission_evidence import SemanticAccessEvidenceSetModel
            evidence_sets = list((await session.execute(select(SemanticAccessEvidenceSetModel).where(
                SemanticAccessEvidenceSetModel.workspace_id == workspace_id,
                SemanticAccessEvidenceSetModel.status == "published",
            ))).scalars())
            for evidence_set in evidence_sets:
                evidence_set.status = "stale"
            return self._context_payload(row)

    async def list_profiles(self, workspace_id: str) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.get_session() as session:
            departments = list((await session.execute(select(DepartmentModel).where(
                DepartmentModel.workspace_id == workspace_id,
                DepartmentModel.parent_id.is_(None),
                DepartmentModel.status == True,  # noqa: E712
            ).order_by(DepartmentModel.order_num, DepartmentModel.id))).scalars())
            bindings = {
                int(row.org_unit_id): row for row in (await session.execute(
                    select(OrganizationSemanticProfileModel).where(
                        OrganizationSemanticProfileModel.workspace_id == workspace_id,
                    )
                )).scalars()
            }
            version_ids = {
                value for row in bindings.values()
                for value in (row.active_version_id, row.draft_version_id) if value
            }
            versions = {
                int(row.id): row for row in (await session.execute(
                    select(OrganizationSemanticProfileVersionModel).where(
                        OrganizationSemanticProfileVersionModel.id.in_(version_ids)
                    )
                )).scalars()
            } if version_ids else {}
            department_names = {department.name for department in departments}
            return {
                "items": [
                    self._profile_payload(
                        department, bindings.get(int(department.id)), versions,
                        allowed_collaboration_names=department_names - {department.name},
                    ) for department in departments
                ]
            }

    async def patch_draft(
        self, workspace_id: str, org_unit_id: int, actor_id: str,
        content: dict[str, Any], expected_revision: int,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            binding, department = await self._binding_for_update(session, workspace_id, org_unit_id)
            if binding.revision != expected_revision:
                raise OrganizationSemanticError("部门画像已发生变化", code="revision_conflict", status_code=409)
            peers = await self._peer_top_departments(session, workspace_id, org_unit_id)
            peer_names = {row.name for row in peers}
            normalized = self._validate_profile_content(
                content,
                allowed_collaboration_names=peer_names,
            )
            version = await session.get(OrganizationSemanticProfileVersionModel, binding.draft_version_id)
            if not version or version.status != "draft":
                active = await session.get(
                    OrganizationSemanticProfileVersionModel,
                    binding.active_version_id,
                )
                if not active or active.status != "confirmed":
                    raise OrganizationSemanticError(
                        "当前没有可编辑画像", code="profile_not_editable", status_code=404,
                    )
                version_number = int((await session.execute(select(func.max(
                    OrganizationSemanticProfileVersionModel.version
                )).where(
                    OrganizationSemanticProfileVersionModel.workspace_id == workspace_id,
                    OrganizationSemanticProfileVersionModel.org_unit_id == org_unit_id,
                ))).scalar() or 0) + 1
                version = OrganizationSemanticProfileVersionModel(
                    workspace_id=workspace_id,
                    org_unit_id=org_unit_id,
                    version=version_number,
                    status="draft",
                    content_json=normalized,
                    evidence_json=dict(active.evidence_json or {}),
                    confidence=active.confidence,
                    input_fingerprint=active.input_fingerprint,
                    business_context_revision=active.business_context_revision,
                    source="human_edited",
                    created_by=actor_id,
                )
                session.add(version)
                await session.flush()
                binding.draft_version_id = version.id
            version.content_json = normalized
            evidence = dict(version.evidence_json or {})
            evidence["assumption_count"] = len(normalized["assumptions"])
            evidence["missing_information_count"] = len(normalized["missing_information"])
            version.evidence_json = evidence
            version.source = "human_edited"
            version.created_by = actor_id
            binding.revision += 1
            binding.status = "draft"
            await session.flush()
            active_version = await session.get(
                OrganizationSemanticProfileVersionModel,
                binding.active_version_id,
            )
            versions = {int(version.id): version}
            if active_version:
                versions[int(active_version.id)] = active_version
            return self._profile_payload(
                department,
                binding,
                versions,
                allowed_collaboration_names=peer_names,
            )

    async def confirm(
        self, workspace_id: str, org_unit_id: int, actor_id: str, expected_revision: int,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            binding, department = await self._binding_for_update(session, workspace_id, org_unit_id)
            if binding.revision != expected_revision:
                raise OrganizationSemanticError("部门画像已发生变化", code="revision_conflict", status_code=409)
            version = await session.get(OrganizationSemanticProfileVersionModel, binding.draft_version_id)
            if not version:
                raise OrganizationSemanticError("当前没有可确认草案", code="draft_not_found", status_code=404)
            peers = await self._peer_top_departments(session, workspace_id, org_unit_id)
            peer_names = {row.name for row in peers}
            normalized = self._validate_profile_content(
                version.content_json or {},
                allowed_collaboration_names=peer_names,
            )
            version.content_json = normalized
            if binding.active_version_id:
                previous = await session.get(
                    OrganizationSemanticProfileVersionModel, binding.active_version_id,
                )
                if previous:
                    previous.status = "superseded"
            version.status = "confirmed"
            version.reviewed_by = actor_id
            version.reviewed_at = datetime.now()
            binding.active_version_id = version.id
            binding.draft_version_id = None
            binding.status = "confirmed"
            binding.error_message = None
            binding.revision += 1
            await session.flush()
            # Evidence readiness is cheap to re-evaluate in the worker; enqueue all data sources.
            from app.models.config.semantic import SemanticDatasourceModel
            datasource_ids = list((await session.execute(select(SemanticDatasourceModel.id).where(
                SemanticDatasourceModel.workspace_id == workspace_id,
                SemanticDatasourceModel.is_active == True,  # noqa: E712
            ))).scalars())
            for datasource_id in datasource_ids:
                await self._enqueue_evidence_in_session(
                    session, workspace_id, int(datasource_id), actor_id,
                )
            return self._profile_payload(
                department,
                binding,
                {int(version.id): version},
                allowed_collaboration_names=peer_names,
            )

    async def retry(self, workspace_id: str, org_unit_id: int, actor_id: str) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_top_department(session, workspace_id, org_unit_id)
            run = await self._enqueue_profile_in_session(
                session, workspace_id, org_unit_id, actor_id, force=True,
            )
            return {"run_id": run.id, "status": run.status}

    async def invalidate_org(
        self, session, workspace_id: str, org_unit_id: int, actor_id: str,
    ) -> None:
        department = await self._assert_department(session, workspace_id, org_unit_id)
        top_id = int(department.get_ancestors_list()[0]) if department.parent_id else int(department.id)
        top = await self._assert_department(session, workspace_id, top_id)
        if top.status:
            await self._enqueue_profile_in_session(
                session, workspace_id, top_id, actor_id, force=True,
            )
        else:
            binding = (await session.execute(select(OrganizationSemanticProfileModel).where(
                OrganizationSemanticProfileModel.workspace_id == workspace_id,
                OrganizationSemanticProfileModel.org_unit_id == top_id,
            ))).scalar_one_or_none()
            if binding:
                binding.status = "stale"
            from app.models.config.semantic import SemanticDatasourceModel
            datasource_ids = list((await session.execute(select(SemanticDatasourceModel.id).where(
                SemanticDatasourceModel.workspace_id == workspace_id,
                SemanticDatasourceModel.is_active == True,  # noqa: E712
            ))).scalars())
            for datasource_id in datasource_ids:
                await self._enqueue_evidence_in_session(
                    session, workspace_id, int(datasource_id), actor_id,
                )
        from app.models.config.permission_evidence import SemanticAccessEvidenceSetModel
        evidence_sets = list((await session.execute(select(SemanticAccessEvidenceSetModel).where(
            SemanticAccessEvidenceSetModel.workspace_id == workspace_id,
            SemanticAccessEvidenceSetModel.status == "published",
        ))).scalars())
        for evidence_set in evidence_sets:
            evidence_set.status = "stale"

    async def invalidate_workspace_org_set(
        self, session, workspace_id: str, actor_id: str,
    ) -> None:
        from app.models.config.permission_evidence import SemanticAccessEvidenceSetModel
        from app.models.config.semantic import SemanticDatasourceModel
        evidence_sets = list((await session.execute(select(SemanticAccessEvidenceSetModel).where(
            SemanticAccessEvidenceSetModel.workspace_id == workspace_id,
            SemanticAccessEvidenceSetModel.status == "published",
        ))).scalars())
        for evidence_set in evidence_sets:
            evidence_set.status = "stale"
        datasource_ids = list((await session.execute(select(SemanticDatasourceModel.id).where(
            SemanticDatasourceModel.workspace_id == workspace_id,
            SemanticDatasourceModel.is_active == True,  # noqa: E712
        ))).scalars())
        for datasource_id in datasource_ids:
            await self._enqueue_evidence_in_session(
                session, workspace_id, int(datasource_id), actor_id,
            )

    async def process_profile_run(self, run_id: int) -> None:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            run = await session.get(SemanticPermissionEvidenceRunModel, run_id)
            if not run or run.job_kind != "org_profile":
                return
            run.status, run.stage, run.started_at = "running", "collecting", datetime.now()
            snapshot, context_revision = await self._organization_input(
                session, run.workspace_id, int(run.org_unit_id),
            )
            fingerprint = stable_fingerprint(snapshot)
            if fingerprint != run.input_fingerprint:
                run.status, run.stage, run.finished_at = "discarded", "input_changed", datetime.now()
                await self._enqueue_profile_in_session(
                    session, run.workspace_id, int(run.org_unit_id), run.created_by or "system", force=True,
                )
                return
            run.stage, run.progress = "generating", 30
        try:
            result = await self.llm.generate_json(
                [
                    {"role": "system", "content": (
                        "你负责生成企业一级部门职责画像。只能依据给定的企业背景与组织事实，"
                        "不得读取或推断个人身份、样例数据或现有权限。企业背景可能不完整；"
                        "不得把行业常识伪装为已知事实。输出 JSON，键为 positioning, "
                        "responsibilities, business_objects, data_produced, data_consumed, "
                        "collaborations, boundaries, keywords, known_facts, assumptions, "
                        "missing_information, permission_impacts。positioning 为字符串，其余为"
                        "简洁字符串数组。known_facts 仅列输入直接支持的事实；assumptions 列出"
                        "为完成画像采用的通用推断；missing_information 列出仍无法判断的信息；"
                        "permission_impacts 说明这些未知项可能影响的表、字段或行范围判断。"
                        "collaborations 只能引用输入 peer_top_departments 中真实存在的其他一级部门，"
                        "不得创造、补全或猜测未创建的部门；没有合适部门时返回空数组。"
                    )},
                    {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)},
                ],
                model=getattr(self.settings.llm, "fast_model", self.settings.llm.model),
                temperature=0,
                max_tokens=3000,
            )
            content = self._validate_profile_content(
                result,
                allowed_collaboration_names={
                    str(item["name"])
                    for item in snapshot.get("peer_top_departments", [])
                    if item.get("name")
                },
            )
        except Exception as exc:
            async with db.session_scope() as session:
                run = await session.get(SemanticPermissionEvidenceRunModel, run_id)
                if run:
                    run.status, run.stage, run.error_message = "failed", "failed", str(exc)[:2000]
                    run.finished_at = datetime.now()
                    binding = (await session.execute(select(OrganizationSemanticProfileModel).where(
                        OrganizationSemanticProfileModel.workspace_id == run.workspace_id,
                        OrganizationSemanticProfileModel.org_unit_id == run.org_unit_id,
                    ))).scalar_one_or_none()
                    if binding:
                        binding.status, binding.error_message = "failed", str(exc)[:2000]
            return
        async with db.session_scope() as session:
            run = await session.get(SemanticPermissionEvidenceRunModel, run_id)
            snapshot_now, _ = await self._organization_input(
                session, run.workspace_id, int(run.org_unit_id),
            )
            if stable_fingerprint(snapshot_now) != run.input_fingerprint:
                run.status, run.stage, run.finished_at = "discarded", "input_changed", datetime.now()
                await self._enqueue_profile_in_session(
                    session, run.workspace_id, int(run.org_unit_id), run.created_by or "system", force=True,
                )
                return
            binding = (await session.execute(select(OrganizationSemanticProfileModel).where(
                OrganizationSemanticProfileModel.workspace_id == run.workspace_id,
                OrganizationSemanticProfileModel.org_unit_id == run.org_unit_id,
            ).with_for_update())).scalar_one()
            version_number = int((await session.execute(select(func.max(
                OrganizationSemanticProfileVersionModel.version
            )).where(
                OrganizationSemanticProfileVersionModel.workspace_id == run.workspace_id,
                OrganizationSemanticProfileVersionModel.org_unit_id == run.org_unit_id,
            ))).scalar() or 0) + 1
            context_quality = (
                snapshot.get("business_context", {}).get("quality")
                or business_context_quality({})
            )
            sources = ["organization", "positions", "role_capabilities"]
            if context_quality["has_any"]:
                sources.insert(0, "business_context")
            version = OrganizationSemanticProfileVersionModel(
                workspace_id=run.workspace_id,
                org_unit_id=run.org_unit_id,
                version=version_number,
                content_json=content,
                evidence_json={
                    "sources": sources,
                    "business_context_quality": context_quality,
                    "assumption_count": len(content["assumptions"]),
                    "missing_information_count": len(content["missing_information"]),
                },
                input_fingerprint=run.input_fingerprint,
                business_context_revision=context_revision,
                source="ai",
                created_by=run.created_by,
            )
            session.add(version)
            await session.flush()
            binding.draft_version_id = version.id
            binding.status = "draft"
            binding.input_fingerprint = run.input_fingerprint
            binding.error_message = None
            binding.revision += 1
            run.status, run.stage, run.progress = "completed", "draft_ready", 100
            run.finished_at = datetime.now()
            run.result_summary_json = {"profile_version_id": version.id, "version": version_number}

    async def _enqueue_profile_in_session(
        self, session, workspace_id: str, org_unit_id: int, actor_id: str, *, force: bool,
    ):
        await self._assert_top_department(session, workspace_id, org_unit_id)
        snapshot, _ = await self._organization_input(session, workspace_id, org_unit_id)
        fingerprint = stable_fingerprint(snapshot)
        binding = (await session.execute(select(OrganizationSemanticProfileModel).where(
            OrganizationSemanticProfileModel.workspace_id == workspace_id,
            OrganizationSemanticProfileModel.org_unit_id == org_unit_id,
        ).with_for_update())).scalar_one_or_none()
        if binding is None:
            binding = OrganizationSemanticProfileModel(
                workspace_id=workspace_id, org_unit_id=org_unit_id, status="generating",
            )
            session.add(binding)
        else:
            binding.status = "stale" if binding.active_version_id else "generating"
        open_run = (await session.execute(select(SemanticPermissionEvidenceRunModel).where(
            SemanticPermissionEvidenceRunModel.workspace_id == workspace_id,
            SemanticPermissionEvidenceRunModel.org_unit_id == org_unit_id,
            SemanticPermissionEvidenceRunModel.job_kind == "org_profile",
            SemanticPermissionEvidenceRunModel.status.in_(("queued", "running")),
        ).order_by(SemanticPermissionEvidenceRunModel.id.desc()))).scalars().first()
        if open_run and open_run.input_fingerprint == fingerprint:
            return open_run
        if open_run:
            open_run.status, open_run.stage = "discarded", "superseded"
            open_run.finished_at = datetime.now()
        run = SemanticPermissionEvidenceRunModel(
            workspace_id=workspace_id, org_unit_id=org_unit_id,
            job_kind="org_profile", input_fingerprint=fingerprint, created_by=actor_id,
        )
        session.add(run)
        await session.flush()
        return run

    async def _enqueue_evidence_in_session(
        self, session, workspace_id: str, datasource_id: int, actor_id: str,
    ) -> None:
        # The evidence worker rechecks the full readiness gate.
        fingerprint = stable_fingerprint({"workspace_id": workspace_id, "datasource_id": datasource_id})
        existing = (await session.execute(select(SemanticPermissionEvidenceRunModel).where(
            SemanticPermissionEvidenceRunModel.workspace_id == workspace_id,
            SemanticPermissionEvidenceRunModel.datasource_id == datasource_id,
            SemanticPermissionEvidenceRunModel.job_kind == "access_evidence",
            SemanticPermissionEvidenceRunModel.status.in_(("queued", "running")),
        ))).scalars().first()
        if not existing:
            session.add(SemanticPermissionEvidenceRunModel(
                workspace_id=workspace_id, datasource_id=datasource_id,
                job_kind="access_evidence", input_fingerprint=fingerprint, created_by=actor_id,
            ))

    async def _peer_top_departments(
        self, session, workspace_id: str, org_unit_id: int,
    ) -> list[DepartmentModel]:
        return list((await session.execute(select(DepartmentModel).where(
            DepartmentModel.workspace_id == workspace_id,
            DepartmentModel.parent_id.is_(None),
            DepartmentModel.status == True,  # noqa: E712
            DepartmentModel.id != org_unit_id,
        ).order_by(DepartmentModel.order_num, DepartmentModel.id))).scalars())

    async def _organization_input(self, session, workspace_id: str, org_unit_id: int):
        context = (await session.execute(select(WorkspaceBusinessContextModel).where(
            WorkspaceBusinessContextModel.workspace_id == workspace_id,
        ))).scalar_one_or_none()
        top = await self._assert_top_department(session, workspace_id, org_unit_id)
        peers = await self._peer_top_departments(session, workspace_id, org_unit_id)
        descendants = list((await session.execute(select(DepartmentModel).where(
            DepartmentModel.workspace_id == workspace_id,
            or_(
                DepartmentModel.id == org_unit_id,
                DepartmentModel.ancestors.like(f"%/{org_unit_id}/%"),
            ),
            DepartmentModel.status == True,  # noqa: E712
        ).order_by(DepartmentModel.order_num, DepartmentModel.id))).scalars())
        org_ids = [int(row.id) for row in descendants]
        positions = list((await session.execute(select(PositionModel).where(
            PositionModel.workspace_id == workspace_id,
            PositionModel.org_unit_id.in_(org_ids),
            PositionModel.status == True,  # noqa: E712
        ).order_by(PositionModel.org_unit_id, PositionModel.name))).scalars())
        role_rows = (await session.execute(
            select(RoleBindingModel.position_id, RoleModel)
            .join(RoleModel, RoleModel.id == RoleBindingModel.role_id)
            .options(selectinload(RoleModel.permissions))
            .where(
                RoleBindingModel.workspace_id == workspace_id,
                RoleBindingModel.position_id.in_([row.id for row in positions] or [-1]),
                RoleBindingModel.status == True,  # noqa: E712
            )
        )).unique().all()
        roles_by_position: dict[int, list[dict[str, Any]]] = {}
        for position_id, role in role_rows:
            roles_by_position.setdefault(int(position_id), []).append({
                "name": role.name,
                "capability_description": role.description or "",
                "capabilities": [
                    {"code": permission.code, "description": permission.description or ""}
                    for permission in role.permissions
                ],
            })
        snapshot = {
            "business_context": self._context_snapshot(context),
            "business_context_revision": int(context.revision) if context else 0,
            "top_department": {"id": int(top.id), "name": top.name, "code": top.code},
            "peer_top_departments": [
                {"id": int(row.id), "name": row.name, "code": row.code}
                for row in peers
            ],
            "departments": [
                {"id": int(row.id), "parent_id": row.parent_id, "name": row.name, "code": row.code}
                for row in descendants
            ],
            "positions": [
                {
                    "id": int(row.id), "org_unit_id": int(row.org_unit_id), "name": row.name,
                    "roles": roles_by_position.get(int(row.id), []),
                } for row in positions
            ],
        }
        return snapshot, int(context.revision) if context else 0

    async def _assert_department(self, session, workspace_id: str, org_unit_id: int):
        row = await session.get(DepartmentModel, org_unit_id)
        if not row or row.workspace_id != workspace_id:
            raise OrganizationSemanticError("部门不存在", code="org_not_found", status_code=404)
        return row

    async def _assert_top_department(self, session, workspace_id: str, org_unit_id: int):
        row = await self._assert_department(session, workspace_id, org_unit_id)
        if row.parent_id is not None or not row.status:
            raise OrganizationSemanticError("仅启用的一级部门可以维护画像", code="top_org_required")
        return row

    async def _binding_for_update(self, session, workspace_id: str, org_unit_id: int):
        department = await self._assert_top_department(session, workspace_id, org_unit_id)
        binding = (await session.execute(select(OrganizationSemanticProfileModel).where(
            OrganizationSemanticProfileModel.workspace_id == workspace_id,
            OrganizationSemanticProfileModel.org_unit_id == org_unit_id,
        ).with_for_update())).scalar_one_or_none()
        if not binding:
            raise OrganizationSemanticError("部门画像尚未生成", code="profile_not_found", status_code=404)
        return binding, department

    def _validate_profile_content(
        self,
        content: dict[str, Any],
        allowed_collaboration_names: set[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(content, dict):
            raise OrganizationSemanticError("画像内容格式错误", code="invalid_profile")
        normalized: dict[str, Any] = {}
        for key in PROFILE_KEYS:
            value = content.get(key, [] if key in PROFILE_INSIGHT_KEYS else None)
            if key == "positioning":
                if not isinstance(value, str) or not value.strip():
                    raise OrganizationSemanticError("部门定位不能为空", code="invalid_profile")
                normalized[key] = value.strip()
            else:
                if key in PROFILE_INSIGHT_KEYS:
                    value = normalize_profile_insight(value)
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise OrganizationSemanticError(f"{key} 必须是文本列表", code="invalid_profile")
                normalized[key] = [item.strip() for item in value if item.strip()]
        if allowed_collaboration_names is not None:
            normalized = restrict_profile_collaborations(
                normalized,
                allowed_collaboration_names,
            )
        return normalized

    @staticmethod
    def _context_payload(row) -> dict[str, Any]:
        normalized = OrganizationSemanticService._context_snapshot(row)
        quality = normalized.pop("quality")
        if not row:
            return {
                "exists": False,
                **normalized,
                "quality": quality,
                "revision": 0,
            }
        return {
            "exists": quality["has_any"],
            **normalized,
            "quality": quality,
            "revision": row.revision,
            "updated_by": row.updated_by, "updated_at": _dt(row.updated_at),
        }

    @staticmethod
    def _context_snapshot(row) -> dict[str, Any]:
        return business_context_snapshot(row)

    @staticmethod
    def _version_payload(
        row,
        allowed_collaboration_names: set[str] | None = None,
    ) -> dict[str, Any] | None:
        if not row:
            return None
        content = dict(row.content_json or {})
        if allowed_collaboration_names is not None:
            content = restrict_profile_collaborations(
                content,
                allowed_collaboration_names,
            )
        return {
            "id": row.id, "version": row.version, "status": row.status,
            "content": content, "evidence": row.evidence_json or {},
            "confidence": row.confidence, "source": row.source,
            "reviewed_by": row.reviewed_by, "reviewed_at": _dt(row.reviewed_at),
            "created_at": _dt(row.created_at),
        }

    def _profile_payload(
        self,
        department,
        binding,
        versions,
        allowed_collaboration_names: set[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "org_unit": {"id": int(department.id), "name": department.name, "code": department.code},
            "status": binding.status if binding else "missing",
            "revision": int(binding.revision or 0) if binding else 0,
            "error_message": binding.error_message if binding else None,
            "active_version": self._version_payload(
                versions.get(int(binding.active_version_id)) if binding and binding.active_version_id else None,
                allowed_collaboration_names,
            ),
            "draft_version": self._version_payload(
                versions.get(int(binding.draft_version_id)) if binding and binding.draft_version_id else None,
                allowed_collaboration_names,
            ),
        }


_service: OrganizationSemanticService | None = None


def get_organization_semantic_service() -> OrganizationSemanticService:
    global _service
    if _service is None:
        _service = OrganizationSemanticService()
    return _service
