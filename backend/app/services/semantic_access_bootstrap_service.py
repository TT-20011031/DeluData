"""AI-assisted first-time semantic access configuration.

The service only sends governed metadata to the LLM. Generated policies remain
reviewable database drafts until an administrator atomically applies a run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import delete, func, or_, select

from app.config import get_settings
from app.core.db.database import get_async_db_manager
from app.core.llm.async_llm import get_async_llm
from app.models.auth.authorization import AssignmentModel, PositionModel
from app.models.auth.organization import DepartmentModel
from app.models.auth.rbac import UserModel
from app.models.auth.workspace import WorkspaceModel
from app.models.config.permission_evidence import (
    SemanticAccessEvidenceAssetModel,
    SemanticAccessEvidenceRelationModel,
    SemanticAccessEvidenceSetModel,
)
from app.models.config.semantic import (
    SemanticAccessBootstrapMappingModel,
    SemanticAccessBootstrapRunModel,
    SemanticAccessBootstrapTargetModel,
    SemanticColumnModel,
    SemanticDatasourceModel,
    SemanticMetricModel,
    SemanticOwnershipMappingModel,
    SemanticPolicyBindingModel,
    SemanticPolicyVersionModel,
    SemanticPolicyVersionEffectModel,
    SemanticRelationshipModel,
    SemanticTableModel,
)
from app.services.authorization_service import (
    build_effective_access_context,
    bump_authorization_revision,
    record_authorization_audit,
)
from app.services.semantic_policy_binding_service import (
    SemanticBindingError,
    _context_allows_scope,
    _definition_expands_access,
    _effective_policy_context,
    _prepare_definition_v3,
    load_bound_semantic_runtime,
    persist_target_policy_version_in_session,
    upsert_ownership_mapping_in_session,
)


logger = logging.getLogger(__name__)
settings = get_settings()

HIGH_CONFIDENCE = 0.60
REVIEW_CONFIDENCE = 0.45
BATCH_SIZE = 10
MAX_COLUMNS_PER_TABLE = 30
FIELD_BATCH_SIZE = 50
BOOTSTRAP_MODE = "table_field_only"
OPEN_RUN_STATUSES = {"pending", "running", "cancel_requested", "review_ready", "partial"}
RUNNING_RUN_STATUSES = {"running", "cancel_requested"}
TERMINAL_RUN_STATUSES = {"applied", "failed", "cancelled", "stale", "discarded"}
OWNERSHIP_TERMS = (
    "department", "dept", "org", "organization", "部门", "组织", "机构",
    "owner", "creator", "user", "person", "负责人", "人员", "用户", "创建人",
)


class SemanticAccessBootstrapError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "access_bootstrap_error",
        status_code: int = 422,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _stable_fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clamp_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _strict_confidence(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须是 0 到 1 的数字")
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        raise ValueError(f"{label} 超出 0 到 1 范围")
    return confidence


def _mapping_payload(row: SemanticOwnershipMappingModel | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "org_column_id": row.org_column_id,
        "org_value_kind": row.org_value_kind,
        "org_value_mapping": row.org_value_mapping_json or {},
        "user_column_id": row.user_column_id,
        "user_value_kind": row.user_value_kind,
    }


def _asset_snapshot(tables: list[Any], columns: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """Persist the minimal enabled-asset catalogue needed to review a run later."""
    return {
        "tables": [{
            "id": int(row.id),
            "business_name": row.business_name,
            "physical_name": row.physical_name,
            "is_sensitive": bool(row.is_sensitive),
        } for row in tables],
        "columns": [{
            "id": int(row.id),
            "table_id": int(row.table_id),
            "business_name": row.business_name,
            "physical_name": row.physical_name,
            "data_type": row.data_type,
            "is_sensitive": bool(row.is_sensitive),
            "ordinal_position": int(row.ordinal_position or 0),
        } for row in columns],
    }


def _evidence_baseline_access(asset: Any) -> str:
    value = getattr(asset, "baseline_access", None)
    if value in {"workspace_visible", "controlled"}:
        return value
    return (
        "workspace_visible"
        if getattr(asset, "access_class", None) == "workspace_public"
        else "controlled"
    )


def _evidence_table_access(relation: Any) -> str:
    value = getattr(relation, "access_level", None)
    if value in {"visible", "partial", "hidden"}:
        return value
    decision = getattr(relation, "access_decision", None)
    if decision == "visible":
        scope_type = (getattr(relation, "row_scope_json", None) or {}).get("type", "all")
        return "visible" if scope_type == "all" else "partial"
    return "hidden"


def _evidence_field_access(relation: Any) -> str:
    value = getattr(relation, "field_decision", None)
    if value in {"visible", "hidden"}:
        return value
    return "visible" if getattr(relation, "access_decision", None) == "visible" else "hidden"


def _row_ownership_generation_summary(value: Any) -> dict[str, Any]:
    summary = dict(value or {})
    return dict(
        summary.get("row_ownership_v2")
        or summary.get("row_ownership_v1")
        or {}
    )


def _merge_table_definition(
    active_definition: dict[str, Any] | None,
    draft_definition: dict[str, Any],
    table_id: int,
) -> dict[str, Any]:
    """Replace exactly one table rule while preserving every other active rule."""
    active_rules = {
        int(rule["table_id"]): deepcopy(rule)
        for rule in (active_definition or {}).get("tables") or []
    }
    draft_rule = next((
        deepcopy(rule) for rule in draft_definition.get("tables") or []
        if int(rule.get("table_id") or 0) == int(table_id)
    ), None)
    if draft_rule is None:
        active_rules.pop(int(table_id), None)
    else:
        active_rules[int(table_id)] = draft_rule
    return {
        **deepcopy(active_definition or {}),
        "name": draft_definition.get("name")
        or (active_definition or {}).get("name")
        or "问数权限",
        "tables": [active_rules[key] for key in sorted(active_rules)],
    }


def _review_published_tables(
    candidates: list[dict[str, Any]],
    rules_by_table: dict[int, dict[str, Any]],
    table_ids: set[int],
) -> list[dict[str, Any]]:
    """Mark only the published table cells reviewed, preserving other drafts."""
    reviewed = deepcopy(candidates)
    for candidate in reviewed:
        table_id = int(candidate.get("table_id") or 0)
        if table_id not in table_ids:
            continue
        rule = rules_by_table.get(table_id)
        expected_visible = bool(candidate.get("selected"))
        if rule is None:
            candidate["review_state"] = "rejected" if expected_visible else "accepted"
        else:
            expected_hidden = set(candidate.get("hidden_column_ids") or [])
            actual_hidden = set(rule.get("hidden_column_ids") or [])
            expected_metrics = set(candidate.get("hidden_metric_ids") or [])
            actual_metrics = set(rule.get("hidden_metric_ids") or [])
            expected_decision = candidate.get("decision") or (
                "visible" if expected_visible else "hidden"
            )
            expected_scope = candidate.get("row_scope") or "all"
            if isinstance(expected_scope, str):
                expected_scope = {"type": expected_scope}
            actual_scope = rule.get("row_scope") or {"type": "all"}
            candidate["review_state"] = (
                "accepted"
                if rule.get("decision", "visible") == expected_decision
                and expected_hidden == actual_hidden
                and expected_metrics == actual_metrics
                and (expected_decision != "visible" or expected_scope == actual_scope)
                else "modified"
            )
        actual_hidden = set((rule or {}).get("hidden_column_ids") or [])
        for field in candidate.get("field_suggestions") or []:
            if rule is None:
                field["review_state"] = "rejected"
                continue
            expected_field_hidden = field.get("effective_decision") == "hidden"
            field["review_state"] = (
                "accepted"
                if (int(field.get("column_id") or 0) in actual_hidden) == expected_field_hidden
                else "modified"
            )
    return reviewed


class SemanticAccessBootstrapService:
    def __init__(self) -> None:
        self.llm = get_async_llm()

    async def _assert_workspace_manage(self, session, workspace_id: str, actor_id: str) -> None:
        context = await build_effective_access_context(session, workspace_id, actor_id)
        if not _context_allows_scope(context, "semantic_access:manage", None):
            raise SemanticAccessBootstrapError(
                "AI 首次配置需要工作区级问数权限管理能力",
                code="workspace_scope_required",
                status_code=403,
            )

    async def _datasource(self, session, workspace_id: str, datasource_id: int):
        datasource = await session.get(SemanticDatasourceModel, datasource_id)
        if not datasource or datasource.workspace_id != workspace_id:
            raise SemanticAccessBootstrapError(
                "数据源不存在", code="datasource_not_found", status_code=404,
            )
        return datasource

    async def _governed_assets(self, session, workspace_id: str, datasource_id: int):
        """Return eligible governed metadata and a semantic snapshot fingerprint."""
        datasource = await self._datasource(session, workspace_id, datasource_id)
        tables = list((await session.execute(select(SemanticTableModel).where(
            SemanticTableModel.workspace_id == workspace_id,
            SemanticTableModel.datasource_id == datasource_id,
            SemanticTableModel.status == "confirmed",
            SemanticTableModel.sync_state == "current",
            SemanticTableModel.is_queryable == True,  # noqa: E712
        ).order_by(SemanticTableModel.id))).scalars())
        table_ids = [int(row.id) for row in tables]
        columns = list((await session.execute(select(SemanticColumnModel).where(
            SemanticColumnModel.workspace_id == workspace_id,
            SemanticColumnModel.datasource_id == datasource_id,
            SemanticColumnModel.table_id.in_(table_ids or [-1]),
            SemanticColumnModel.status == "confirmed",
            SemanticColumnModel.sync_state == "current",
            SemanticColumnModel.is_queryable == True,  # noqa: E712
        ).order_by(
            SemanticColumnModel.table_id,
            SemanticColumnModel.ordinal_position,
            SemanticColumnModel.id,
        ))).scalars())
        metrics = list((await session.execute(select(SemanticMetricModel).where(
            SemanticMetricModel.workspace_id == workspace_id,
            SemanticMetricModel.datasource_id == datasource_id,
            SemanticMetricModel.table_id.in_(table_ids or [-1]),
            SemanticMetricModel.status == "confirmed",
            SemanticMetricModel.sync_state == "current",
            SemanticMetricModel.is_queryable == True,  # noqa: E712
        ).order_by(SemanticMetricModel.table_id, SemanticMetricModel.id))).scalars())
        fingerprint = _stable_fingerprint({
            "physical_schema": datasource.schema_fingerprint,
            "tables": [{
                "id": int(row.id),
                "physical_name": row.physical_name,
                "business_name": row.business_name,
                "description": row.description,
                "physical_comment": row.physical_comment,
                "synonyms": row.synonyms or [],
                "is_sensitive": bool(row.is_sensitive),
                "confidence": row.confidence,
            } for row in tables],
            "columns": [{
                "id": int(row.id),
                "table_id": int(row.table_id),
                "physical_name": row.physical_name,
                "business_name": row.business_name,
                "description": row.description,
                "physical_comment": row.physical_comment,
                "data_type": row.data_type,
                "synonyms": row.synonyms or [],
                "is_sensitive": bool(row.is_sensitive),
                "confidence": row.confidence,
                "ordinal_position": row.ordinal_position,
            } for row in columns],
            "metrics": [{
                "id": int(row.id),
                "table_id": int(row.table_id),
                "name": row.name,
                "business_name": row.business_name,
                "description": row.description,
                "formula": row.formula,
                "is_sensitive": bool(row.is_sensitive),
                "confidence": row.confidence,
            } for row in metrics],
        })
        return datasource, tables, columns, metrics, fingerprint

    async def _organization_snapshot(self, session, workspace_id: str) -> tuple[list[Any], str]:
        rows = list((await session.execute(select(DepartmentModel).where(
            DepartmentModel.workspace_id == workspace_id,
        ).order_by(DepartmentModel.id))).scalars())
        payload = [{
            "id": int(row.id),
            "parent_id": int(row.parent_id) if row.parent_id is not None else None,
            "name": row.name,
            "code": row.code,
            "status": bool(row.status),
        } for row in rows]
        return rows, _stable_fingerprint(payload)

    def _eligible_top_departments(
        self,
        departments: list[Any],
        configured: set[tuple[str, str]] | None = None,
    ) -> list[Any]:
        configured = configured or set()
        return [
            row for row in departments
            if row.parent_id is None
            and bool(row.status)
            and ("org_unit", str(row.id)) not in configured
        ]

    async def readiness(
        self, workspace_id: str, datasource_id: int, actor_id: str,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            datasource, eligible_tables, eligible_columns, _eligible_metrics, semantic_fingerprint = (
                await self._governed_assets(session, workspace_id, datasource_id)
            )
            departments, organization_fingerprint = await self._organization_snapshot(
                session, workspace_id,
            )
            top_departments = self._eligible_top_departments(departments)
            target_keys = {("baseline", "*")} | {
                ("org_unit", str(row.id)) for row in top_departments
            }
            bindings = list((await session.execute(select(SemanticPolicyBindingModel).where(
                SemanticPolicyBindingModel.workspace_id == workspace_id,
                SemanticPolicyBindingModel.datasource_id == datasource_id,
            ))).scalars())
            configured = {
                (row.target_type, row.target_id)
                for row in bindings
                if row.active_version_id and row.status
            }
            unconfigured = sorted(target_keys - configured)
            mappings = list((await session.execute(select(SemanticOwnershipMappingModel).where(
                SemanticOwnershipMappingModel.workspace_id == workspace_id,
                SemanticOwnershipMappingModel.datasource_id == datasource_id,
                SemanticOwnershipMappingModel.org_column_id.is_not(None),
            ))).scalars())
            active = (await session.execute(select(SemanticAccessBootstrapRunModel).where(
                SemanticAccessBootstrapRunModel.workspace_id == workspace_id,
                SemanticAccessBootstrapRunModel.datasource_id == datasource_id,
                SemanticAccessBootstrapRunModel.status.in_(list(OPEN_RUN_STATUSES)),
            ).order_by(SemanticAccessBootstrapRunModel.created_at.desc()))).scalars().first()

            blockers: list[dict[str, str]] = []
            warnings: list[dict[str, str]] = []
            if not eligible_tables:
                blockers.append({"code": "semantic_tables_empty", "message": "没有可用于首次配置的已确认语义表"})
            if not top_departments:
                warnings.append({"code": "top_departments_empty", "message": "没有启用的一级部门，本次只会生成全员基线"})
            if not unconfigured:
                blockers.append({"code": "targets_configured", "message": "全员基线和一级部门均已有生效配置"})
            ignored_table_count = int((await session.execute(select(func.count(SemanticTableModel.id)).where(
                SemanticTableModel.workspace_id == workspace_id,
                SemanticTableModel.datasource_id == datasource_id,
                or_(
                    SemanticTableModel.status != "confirmed",
                    SemanticTableModel.sync_state != "current",
                    SemanticTableModel.is_queryable == False,  # noqa: E712
                ),
            ))).scalar_one() or 0)
            if ignored_table_count:
                warnings.append({
                    "code": "semantic_tables_ignored",
                    "message": f"{ignored_table_count} 张未确认、失效或不可问数的表将被忽略",
                })
            return {
                "ready": not blockers,
                "datasource_id": datasource_id,
                "schema_fingerprint": semantic_fingerprint,
                "organization_fingerprint": organization_fingerprint,
                "eligible_table_count": len(eligible_tables),
                "eligible_column_count": len(eligible_columns),
                "eligible_table_ids": [int(row.id) for row in eligible_tables],
                "ignored_table_count": ignored_table_count,
                "top_level_departments": [
                    {"id": row.id, "name": row.name, "code": row.code}
                    for row in top_departments
                ],
                "unconfigured_target_count": len(unconfigured),
                "configured_target_count": len(target_keys & configured),
                "configured_targets_skipped": [
                    {"target_type": kind, "target_id": target_id}
                    for kind, target_id in sorted(target_keys & configured)
                ],
                "ownership_mapping_count": len(mappings),
                "ownership_mapping_coverage": (
                    len(mappings) / len(eligible_tables) if eligible_tables else 0.0
                ),
                "blockers": blockers,
                "warnings": warnings,
                "active_run": self._run_payload(active) if active else None,
            }

    async def create_run(
        self,
        workspace_id: str,
        datasource_id: int,
        actor_id: str,
        business_context: str | None = None,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.get_session() as session:
            datasource = await self._datasource(session, workspace_id, datasource_id)
            if bool(getattr(datasource, "access_bootstrap_required", False)):
                raise SemanticAccessBootstrapError(
                    "该数据源必须先确认访问依据，再由确定性编译生成首次权限",
                    code="access_evidence_required",
                    status_code=409,
                )
        readiness = await self.readiness(workspace_id, datasource_id, actor_id)
        active_payload = readiness.get("active_run")
        if active_payload and not (
            active_payload.get("status") == "partial"
            and not active_payload.get("retryable")
        ):
            return {**active_payload, "coalesced": True}
        if not readiness["ready"]:
            raise SemanticAccessBootstrapError(
                "当前数据源尚不满足 AI 首次配置条件",
                code="readiness_blocked",
                details={"blockers": readiness["blockers"], "warnings": readiness["warnings"]},
            )
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            _datasource, eligible_tables, eligible_columns, _metrics, semantic_fingerprint = (
                await self._governed_assets(session, workspace_id, datasource_id)
            )
            if semantic_fingerprint != readiness["schema_fingerprint"]:
                raise SemanticAccessBootstrapError(
                    "语义 Schema 已变化，请重新检查后生成",
                    code="schema_changed",
                    status_code=409,
                )
            await session.execute(select(SemanticDatasourceModel.id).where(
                SemanticDatasourceModel.id == datasource_id,
            ).with_for_update())
            active = (await session.execute(select(SemanticAccessBootstrapRunModel).where(
                SemanticAccessBootstrapRunModel.workspace_id == workspace_id,
                SemanticAccessBootstrapRunModel.datasource_id == datasource_id,
                SemanticAccessBootstrapRunModel.status.in_(list(OPEN_RUN_STATUSES)),
            ).order_by(SemanticAccessBootstrapRunModel.created_at.desc()))).scalars().first()
            if active:
                if (
                    active.status == "partial"
                    and int(active.attempt or 0) >= int(active.max_attempts or 2)
                ):
                    active.status = "discarded"
                    active.stage = "retry_limit_reached"
                    active.completed_at = datetime.now()
                    active.revision += 1
                else:
                    return {**self._run_payload(active), "coalesced": True}
            run = SemanticAccessBootstrapRunModel(
                workspace_id=workspace_id,
                datasource_id=datasource_id,
                status="pending",
                stage="queued",
                progress=0,
                revision=0,
                schema_fingerprint=readiness["schema_fingerprint"],
                organization_fingerprint=readiness["organization_fingerprint"],
                business_context=(business_context or "").strip()[:8000] or None,
                input_snapshot_json={
                    "bootstrap_mode": BOOTSTRAP_MODE,
                    "top_level_departments": readiness["top_level_departments"],
                    "configured_targets_skipped": readiness["configured_targets_skipped"],
                    "eligible_table_count": readiness["eligible_table_count"],
                    "eligible_column_count": readiness["eligible_column_count"],
                    "eligible_table_ids": readiness["eligible_table_ids"],
                    "assets": _asset_snapshot(eligible_tables, eligible_columns),
                    "metadata_only": True,
                },
                summary_json={},
                triggered_by=actor_id,
                max_attempts=2,
            )
            session.add(run)
            await session.flush()
            return self._run_payload(run)

    def _run_payload(self, run: SemanticAccessBootstrapRunModel | None, **extra: Any) -> dict[str, Any]:
        if run is None:
            return {}
        return {
            "run_id": run.id,
            "workspace_id": run.workspace_id,
            "datasource_id": run.datasource_id,
            "evidence_set_id": run.evidence_set_id,
            "status": run.status,
            "stage": run.stage,
            "progress": int(run.progress or 0),
            "revision": int(run.revision or 0),
            "schema_fingerprint": run.schema_fingerprint,
            "organization_fingerprint": run.organization_fingerprint,
            "business_context": run.business_context,
            "summary": run.summary_json or {},
            "error_message": run.error_message,
            "worker_id": run.worker_id,
            "attempt": int(run.attempt or 0),
            "max_attempts": int(run.max_attempts or 0),
            "retryable": (
                run.status in {"failed", "partial", "cancelled"}
                and int(run.attempt or 0) < int(run.max_attempts or 2)
            ),
            "cancel_requested_at": _serialize_datetime(run.cancel_requested_at),
            "created_at": _serialize_datetime(run.created_at),
            "started_at": _serialize_datetime(run.started_at),
            "completed_at": _serialize_datetime(run.completed_at),
            "applied_at": _serialize_datetime(run.applied_at),
            **extra,
        }

    async def list_runs(
        self, workspace_id: str, datasource_id: int, actor_id: str, limit: int = 20,
    ) -> list[dict[str, Any]]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            rows = list((await session.execute(select(SemanticAccessBootstrapRunModel).where(
                SemanticAccessBootstrapRunModel.workspace_id == workspace_id,
                SemanticAccessBootstrapRunModel.datasource_id == datasource_id,
            ).order_by(SemanticAccessBootstrapRunModel.created_at.desc()).limit(
                min(max(int(limit), 1), 100),
            ))).scalars())
            return [self._run_payload(row) for row in rows]

    async def get_run(self, workspace_id: str, run_id: int, actor_id: str) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id)
            return self._run_payload(run)

    async def _run(self, session, workspace_id: str, run_id: int, *, lock: bool = False):
        query = select(SemanticAccessBootstrapRunModel).where(
            SemanticAccessBootstrapRunModel.id == run_id,
            SemanticAccessBootstrapRunModel.workspace_id == workspace_id,
        )
        if lock:
            query = query.with_for_update()
        run = (await session.execute(query)).scalar_one_or_none()
        if not run:
            raise SemanticAccessBootstrapError(
                "AI 首次配置运行不存在", code="run_not_found", status_code=404,
            )
        return run

    async def claim_next_run(
        self, *, worker_id: str, lease_timeout_sec: int, workspace_max_running: int = 1,
    ) -> Optional[dict[str, Any]]:
        now = datetime.now()
        db = get_async_db_manager()
        async with db.session_scope() as session:
            pending = list((await session.execute(select(SemanticAccessBootstrapRunModel).where(
                SemanticAccessBootstrapRunModel.status == "pending",
            ).order_by(SemanticAccessBootstrapRunModel.created_at).limit(25).with_for_update(
                skip_locked=True,
            ))).scalars())
            for run in pending:
                await session.execute(select(WorkspaceModel.id).where(
                    WorkspaceModel.id == run.workspace_id,
                ).with_for_update())
                running = len(list((await session.execute(select(
                    SemanticAccessBootstrapRunModel.id,
                ).where(
                    SemanticAccessBootstrapRunModel.workspace_id == run.workspace_id,
                    SemanticAccessBootstrapRunModel.status.in_(list(RUNNING_RUN_STATUSES)),
                ).with_for_update())).scalars()))
                if running >= max(1, int(workspace_max_running)):
                    continue
                run.status = "running"
                run.stage = "collecting_metadata"
                run.progress = 5
                run.worker_id = worker_id
                run.run_token = uuid.uuid4().hex
                run.lease_expires_at = now + timedelta(seconds=max(30, int(lease_timeout_sec)))
                run.heartbeat_at = now
                run.started_at = run.started_at or now
                run.completed_at = None
                run.error_message = None
                run.cancel_requested_at = None
                run.attempt = int(run.attempt or 0) + 1
                await session.flush()
                return {**self._run_payload(run), "run_token": run.run_token}
        return None

    async def heartbeat_run(
        self, run_id: int, *, worker_id: str, run_token: str, lease_timeout_sec: int,
    ) -> bool:
        db = get_async_db_manager()
        now = datetime.now()
        async with db.session_scope() as session:
            run = (await session.execute(select(SemanticAccessBootstrapRunModel).where(
                SemanticAccessBootstrapRunModel.id == run_id,
                SemanticAccessBootstrapRunModel.worker_id == worker_id,
                SemanticAccessBootstrapRunModel.run_token == run_token,
                SemanticAccessBootstrapRunModel.status.in_(list(RUNNING_RUN_STATUSES)),
            ))).scalar_one_or_none()
            if not run:
                return False
            run.heartbeat_at = now
            run.lease_expires_at = now + timedelta(seconds=max(30, int(lease_timeout_sec)))
            return True

    async def cancel_run(
        self, workspace_id: str, run_id: int, actor_id: str, expected_revision: int,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id, lock=True)
            self._assert_revision(run, expected_revision)
            now = datetime.now()
            if run.status == "pending":
                run.status = "cancelled"
                run.stage = "cancelled"
                run.progress = 100
                run.completed_at = now
            elif run.status == "running":
                run.status = "cancel_requested"
                run.stage = "cancelling"
            elif run.status in {"review_ready", "partial"}:
                run.status = "discarded"
                run.stage = "discarded"
                run.progress = 100
                run.completed_at = now
            elif run.status not in {"cancel_requested", "cancelled"}:
                raise SemanticAccessBootstrapError(
                    "当前运行不能取消", code="run_state_invalid", status_code=409,
                )
            run.cancel_requested_at = now
            run.revision += 1
            return self._run_payload(run)

    async def retry_run(
        self, workspace_id: str, run_id: int, actor_id: str, expected_revision: int,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id, lock=True)
            self._assert_revision(run, expected_revision)
            if run.status not in {"failed", "partial", "cancelled"}:
                raise SemanticAccessBootstrapError(
                    "当前运行不能重试", code="run_state_invalid", status_code=409,
                )
            if int(run.attempt or 0) >= int(run.max_attempts or 2):
                raise SemanticAccessBootstrapError(
                    "运行已达到最大重试次数", code="retry_limit_reached", status_code=409,
                )
            _datasource, tables, columns, _metrics, semantic_fingerprint = await self._governed_assets(
                session, workspace_id, run.datasource_id,
            )
            if semantic_fingerprint != run.schema_fingerprint:
                raise SemanticAccessBootstrapError(
                    "语义 Schema 已变化，请重新生成",
                    code="schema_changed",
                    status_code=409,
                )
            snapshot = dict(run.input_snapshot_json or {})
            snapshot["bootstrap_mode"] = BOOTSTRAP_MODE
            snapshot["eligible_table_ids"] = [int(row.id) for row in tables]
            snapshot["eligible_table_count"] = len(tables)
            snapshot["eligible_column_count"] = len(columns)
            snapshot["assets"] = _asset_snapshot(tables, columns)
            run.input_snapshot_json = snapshot
            await session.execute(delete(SemanticAccessBootstrapTargetModel).where(
                SemanticAccessBootstrapTargetModel.run_id == run.id,
            ))
            await session.execute(delete(SemanticAccessBootstrapMappingModel).where(
                SemanticAccessBootstrapMappingModel.run_id == run.id,
            ))
            run.status = "pending"
            run.stage = "queued"
            run.progress = 0
            run.error_message = None
            run.worker_id = None
            run.run_token = None
            run.lease_expires_at = None
            run.heartbeat_at = None
            run.cancel_requested_at = None
            run.completed_at = None
            run.revision += 1
            return self._run_payload(run)

    def _assert_revision(self, run, expected_revision: int) -> None:
        if int(run.revision or 0) != int(expected_revision):
            raise SemanticAccessBootstrapError(
                "审核方案已被其他管理员修改，请刷新后重试",
                code="bootstrap_revision_conflict",
                status_code=409,
                details={"current_revision": int(run.revision or 0)},
            )

    def _assert_worker_ownership(
        self, run, worker_id: str | None, run_token: str | None,
    ) -> None:
        if worker_id is None and run_token is None:
            return
        if (
            not worker_id
            or not run_token
            or run.worker_id != worker_id
            or run.run_token != run_token
            or run.status not in RUNNING_RUN_STATUSES
        ):
            raise SemanticAccessBootstrapError(
                "AI 首配 worker 已失去运行租约",
                code="worker_lease_lost",
                status_code=409,
            )

    async def mark_stale_runs_failed(self) -> int:
        now = datetime.now()
        db = get_async_db_manager()
        count = 0
        async with db.session_scope() as session:
            rows = list((await session.execute(select(SemanticAccessBootstrapRunModel).where(
                SemanticAccessBootstrapRunModel.status.in_(list(RUNNING_RUN_STATUSES)),
                SemanticAccessBootstrapRunModel.lease_expires_at.is_not(None),
                SemanticAccessBootstrapRunModel.lease_expires_at < now,
            ).with_for_update(skip_locked=True))).scalars())
            for run in rows:
                run.status = "failed"
                run.stage = "worker_lease_expired"
                run.progress = 100
                run.error_message = "AI 首配 worker 租约过期"
                run.completed_at = now
                run.worker_id = None
                run.run_token = None
                count += 1
        return count

    async def _cancel_requested(self, run_id: int) -> bool:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            status = (await session.execute(select(SemanticAccessBootstrapRunModel.status).where(
                SemanticAccessBootstrapRunModel.id == run_id,
            ))).scalar_one_or_none()
            return status == "cancel_requested"

    async def _set_run_state(
        self, run_id: int, *, stage: str, progress: int, status: str | None = None,
        summary: dict[str, Any] | None = None, error_message: str | None = None,
        worker_id: str | None = None, run_token: str | None = None,
    ) -> bool:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            run = await session.get(SemanticAccessBootstrapRunModel, run_id)
            if not run:
                return False
            try:
                self._assert_worker_ownership(run, worker_id, run_token)
            except SemanticAccessBootstrapError:
                logger.warning("access bootstrap state update ignored after lease loss: run=%s", run_id)
                return False
            run.stage = stage
            run.progress = max(0, min(100, int(progress)))
            if status:
                run.status = status
            if summary is not None:
                run.summary_json = summary
            if error_message is not None:
                run.error_message = error_message
            if status in TERMINAL_RUN_STATUSES | {"review_ready", "partial"}:
                run.completed_at = datetime.now()
            return True

    async def execute_run(
        self, run_id: int, *, worker_id: str | None = None, run_token: str | None = None,
    ) -> None:
        try:
            context = await self._generation_context(run_id, worker_id, run_token)
            if await self._cancel_requested(run_id):
                await self._set_run_state(
                    run_id, stage="cancelled", progress=100, status="cancelled",
                    worker_id=worker_id, run_token=run_token,
                )
                return
            await self._set_run_state(
                run_id, stage="generating_policies", progress=20,
                worker_id=worker_id, run_token=run_token,
            )
            classifications, failed_batches = await self._generate_classifications(
                run_id, context, worker_id, run_token,
            )
            await self._set_run_state(
                run_id, stage="generating_fields", progress=58,
                worker_id=worker_id, run_token=run_token,
            )
            field_classifications, failed_field_batches = await self._generate_field_classifications(
                run_id, context, classifications, worker_id, run_token,
            )
            await self._set_run_state(
                run_id, stage="composing_drafts", progress=82,
                worker_id=worker_id, run_token=run_token,
            )
            summary = await self._persist_generated_drafts(
                run_id, context, classifications, field_classifications, worker_id, run_token,
            )
            all_failures = [*failed_batches, *failed_field_batches]
            summary["failed_batches"] = all_failures
            if await self._cancel_requested(run_id):
                await self._set_run_state(
                    run_id, stage="cancelled", progress=100, status="cancelled",
                    worker_id=worker_id, run_token=run_token,
                )
                return
            status = (
                "failed" if failed_batches and not classifications
                else "partial" if all_failures
                else "review_ready"
            )
            await self._set_run_state(
                run_id,
                stage=status,
                progress=100,
                status=status,
                summary=summary,
                error_message=(
                    "AI 未生成任何有效表建议，请重试"
                    if status == "failed"
                    else "部分 AI 表或字段建议生成失败，请重试后再发布"
                    if status == "partial"
                    else None
                ),
                worker_id=worker_id,
                run_token=run_token,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("semantic access bootstrap run failed: run_id=%s", run_id, exc_info=True)
            await self._set_run_state(
                run_id,
                stage="failed",
                progress=100,
                status="failed",
                error_message=str(exc)[:2000],
                worker_id=worker_id,
                run_token=run_token,
            )

    async def _generation_context(
        self, run_id: int, worker_id: str | None = None, run_token: str | None = None,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            run = await session.get(SemanticAccessBootstrapRunModel, run_id)
            if not run:
                raise SemanticAccessBootstrapError("运行不存在", code="run_not_found", status_code=404)
            self._assert_worker_ownership(run, worker_id, run_token)
            datasource, tables, columns, metrics, semantic_fingerprint = await self._governed_assets(
                session, run.workspace_id, run.datasource_id,
            )
            departments, organization_fingerprint = await self._organization_snapshot(
                session, run.workspace_id,
            )
            if semantic_fingerprint != run.schema_fingerprint:
                raise SemanticAccessBootstrapError("语义 Schema 已变化，请重新生成", code="schema_changed", status_code=409)
            if organization_fingerprint != run.organization_fingerprint:
                raise SemanticAccessBootstrapError("组织架构已变化，请重新生成", code="organization_changed", status_code=409)
            relationships = list((await session.execute(select(SemanticRelationshipModel).where(
                SemanticRelationshipModel.workspace_id == run.workspace_id,
                SemanticRelationshipModel.datasource_id == run.datasource_id,
            ))).scalars())
            mappings = list((await session.execute(select(SemanticOwnershipMappingModel).where(
                SemanticOwnershipMappingModel.workspace_id == run.workspace_id,
                SemanticOwnershipMappingModel.datasource_id == run.datasource_id,
            ))).scalars())
            bindings = list((await session.execute(select(SemanticPolicyBindingModel).where(
                SemanticPolicyBindingModel.workspace_id == run.workspace_id,
                SemanticPolicyBindingModel.datasource_id == run.datasource_id,
            ))).scalars())
            binding_by_target = {(row.target_type, row.target_id): row for row in bindings}
            configured = {
                key for key, row in binding_by_target.items()
                if row.status and row.active_version_id
            }
            top_departments = self._eligible_top_departments(departments, configured)
            baseline_binding = binding_by_target.get(("baseline", "*"))
            baseline_enabled = ("baseline", "*") not in configured
            columns_by_table: dict[int, list[Any]] = {}
            for column in columns:
                columns_by_table.setdefault(int(column.table_id), []).append(column)
            metrics_by_table: dict[int, list[Any]] = {}
            for metric in metrics:
                metrics_by_table.setdefault(int(metric.table_id), []).append(metric)

            return {
                "run": run,
                "datasource": datasource,
                "tables": tables,
                "columns": columns,
                "columns_by_table": columns_by_table,
                "metrics_by_table": metrics_by_table,
                "metrics": metrics,
                "relationships": relationships,
                "mappings": {int(row.table_id): row for row in mappings},
                "top_departments": top_departments,
                "binding_by_target": binding_by_target,
                "baseline_enabled": baseline_enabled,
                "baseline_binding": baseline_binding,
                "configured_targets_skipped": [
                    {"target_type": kind, "target_id": target_id}
                    for kind, target_id in sorted(configured)
                    if kind in {"baseline", "org_unit"}
                ],
            }

    def _column_priority(self, column: Any) -> tuple[int, int]:
        text = " ".join([
            str(column.business_name or ""),
            str(column.physical_name or ""),
            str(column.description or ""),
        ]).lower()
        ownership = any(term in text for term in OWNERSHIP_TERMS)
        return (2 if column.is_sensitive else 1 if ownership else 0, -int(column.ordinal_position or 0))

    def _batch_payload(self, context: dict[str, Any], tables: list[Any]) -> dict[str, Any]:
        return {
            "business_context": context["run"].business_context or "",
            "departments": [{"id": row.id, "name": row.name, "code": row.code} for row in context["top_departments"]],
            "tables": [{
                "id": table.id,
                "business_name": table.business_name,
                "physical_name": table.physical_name,
                "description": table.description or table.physical_comment or "",
                "synonyms": table.synonyms or [],
                "is_sensitive": bool(table.is_sensitive),
                "governance_confidence": table.confidence,
                "columns": [{
                    "id": column.id,
                    "business_name": column.business_name,
                    "physical_name": column.physical_name,
                    "description": column.description or column.physical_comment or "",
                    "data_type": column.data_type,
                    "is_sensitive": bool(column.is_sensitive),
                } for column in sorted(
                    context["columns_by_table"].get(int(table.id), []),
                    key=self._column_priority,
                    reverse=True,
                )[:MAX_COLUMNS_PER_TABLE]],
            } for table in tables],
        }

    async def _generate_classifications(
        self,
        run_id: int,
        context: dict[str, Any],
        worker_id: str | None = None,
        run_token: str | None = None,
    ) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
        tables = context["tables"]
        results: dict[int, dict[str, Any]] = {}
        failures: list[dict[str, Any]] = []
        chunks = [tables[index:index + BATCH_SIZE] for index in range(0, len(tables), BATCH_SIZE)]
        for index, chunk in enumerate(chunks):
            if await self._cancel_requested(run_id):
                break
            pending = {int(row.id): row for row in chunk}
            validation_errors: dict[int, str] = {}
            for attempt in range(3):
                if not pending:
                    break
                attempt_chunk = list(pending.values())
                payload = self._batch_payload(context, attempt_chunk)
                try:
                    parsed = await self.llm.generate_json(
                        self._generation_messages(payload, validation_errors),
                        model=getattr(settings.llm, "fast_model", settings.llm.model),
                        temperature=0,
                        max_tokens=6000,
                    )
                    validated, validation_errors = self._validate_batch_partial(
                        parsed, attempt_chunk, context,
                    )
                    results.update(validated)
                    for table_id in validated:
                        pending.pop(table_id, None)
                    if validation_errors:
                        logger.warning(
                            "access bootstrap records invalid: run=%s batch=%s attempt=%s errors=%s",
                            run_id, index, attempt + 1, validation_errors,
                        )
                except Exception as exc:  # noqa: BLE001
                    validation_errors = {
                        table_id: str(exc)[:500] for table_id in pending
                    }
                    logger.warning(
                        "access bootstrap batch invalid: run=%s batch=%s attempt=%s err=%s",
                        run_id, index, attempt + 1, exc,
                    )
            if pending:
                failures.append({
                    "batch": index + 1,
                    "table_ids": sorted(pending),
                    "message": "；".join(
                        f"表 {table_id}: {validation_errors.get(table_id, '模型输出无效')}"
                        for table_id in sorted(pending)
                    )[:500],
                })
            await self._set_run_state(
                run_id,
                stage="generating_policies",
                progress=20 + int(35 * (index + 1) / max(len(chunks), 1)),
                worker_id=worker_id,
                run_token=run_token,
            )
        return results, failures

    def _field_batch_payload(
        self, context: dict[str, Any], table: Any, columns: list[Any],
    ) -> dict[str, Any]:
        targets = []
        if context["baseline_enabled"]:
            targets.append({"target_type": "baseline", "target_id": "*", "name": "全员基线"})
        targets.extend({
            "target_type": "org_unit",
            "target_id": str(row.id),
            "name": row.name,
        } for row in context["top_departments"])
        return {
            "business_context": context["run"].business_context or "",
            "table": {
                "id": int(table.id),
                "business_name": table.business_name,
                "physical_name": table.physical_name,
                "description": table.description or table.physical_comment or "",
                "is_sensitive": bool(table.is_sensitive),
            },
            "targets": targets,
            "columns": [{
                "id": int(column.id),
                "business_name": column.business_name,
                "physical_name": column.physical_name,
                "description": column.description or column.physical_comment or "",
                "data_type": column.data_type,
                "is_sensitive": bool(column.is_sensitive),
            } for column in columns],
        }

    def _field_generation_messages(
        self, payload: dict[str, Any], validation_errors: dict[int, str] | None = None,
    ) -> list[dict[str, str]]:
        correction = ""
        if validation_errors:
            correction = (
                "\n上次输出未通过校验，请逐字段修正以下错误，不要改动输入 ID："
                + json.dumps(validation_errors, ensure_ascii=False)
            )
        return [
            {
                "role": "system",
                "content": (
                    "你是企业问数的字段权限分析器。只根据治理元数据判断字段是否应随已授权表可见。"
                    "通用标识、名称、时间、状态、数量等非敏感字段通常可见；成本、内部控制、"
                    "专属业务或保密含义字段可对无关部门隐藏。同一字段可以对多个部门相同，也可通过"
                    "覆盖项产生差异。敏感字段必须隐藏。禁止推测不存在的 ID，只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "严格输出结构：{\"columns\":[{\"column_id\":1,"
                    "\"default_decision\":\"visible\",\"confidence\":0.0,\"reason\":\"\","
                    "\"target_overrides\":[{\"target_type\":\"org_unit\",\"target_id\":\"1\","
                    "\"decision\":\"hidden\",\"confidence\":0.0,\"reason\":\"\"}]}]}。"
                    "default_decision 和 decision 只能为 visible 或 hidden；每个输入字段必须且只能返回一次；"
                    "target_overrides 只返回与默认决定不同的授权对象，baseline 的 target_id 固定为 *。"
                    + correction + "\n\n" + json.dumps(payload, ensure_ascii=False)
                ),
            },
        ]

    def _field_target_keys(self, context: dict[str, Any]) -> set[tuple[str, str]]:
        keys = {
            ("org_unit", str(row.id)) for row in context["top_departments"]
        }
        if context["baseline_enabled"]:
            keys.add(("baseline", "*"))
        return keys

    def _validate_field_batch(
        self, parsed: Any, columns: list[Any], context: dict[str, Any], table_id: int,
    ) -> dict[int, dict[str, Any]]:
        if not isinstance(parsed, dict) or not isinstance(parsed.get("columns"), list):
            raise ValueError("模型没有返回有效 columns 数组")
        expected = {int(column.id) for column in columns}
        target_keys = self._field_target_keys(context)
        result: dict[int, dict[str, Any]] = {}
        for raw in parsed["columns"]:
            if not isinstance(raw, dict):
                raise ValueError(f"表 {table_id} 的字段建议必须是对象")
            column_id = raw.get("column_id")
            if isinstance(column_id, bool) or not isinstance(column_id, int):
                raise ValueError(f"表 {table_id} 的字段 ID 必须是整数")
            if column_id not in expected or column_id in result:
                raise ValueError(f"表 {table_id} 返回无效或重复字段 ID：{column_id}")
            default_decision = str(raw.get("default_decision") or "")
            if default_decision not in {"visible", "hidden"}:
                raise ValueError(f"字段 {column_id} 的默认决定无效")
            overrides: list[dict[str, Any]] = []
            seen_targets: set[tuple[str, str]] = set()
            if not isinstance(raw.get("target_overrides"), list):
                raise ValueError(f"字段 {column_id} 的 target_overrides 必须是数组")
            for override in raw["target_overrides"]:
                if not isinstance(override, dict):
                    raise ValueError(f"字段 {column_id} 的授权对象覆盖必须是对象")
                target_type = str(override.get("target_type") or "")
                target_id = str(override.get("target_id") or "")
                key = (target_type, target_id)
                if key not in target_keys:
                    raise ValueError(f"字段 {column_id} 返回无效授权对象：{target_type}:{target_id}")
                if key in seen_targets:
                    raise ValueError(f"字段 {column_id} 重复返回授权对象：{target_type}:{target_id}")
                seen_targets.add(key)
                decision = str(override.get("decision") or "")
                if decision not in {"visible", "hidden"}:
                    raise ValueError(f"字段 {column_id} 的覆盖决定无效")
                overrides.append({
                    "target_type": target_type,
                    "target_id": target_id,
                    "decision": decision,
                    "confidence": _strict_confidence(
                        override.get("confidence"), "字段覆盖置信度",
                    ),
                    "reason": str(override.get("reason") or "")[:500],
                })
            result[column_id] = {
                "column_id": column_id,
                "default_decision": default_decision,
                "confidence": _strict_confidence(raw.get("confidence"), "字段置信度"),
                "reason": str(raw.get("reason") or "")[:500],
                "target_overrides": overrides,
            }
        if set(result) != expected:
            raise ValueError(f"表 {table_id} 遗漏字段 ID：{sorted(expected - set(result))}")
        return result

    def _validate_field_batch_partial(
        self, parsed: Any, columns: list[Any], context: dict[str, Any], table_id: int,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
        if not isinstance(parsed, dict) or not isinstance(parsed.get("columns"), list):
            raise ValueError("模型没有返回有效 columns 数组")
        expected = {int(column.id): column for column in columns}
        grouped: dict[int, list[Any]] = {column_id: [] for column_id in expected}
        for raw in parsed["columns"]:
            if not isinstance(raw, dict):
                continue
            column_id = raw.get("column_id")
            if isinstance(column_id, int) and not isinstance(column_id, bool) and column_id in grouped:
                grouped[column_id].append(raw)
        valid: dict[int, dict[str, Any]] = {}
        errors: dict[int, str] = {}
        for column_id, column in expected.items():
            rows = grouped[column_id]
            if not rows:
                errors[column_id] = "模型遗漏该字段"
                continue
            if len(rows) > 1:
                errors[column_id] = "模型重复返回该字段"
                continue
            try:
                valid.update(self._validate_field_batch(
                    {"columns": rows}, [column], context, table_id,
                ))
            except Exception as exc:  # noqa: BLE001
                errors[column_id] = str(exc)[:500]
        return valid, errors

    async def _generate_field_classifications(
        self,
        run_id: int,
        context: dict[str, Any],
        classifications: dict[int, dict[str, Any]],
        worker_id: str | None = None,
        run_token: str | None = None,
    ) -> tuple[dict[int, dict[int, dict[str, Any]]], list[dict[str, Any]]]:
        tables = [table for table in context["tables"] if int(table.id) in classifications]
        batches: list[tuple[Any, list[Any]]] = []
        for table in tables:
            columns = sorted(
                context["columns_by_table"].get(int(table.id), []),
                key=lambda column: int(column.ordinal_position or 0),
            )
            batches.extend(
                (table, columns[index:index + FIELD_BATCH_SIZE])
                for index in range(0, len(columns), FIELD_BATCH_SIZE)
            )
        results: dict[int, dict[int, dict[str, Any]]] = {
            int(table.id): {} for table in tables
        }
        failures: list[dict[str, Any]] = []
        for index, (table, batch) in enumerate(batches):
            if await self._cancel_requested(run_id):
                break
            table_id = int(table.id)
            pending = {int(column.id): column for column in batch}
            validation_errors: dict[int, str] = {}
            for attempt in range(3):
                if not pending:
                    break
                attempt_columns = list(pending.values())
                try:
                    parsed = await self.llm.generate_json(
                        self._field_generation_messages(
                            self._field_batch_payload(context, table, attempt_columns),
                            validation_errors,
                        ),
                        model=getattr(settings.llm, "fast_model", settings.llm.model),
                        temperature=0,
                        max_tokens=10000,
                    )
                    validated, validation_errors = self._validate_field_batch_partial(
                        parsed, attempt_columns, context, table_id,
                    )
                    results[table_id].update(validated)
                    for column_id in validated:
                        pending.pop(column_id, None)
                except Exception as exc:  # noqa: BLE001
                    validation_errors = {
                        column_id: str(exc)[:500] for column_id in pending
                    }
                    logger.warning(
                        "access bootstrap field batch invalid: run=%s table=%s batch=%s attempt=%s err=%s",
                        run_id, table_id, index + 1, attempt + 1, exc,
                    )
            if pending:
                failures.append({
                    "kind": "field",
                    "batch": index + 1,
                    "table_id": table_id,
                    "column_ids": sorted(pending),
                    "message": "；".join(
                        f"字段 {column_id}: {validation_errors.get(column_id, '模型输出无效')}"
                        for column_id in sorted(pending)
                    )[:500],
                })
            await self._set_run_state(
                run_id,
                stage="generating_fields",
                progress=58 + int(22 * (index + 1) / max(len(batches), 1)),
                worker_id=worker_id,
                run_token=run_token,
            )
        return results, failures

    def _generation_messages(
        self, payload: dict[str, Any], validation_errors: dict[int, str] | None = None,
    ) -> list[dict[str, str]]:
        correction = ""
        if validation_errors:
            correction = (
                "\n上次输出未通过校验，请逐表修正以下错误，不要改动输入 ID："
                + json.dumps(validation_errors, ensure_ascii=False)
            )
        return [
            {
                "role": "system",
                "content": (
                    "你是企业问数数据权限分析器。只根据提供的治理元数据判断公共表、"
                    "一级部门业务相关性、跨部门公共业务表和全局字典表。禁止推测不存在的 ID，"
                    "禁止输出 SQL。敏感表不能作为全员或全部门公共表。应适度放宽业务相关性判断，"
                    "同一张表可以与多个部门相关。只输出 JSON，所有置信度范围为 0 到 1。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "严格输出结构：{\"tables\":[{\"table_id\":1,"
                    "\"shared\":false,\"shared_confidence\":0.0,"
                    "\"global_reference\":false,\"global_reference_confidence\":0.0,"
                    "\"all_departments\":false,\"all_departments_confidence\":0.0,"
                    "\"reason\":\"\",\"department_access\":[{\"org_unit_id\":1,"
                    "\"confidence\":0.0,\"reason\":\"\"}]}]}。每张输入表必须且只能返回一次；"
                    "department_access 必须把输入中的每个一级部门各返回一次，即使不相关也要返回低置信度；"
                    "all_departments 只用于非敏感、跨部门共用且不等同于全员基线的表。"
                    + correction + "\n\n"
                    + json.dumps(payload, ensure_ascii=False)
                ),
            },
        ]

    def _validate_batch_partial(
        self, parsed: Any, chunk: list[Any], context: dict[str, Any],
    ) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
        if not isinstance(parsed, dict) or not isinstance(parsed.get("tables"), list):
            raise ValueError("模型没有返回有效 tables 数组")
        expected = {int(row.id): row for row in chunk}
        grouped: dict[int, list[Any]] = {table_id: [] for table_id in expected}
        for raw in parsed["tables"]:
            if not isinstance(raw, dict):
                continue
            table_id = raw.get("table_id")
            if isinstance(table_id, int) and not isinstance(table_id, bool) and table_id in grouped:
                grouped[table_id].append(raw)
        valid: dict[int, dict[str, Any]] = {}
        errors: dict[int, str] = {}
        for table_id, table in expected.items():
            rows = grouped[table_id]
            if not rows:
                errors[table_id] = "模型遗漏该表"
                continue
            if len(rows) > 1:
                errors[table_id] = "模型重复返回该表"
                continue
            try:
                valid.update(self._validate_batch({"tables": rows}, [table], context))
            except Exception as exc:  # noqa: BLE001
                errors[table_id] = str(exc)[:500]
        return valid, errors

    def _validate_batch(
        self, parsed: Any, chunk: list[Any], context: dict[str, Any],
    ) -> dict[int, dict[str, Any]]:
        if not isinstance(parsed, dict) or not isinstance(parsed.get("tables"), list):
            raise ValueError("模型没有返回有效 tables 数组")
        expected = {int(row.id) for row in chunk}
        department_ids = {int(row.id) for row in context["top_departments"]}
        result: dict[int, dict[str, Any]] = {}
        for raw in parsed["tables"]:
            if not isinstance(raw, dict):
                raise ValueError("表分类必须是对象")
            if isinstance(raw.get("table_id"), bool) or not isinstance(raw.get("table_id"), int):
                raise ValueError("模型返回的表 ID 必须是整数")
            table_id = raw["table_id"]
            if table_id not in expected or table_id in result:
                raise ValueError(f"模型返回无效或重复表 ID：{table_id}")
            if not isinstance(raw.get("shared"), bool):
                raise ValueError(f"表 {table_id} 的 shared 必须是布尔值")
            if not isinstance(raw.get("global_reference"), bool):
                raise ValueError(f"表 {table_id} 的 global_reference 必须是布尔值")
            if not isinstance(raw.get("all_departments"), bool):
                raise ValueError(f"表 {table_id} 的 all_departments 必须是布尔值")
            if not isinstance(raw.get("department_access"), list):
                raise ValueError(f"表 {table_id} 的 department_access 必须是数组")
            departments = []
            seen_department_ids: set[int] = set()
            for item in raw["department_access"]:
                if not isinstance(item, dict):
                    raise ValueError(f"表 {table_id} 的部门建议必须是对象")
                if isinstance(item.get("org_unit_id"), bool) or not isinstance(item.get("org_unit_id"), int):
                    raise ValueError(f"表 {table_id} 的一级部门 ID 必须是整数")
                org_id = item["org_unit_id"]
                if org_id not in department_ids:
                    raise ValueError(f"模型返回无效一级部门 ID：{org_id}")
                if org_id in seen_department_ids:
                    raise ValueError(f"模型重复返回一级部门 ID：{org_id}")
                seen_department_ids.add(org_id)
                departments.append({
                    "org_unit_id": org_id,
                    "confidence": _strict_confidence(item.get("confidence"), "部门置信度"),
                    "reason": str(item.get("reason") or "")[:500],
                })
            if seen_department_ids != department_ids:
                raise ValueError(
                    f"表 {table_id} 必须返回所有一级部门，遗漏：{sorted(department_ids - seen_department_ids)}"
                )
            result[table_id] = {
                "table_id": table_id,
                "shared": raw["shared"],
                "shared_confidence": _strict_confidence(
                    raw.get("shared_confidence"), "共享置信度",
                ),
                "global_reference": raw["global_reference"],
                "global_reference_confidence": _strict_confidence(
                    raw.get("global_reference_confidence", 0.0), "全局字典置信度",
                ),
                "all_departments": raw["all_departments"],
                "all_departments_confidence": _strict_confidence(
                    raw.get("all_departments_confidence", 0.0), "全部门置信度",
                ),
                "reason": str(raw.get("reason") or "")[:500],
                "department_access": departments,
            }
        if set(result) != expected:
            raise ValueError(f"模型遗漏表 ID：{sorted(expected - set(result))}")
        return result

    def _candidate_decision(
        self,
        *,
        table: Any,
        item: dict[str, Any],
        target_type: str,
        target_id: str,
        has_mapping: bool,
        has_mapping_proposal: bool,
    ) -> dict[str, Any]:
        """Apply the fail-closed bootstrap rules to one table/target pair."""
        confidence = 0.0
        reason = str(item.get("reason") or "")
        requires_mapping = False
        if target_type == "baseline":
            confidence = _clamp_confidence(item.get("shared_confidence"))
            return {
                "selected": bool(
                    item.get("shared")
                    and confidence >= HIGH_CONFIDENCE
                    and not bool(table.is_sensitive)
                ),
                "confidence": confidence,
                "reason": reason,
                "row_scope": "all",
                "requires_mapping": False,
            }

        department_match = next((
            row for row in item.get("department_access") or []
            if str(row.get("org_unit_id")) == str(target_id)
        ), None)
        if department_match:
            confidence = _clamp_confidence(department_match.get("confidence"))
            reason = str(department_match.get("reason") or reason)
        all_departments_confidence = max(
            _clamp_confidence(item.get("all_departments_confidence")),
            _clamp_confidence(item.get("global_reference_confidence")),
        )
        if (
            (item.get("all_departments") or item.get("global_reference"))
            and all_departments_confidence >= HIGH_CONFIDENCE
            and not bool(table.is_sensitive)
        ):
            confidence = all_departments_confidence
            selected = True
        else:
            selected = bool(
                department_match
                and confidence >= HIGH_CONFIDENCE
            )
        return {
            "selected": selected,
            "confidence": confidence,
            "reason": reason,
            "row_scope": "all",
            "requires_mapping": False,
        }

    def _sensitive_asset_exceptions(
        self, context: dict[str, Any], table_id: int,
    ) -> tuple[list[int], list[int]]:
        hidden_columns = [
            int(column.id)
            for column in context["columns_by_table"].get(table_id, [])
            if bool(column.is_sensitive)
        ]
        hidden_metrics = [
            int(metric.id)
            for metric in context["metrics_by_table"].get(table_id, [])
            if bool(metric.is_sensitive)
        ]
        return hidden_columns, hidden_metrics

    def _field_suggestions_for_target(
        self,
        context: dict[str, Any],
        table_id: int,
        target_type: str,
        target_id: str,
        classifications: dict[int, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[int], dict[str, int]]:
        suggestions: list[dict[str, Any]] = []
        hidden_column_ids: list[int] = []
        summary = {
            "visible": 0,
            "hidden": 0,
            "sensitive_hidden": 0,
            "attention": 0,
        }
        for column in sorted(
            context["columns_by_table"].get(table_id, []),
            key=lambda row: int(row.ordinal_position or 0),
        ):
            column_id = int(column.id)
            raw = classifications.get(column_id)
            if bool(column.is_sensitive):
                decision = "hidden"
                effective_decision = "hidden"
                confidence = 1.0
                reason = "字段在语义治理中被标记为敏感，AI 首配强制隐藏"
                source = "governance"
            elif raw is None:
                decision = "hidden"
                effective_decision = "hidden"
                confidence = 0.0
                reason = "字段建议生成缺失，保持保守隐藏"
                source = "fallback"
            else:
                selected = raw
                for override in raw.get("target_overrides") or []:
                    if (
                        override.get("target_type") == target_type
                        and str(override.get("target_id")) == str(target_id)
                    ):
                        selected = override
                        break
                decision = str(selected.get("decision") or selected.get("default_decision") or "visible")
                confidence = _clamp_confidence(selected.get("confidence"))
                reason = str(selected.get("reason") or raw.get("reason") or "")
                source = "ai"
                # Non-sensitive fields inherit visibility unless the model is
                # confident enough to create a target-specific hidden exception.
                effective_decision = (
                    "hidden"
                    if decision == "hidden" and confidence >= HIGH_CONFIDENCE
                    else "visible"
                )
            confidence_level = (
                "high" if confidence >= HIGH_CONFIDENCE
                else "medium" if confidence >= REVIEW_CONFIDENCE else "low"
            )
            if effective_decision == "hidden":
                hidden_column_ids.append(column_id)
                summary["hidden"] += 1
            else:
                summary["visible"] += 1
            if bool(column.is_sensitive):
                summary["sensitive_hidden"] += 1
            if source == "fallback" or (
                source == "ai" and decision != effective_decision
            ):
                summary["attention"] += 1
            suggestions.append({
                "column_id": column_id,
                "decision": decision,
                "effective_decision": effective_decision,
                "confidence": confidence,
                "confidence_level": confidence_level,
                "reason": reason[:500],
                "source": source,
                "review_state": "pending",
            })
        return suggestions, hidden_column_ids, summary

    async def _persist_generated_drafts(
        self,
        run_id: int,
        context: dict[str, Any],
        classifications: dict[int, dict[str, Any]],
        field_classifications: dict[int, dict[int, dict[str, Any]]],
        worker_id: str | None = None,
        run_token: str | None = None,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            run = await session.get(SemanticAccessBootstrapRunModel, run_id)
            if not run:
                raise SemanticAccessBootstrapError("运行不存在", code="run_not_found")
            self._assert_worker_ownership(run, worker_id, run_token)
            await session.execute(delete(SemanticAccessBootstrapTargetModel).where(
                SemanticAccessBootstrapTargetModel.run_id == run_id,
            ))
            await session.execute(delete(SemanticAccessBootstrapMappingModel).where(
                SemanticAccessBootstrapMappingModel.run_id == run_id,
            ))
            target_specs: list[tuple[str, str, str, bool, Any]] = []
            if context["baseline_enabled"]:
                target_specs.append(("baseline", "*", "全员基线", False, context["baseline_binding"]))
            for department in context["top_departments"]:
                target_specs.append((
                    "org_unit", str(department.id), department.name, True,
                    context["binding_by_target"].get(("org_unit", str(department.id))),
                ))

            targets: list[SemanticAccessBootstrapTargetModel] = []
            for target_type, target_id, label, include_descendants, binding in target_specs:
                rules: list[dict[str, Any]] = []
                candidates: list[dict[str, Any]] = []
                explanations: list[dict[str, Any]] = []
                confidences: list[float] = []
                for table in context["tables"]:
                    table_id = int(table.id)
                    item = classifications.get(table_id)
                    if item is None:
                        continue
                    field_suggestions, hidden_columns, field_summary = (
                        self._field_suggestions_for_target(
                            context,
                            table_id,
                            target_type,
                            target_id,
                            field_classifications.get(table_id, {}),
                        )
                    )
                    _, hidden_metrics = self._sensitive_asset_exceptions(
                        context, table_id,
                    )
                    decision = self._candidate_decision(
                        table=table,
                        item=item,
                        target_type=target_type,
                        target_id=target_id,
                        has_mapping=False,
                        has_mapping_proposal=False,
                    )
                    selected = bool(decision["selected"])
                    confidence = float(decision["confidence"])
                    scope = decision["row_scope"]
                    reason = decision["reason"]
                    requires_mapping = bool(decision["requires_mapping"])
                    candidate = {
                        "table_id": table_id,
                        "selected": selected,
                        "review_state": "pending",
                        "confidence": confidence,
                        "confidence_level": (
                            "high" if confidence >= HIGH_CONFIDENCE
                            else "medium" if confidence >= REVIEW_CONFIDENCE else "low"
                        ),
                        "reason": reason,
                        "row_scope": scope,
                        "requires_mapping": requires_mapping,
                        "has_mapping_proposal": False,
                        "sensitive": bool(table.is_sensitive),
                        "hidden_column_ids": hidden_columns,
                        "hidden_metric_ids": hidden_metrics,
                        "field_suggestions": field_suggestions,
                        "field_summary": field_summary,
                    }
                    candidates.append(candidate)
                    if selected:
                        rules.append({
                            "table_id": table_id,
                            "decision": "visible",
                            "hidden_column_ids": hidden_columns,
                            "hidden_metric_ids": hidden_metrics,
                            "row_scope": {"type": scope},
                        })
                        confidences.append(confidence)
                        explanations.append({
                            "table_id": table_id,
                            "confidence": confidence,
                            "reason": reason,
                        })
                target = SemanticAccessBootstrapTargetModel(
                    workspace_id=run.workspace_id,
                    datasource_id=run.datasource_id,
                    run_id=run.id,
                    target_type=target_type,
                    target_id=target_id,
                    target_label=label,
                    include_descendants=include_descendants,
                    base_binding_id=binding.id if binding else None,
                    base_revision=int(binding.revision or 0) if binding else 0,
                    included=bool(rules),
                    status="proposed" if rules else "empty",
                    confidence=(sum(confidences) / len(confidences) if confidences else 0.0),
                    definition_json={"name": label, "tables": rules},
                    candidates_json=candidates,
                    explanation_json=explanations,
                    validation_json={"blockers": [], "warnings": []},
                )
                session.add(target)
                targets.append(target)
            await session.flush()
            await self._revalidate_targets(session, run, targets)
            run.revision += 1
            summary = {
                "target_count": len(targets),
                "included_target_count": sum(1 for row in targets if row.included),
                "mapping_suggestion_count": 0,
                "selected_table_rule_count": sum(
                    len((row.definition_json or {}).get("tables") or []) for row in targets
                ),
                "configured_targets_skipped": context["configured_targets_skipped"],
                "high_confidence_threshold": HIGH_CONFIDENCE,
                "review_confidence_threshold": REVIEW_CONFIDENCE,
                "metadata_only": True,
                "bootstrap_mode": BOOTSTRAP_MODE,
                "row_level_configured": False,
            }
            run.summary_json = summary
            return summary

    def _mapping_overrides(self, mappings: list[SemanticAccessBootstrapMappingModel]) -> dict[int, dict[str, Any]]:
        return {
            int(row.table_id): dict(row.proposed_mapping_json or {})
            for row in mappings if row.accepted and row.status == "accepted"
        }

    async def _revalidate_targets(
        self,
        session,
        run: SemanticAccessBootstrapRunModel,
        targets: list[SemanticAccessBootstrapTargetModel] | None = None,
    ) -> None:
        from app.services.semantic_access_policy_service import get_semantic_access_policy_service

        if targets is None:
            targets = list((await session.execute(select(SemanticAccessBootstrapTargetModel).where(
                SemanticAccessBootstrapTargetModel.run_id == run.id,
            ))).scalars())
        mappings = list((await session.execute(select(SemanticAccessBootstrapMappingModel).where(
            SemanticAccessBootstrapMappingModel.run_id == run.id,
        ))).scalars())
        mapping_overrides = self._mapping_overrides(mappings)
        pending_mapping_table_ids = [
            int(row.table_id) for row in mappings if row.status == "proposed"
        ]
        datasource = await session.get(SemanticDatasourceModel, run.datasource_id)
        access_service = get_semantic_access_policy_service()
        for target in targets:
            validation_metadata = {
                key: value
                for key, value in (target.validation_json or {}).items()
                if key not in {"blockers", "warnings"}
            }
            if not target.included:
                target.validation_json = {
                    "blockers": [],
                    "warnings": [],
                    **validation_metadata,
                }
                continue
            definition = dict(target.definition_json or {})
            if not definition.get("tables"):
                target.validation_json = {
                    "blockers": [{"code": "tables_empty", "message": "已包含目标至少需要一张表"}],
                    "warnings": [],
                    **validation_metadata,
                }
                continue
            candidate_by_table = {
                int(row.get("table_id") or 0): row
                for row in (target.candidates_json or [])
            }
            pending_table_ids = [
                int(rule["table_id"])
                for rule in definition.get("tables") or []
                if candidate_by_table.get(int(rule["table_id"]))
                and candidate_by_table[int(rule["table_id"])].get("review_state", "pending") == "pending"
            ]
            review_blockers = ([{
                "code": "review_pending",
                "message": f"{len(pending_table_ids)} 条 AI 推荐尚未接受",
                "table_ids": pending_table_ids,
            }] if pending_table_ids else [])
            if target.target_type == "baseline" and pending_mapping_table_ids:
                review_blockers.append({
                    "code": "ownership_mapping_review_pending",
                    "message": f"{len(pending_mapping_table_ids)} 个行归属候选尚未确认或拒绝",
                    "table_ids": pending_mapping_table_ids,
                })
            binding = SimpleNamespace(
                id=target.base_binding_id,
                workspace_id=run.workspace_id,
                datasource_id=run.datasource_id,
                target_type=target.target_type,
                target_id=target.target_id,
                include_descendants=target.include_descendants,
            )
            prepared, ownership_blockers = await _prepare_definition_v3(
                session,
                binding,
                definition,
                run.triggered_by,
                mapping_overrides=mapping_overrides,
            )
            compilation = await access_service._compile_definition(
                session,
                run.workspace_id,
                run.datasource_id,
                prepared,
                schema_fingerprint=getattr(datasource, "schema_fingerprint", None),
            )
            target.validation_json = {
                "blockers": (
                    review_blockers
                    + ownership_blockers
                    + list(compilation["validation"].get("blockers") or [])
                ),
                "warnings": list(compilation["validation"].get("warnings") or []),
                **validation_metadata,
            }

    async def materialize_evidence_draft(
        self,
        workspace_id: str,
        evidence_set_id: int,
        actor_id: str,
    ) -> dict[str, Any]:
        """Idempotently project an evidence set into the direct-policy draft model."""
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            evidence_set = await session.get(SemanticAccessEvidenceSetModel, evidence_set_id)
            if not evidence_set or evidence_set.workspace_id != workspace_id:
                raise SemanticAccessBootstrapError(
                    "访问依据集不存在",
                    code="evidence_set_not_found",
                    status_code=404,
                )
            if evidence_set.status not in {"review_ready", "publish_failed", "published"}:
                raise SemanticAccessBootstrapError(
                    "访问依据尚未完成，暂时不能生成可编辑草稿",
                    code="evidence_set_not_ready",
                    status_code=409,
                )
            await session.execute(select(SemanticDatasourceModel.id).where(
                SemanticDatasourceModel.id == evidence_set.datasource_id,
                SemanticDatasourceModel.workspace_id == workspace_id,
            ).with_for_update())
            existing = (await session.execute(select(
                SemanticAccessBootstrapRunModel,
            ).where(
                SemanticAccessBootstrapRunModel.workspace_id == workspace_id,
                SemanticAccessBootstrapRunModel.evidence_set_id == evidence_set.id,
            ).order_by(
                SemanticAccessBootstrapRunModel.created_at.desc(),
            ).with_for_update())).scalars().first()
            if existing:
                if existing.status == "review_ready":
                    existing_targets = list((await session.execute(select(
                        SemanticAccessBootstrapTargetModel,
                    ).where(
                        SemanticAccessBootstrapTargetModel.run_id == existing.id,
                    ).with_for_update())).scalars())
                    definitions_changed = False
                    for target in existing_targets:
                        normalized = self._normalize_draft_definition(
                            target.definition_json or {},
                        )
                        if normalized != (target.definition_json or {}):
                            target.definition_json = normalized
                            definitions_changed = True
                    existing_mappings = list((await session.execute(select(
                        SemanticAccessBootstrapMappingModel,
                    ).where(
                        SemanticAccessBootstrapMappingModel.run_id == existing.id,
                        SemanticAccessBootstrapMappingModel.status == "proposed",
                    ).with_for_update())).scalars())
                    embedded_count = 0
                    for mapping in existing_mappings:
                        embedded = self._embed_mapping_row_scope_suggestion(
                            existing_targets,
                            mapping,
                            actor_id=actor_id,
                        )
                        mapping.status = "embedded" if embedded else "skipped"
                        mapping.edited_by = actor_id
                        definitions_changed = True
                        embedded_count += int(embedded)
                    if existing_mappings:
                        existing.summary_json = {
                            **dict(existing.summary_json or {}),
                            "row_ownership_embedded_count": embedded_count,
                            "row_level_configured": embedded_count > 0,
                        }
                    await self._revalidate_targets(session, existing, existing_targets)
                    if definitions_changed:
                        existing.revision += 1
                return self._run_payload(existing, coalesced=True)

            datasource, tables, columns, metrics, semantic_fingerprint = (
                await self._governed_assets(
                    session, workspace_id, int(evidence_set.datasource_id),
                )
            )
            departments, organization_fingerprint = await self._organization_snapshot(
                session, workspace_id,
            )
            top_departments = [
                row for row in departments
                if row.parent_id is None and bool(row.status)
            ]
            assets = list((await session.execute(select(
                SemanticAccessEvidenceAssetModel,
            ).where(
                SemanticAccessEvidenceAssetModel.evidence_set_id == evidence_set.id,
            ))).scalars())
            relations = list((await session.execute(select(
                SemanticAccessEvidenceRelationModel,
            ).where(
                SemanticAccessEvidenceRelationModel.evidence_set_id == evidence_set.id,
            ))).scalars())
            table_assets = {
                int(row.table_id): row
                for row in assets
                if row.asset_type == "table"
            }
            table_relations = {
                (int(row.org_unit_id), int(row.table_id)): row
                for row in relations
                if row.asset_type == "table"
            }
            field_relations = {
                (int(row.org_unit_id), int(row.table_id), int(row.asset_id)): row
                for row in relations
                if row.asset_type == "column"
            }
            columns_by_table: dict[int, list[Any]] = {}
            metrics_by_table: dict[int, list[Any]] = {}
            for column in columns:
                columns_by_table.setdefault(int(column.table_id), []).append(column)
            for metric in metrics:
                metrics_by_table.setdefault(int(metric.table_id), []).append(metric)

            row_ownership_summary = _row_ownership_generation_summary(
                evidence_set.generation_summary_json,
            )
            valid_table_ids = {int(row.id) for row in tables}
            valid_column_ids_by_table = {
                (int(row.table_id), int(row.id)) for row in columns
            }
            ownership_candidates = {
                int(row["table_id"]): row
                for row in (row_ownership_summary.get("candidates") or [])
                if (
                    isinstance(row, dict)
                    and int(row.get("table_id") or 0) in valid_table_ids
                    and (
                        int(row.get("table_id") or 0),
                        int((row.get("proposed_mapping") or {}).get("org_column_id") or 0),
                    ) in valid_column_ids_by_table
                )
            }
            existing_mappings = list((await session.execute(select(
                SemanticOwnershipMappingModel,
            ).where(
                SemanticOwnershipMappingModel.workspace_id == workspace_id,
                SemanticOwnershipMappingModel.datasource_id == evidence_set.datasource_id,
                SemanticOwnershipMappingModel.table_id.in_(list(ownership_candidates) or [-1]),
            ))).scalars())
            existing_mapping_by_table = {
                int(row.table_id): row for row in existing_mappings
            }

            run = SemanticAccessBootstrapRunModel(
                workspace_id=workspace_id,
                datasource_id=evidence_set.datasource_id,
                evidence_set_id=evidence_set.id,
                status="review_ready" if evidence_set.status != "published" else "applied",
                stage="direct_draft" if evidence_set.status != "published" else "applied",
                progress=100,
                revision=0,
                schema_fingerprint=semantic_fingerprint,
                organization_fingerprint=organization_fingerprint,
                input_snapshot_json={
                    "bootstrap_mode": "table_field_row_v1",
                    "evidence_set_id": evidence_set.id,
                    "evidence_set_revision": evidence_set.revision,
                    "assets": _asset_snapshot(tables, columns),
                    "metadata_only": False,
                },
                summary_json={},
                triggered_by=actor_id,
                started_at=datetime.now(),
                completed_at=datetime.now(),
                applied_at=evidence_set.published_at,
            )
            session.add(run)
            await session.flush()

            target_specs: list[tuple[str, str, str, bool, int | None]] = [
                ("baseline", "*", "全员基线", False, None),
                *[
                    ("org_unit", str(row.id), row.name, True, int(row.id))
                    for row in top_departments
                ],
            ]
            targets: list[SemanticAccessBootstrapTargetModel] = []
            low_confidence_table_count = 0
            low_confidence_field_count = 0
            sensitive_table_count = sum(bool(row.is_sensitive) for row in tables)
            sensitive_field_count = sum(bool(row.is_sensitive) for row in columns)

            for target_type, target_id, label, include_descendants, org_id in target_specs:
                binding = (await session.execute(select(
                    SemanticPolicyBindingModel,
                ).where(
                    SemanticPolicyBindingModel.workspace_id == workspace_id,
                    SemanticPolicyBindingModel.datasource_id == evidence_set.datasource_id,
                    SemanticPolicyBindingModel.target_type == target_type,
                    SemanticPolicyBindingModel.target_id == target_id,
                ))).scalar_one_or_none()
                rules: list[dict[str, Any]] = []
                candidates: list[dict[str, Any]] = []
                explanations: list[dict[str, Any]] = []
                target_confidences: list[float] = []

                for table in tables:
                    table_id = int(table.id)
                    asset = table_assets.get(table_id)
                    relation = (
                        table_relations.get((int(org_id), table_id))
                        if org_id is not None else None
                    )
                    if target_type == "baseline":
                        visible = bool(
                            asset
                            and _evidence_baseline_access(asset) == "workspace_visible"
                            and not bool(table.is_sensitive)
                        )
                        table_confidence = 1.0 if not visible else 0.60
                        table_reason = (
                            getattr(asset, "reason", None)
                            or "访问依据未建议全员开放，保持默认拒绝"
                        )
                        row_scope = {"type": "all"}
                    else:
                        access_level = _evidence_table_access(relation) if relation else "hidden"
                        visible = access_level in {"visible", "partial"}
                        table_confidence = _clamp_confidence(
                            getattr(relation, "confidence", 0.0),
                        )
                        table_reason = (
                            getattr(relation, "reason", None)
                            or "没有匹配的部门访问依据，保持默认拒绝"
                        )
                        row_scope = (
                            dict(getattr(relation, "row_scope_json", None) or {"type": "all"})
                            if access_level == "partial" else {"type": "all"}
                        )
                    if table_confidence < HIGH_CONFIDENCE:
                        low_confidence_table_count += 1

                    hidden_column_ids: list[int] = []
                    field_suggestions: list[dict[str, Any]] = []
                    for column in sorted(
                        columns_by_table.get(table_id, []),
                        key=lambda row: (int(row.ordinal_position or 0), int(row.id)),
                    ):
                        field_relation = (
                            field_relations.get((int(org_id), table_id, int(column.id)))
                            if org_id is not None else None
                        )
                        if bool(column.is_sensitive):
                            field_decision = "hidden"
                            field_confidence = 1.0
                            field_reason = "字段已在语义治理中标记为敏感，首次权限默认隐藏"
                            field_source = "governance"
                        elif field_relation is not None:
                            field_decision = _evidence_field_access(field_relation)
                            field_confidence = _clamp_confidence(field_relation.confidence)
                            field_reason = field_relation.reason or table_reason
                            field_source = "evidence"
                        else:
                            field_decision = "visible"
                            field_confidence = table_confidence
                            field_reason = "未配置字段例外，沿用表级访问依据"
                            field_source = "table"
                        if field_decision == "hidden":
                            hidden_column_ids.append(int(column.id))
                        if field_confidence < HIGH_CONFIDENCE:
                            low_confidence_field_count += 1
                        field_suggestions.append({
                            "column_id": int(column.id),
                            "decision": field_decision,
                            "effective_decision": (
                                field_decision if visible else "hidden"
                            ),
                            "confidence": field_confidence,
                            "confidence_level": (
                                "high" if field_confidence >= HIGH_CONFIDENCE
                                else "medium" if field_confidence >= REVIEW_CONFIDENCE
                                else "low"
                            ),
                            "reason": field_reason[:500],
                            "source": field_source,
                            "review_state": "accepted",
                        })

                    hidden_metric_ids = [
                        int(metric.id)
                        for metric in metrics_by_table.get(table_id, [])
                        if bool(metric.is_sensitive)
                    ]
                    rule = {
                        "table_id": table_id,
                        "decision": "visible" if visible else "hidden",
                        "hidden_column_ids": hidden_column_ids,
                        "hidden_metric_ids": hidden_metric_ids,
                    }
                    if visible:
                        rule["row_scope"] = row_scope
                    rules.append(rule)
                    candidates.append({
                        "table_id": table_id,
                        "selected": visible,
                        "decision": rule["decision"],
                        "review_state": "accepted",
                        "confidence": table_confidence,
                        "confidence_level": (
                            "high" if table_confidence >= HIGH_CONFIDENCE
                            else "medium" if table_confidence >= REVIEW_CONFIDENCE
                            else "low"
                        ),
                        "reason": table_reason[:500],
                        "row_scope": row_scope,
                        "requires_mapping": row_scope.get("type") != "all",
                        "has_mapping_proposal": table_id in ownership_candidates,
                        "sensitive": bool(table.is_sensitive),
                        "hidden_column_ids": hidden_column_ids,
                        "hidden_metric_ids": hidden_metric_ids,
                        "field_suggestions": field_suggestions,
                        "field_summary": {
                            "visible": sum(
                                item["decision"] == "visible"
                                for item in field_suggestions
                            ),
                            "hidden": sum(
                                item["decision"] == "hidden"
                                for item in field_suggestions
                            ),
                            "sensitive_hidden": sum(
                                bool(column.is_sensitive)
                                for column in columns_by_table.get(table_id, [])
                            ),
                            "attention": sum(
                                item["confidence"] < HIGH_CONFIDENCE
                                for item in field_suggestions
                            ),
                        },
                    })
                    explanations.append({
                        "table_id": table_id,
                        "confidence": table_confidence,
                        "reason": table_reason[:500],
                    })
                    target_confidences.append(table_confidence)

                base_active_version_id = (
                    int(binding.active_version_id)
                    if binding and binding.active_version_id else None
                )
                target = SemanticAccessBootstrapTargetModel(
                    workspace_id=workspace_id,
                    datasource_id=evidence_set.datasource_id,
                    run_id=run.id,
                    target_type=target_type,
                    target_id=target_id,
                    target_label=label,
                    include_descendants=include_descendants,
                    base_binding_id=int(binding.id) if binding else None,
                    base_revision=int(binding.revision or 0) if binding else 0,
                    included=True,
                    status="proposed",
                    confidence=(
                        sum(target_confidences) / len(target_confidences)
                        if target_confidences else 0.0
                    ),
                    definition_json={"name": label, "tables": rules},
                    candidates_json=candidates,
                    explanation_json=explanations,
                    validation_json={
                        "blockers": [],
                        "warnings": [],
                        "base_active_version_id": base_active_version_id,
                    },
                )
                session.add(target)
                targets.append(target)

            mapping_suggestions: list[SemanticAccessBootstrapMappingModel] = []
            for table_id, candidate in ownership_candidates.items():
                asset = table_assets.get(table_id)
                table = next(row for row in tables if int(row.id) == table_id)
                baseline_visible = bool(
                    asset
                    and _evidence_baseline_access(asset) == "workspace_visible"
                    and not bool(table.is_sensitive)
                )
                explicit_org_unit_ids = [
                    int(org.id)
                    for org in top_departments
                    if _evidence_table_access(
                        table_relations.get((int(org.id), table_id))
                    ) in {"visible", "partial"}
                ]
                scoped_org_unit_ids = (
                    [int(org.id) for org in top_departments]
                    if baseline_visible else explicit_org_unit_ids
                )
                validation = deepcopy(candidate.get("validation") or {})
                validation["grant_snapshot"] = {
                    "baseline_visible": baseline_visible,
                    "explicit_org_unit_ids": explicit_org_unit_ids,
                    "scoped_org_unit_ids": scoped_org_unit_ids,
                }
                mapping = SemanticAccessBootstrapMappingModel(
                    workspace_id=workspace_id,
                    datasource_id=evidence_set.datasource_id,
                    run_id=run.id,
                    table_id=table_id,
                    table_label=candidate.get("table_label") or table.business_name,
                    status="proposed",
                    accepted=False,
                    confidence=_clamp_confidence(candidate.get("confidence")),
                    base_mapping_json=_mapping_payload(
                        existing_mapping_by_table.get(table_id),
                    ),
                    proposed_mapping_json=deepcopy(candidate.get("proposed_mapping") or {}),
                    evidence_json=list(candidate.get("evidence") or []),
                    validation_json=validation,
                )
                session.add(mapping)
                mapping_suggestions.append(mapping)

            await session.flush()
            embedded_count = 0
            for mapping in mapping_suggestions:
                embedded = self._embed_mapping_row_scope_suggestion(
                    targets,
                    mapping,
                    actor_id=actor_id,
                )
                mapping.status = "embedded" if embedded else "skipped"
                mapping.edited_by = actor_id
                embedded_count += int(embedded)
            await self._revalidate_targets(session, run, targets)
            run.summary_json = {
                "bootstrap_mode": "table_field_row_v1",
                "target_count": len(targets),
                "selected_table_rule_count": sum(
                    len((row.definition_json or {}).get("tables") or [])
                    for row in targets
                ),
                "low_confidence_table_count": low_confidence_table_count,
                "low_confidence_field_count": low_confidence_field_count,
                "sensitive_table_count": sensitive_table_count,
                "sensitive_field_count": sensitive_field_count,
                "row_ownership_candidate_count": len(mapping_suggestions),
                "row_ownership_embedded_count": embedded_count,
                "row_ownership_skipped_count": len(row_ownership_summary.get("skipped") or []),
                "row_level_configured": embedded_count > 0,
                "high_confidence_threshold": HIGH_CONFIDENCE,
                "metadata_only": False,
            }
            return self._run_payload(run)

    async def get_target_draft(
        self,
        workspace_id: str,
        run_id: int,
        target_type: str,
        target_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id)
            self._assert_reviewable(run)
            await self._resolve_target_label(
                session, workspace_id, target_type, target_id,
            )
            draft_targets = list((await session.execute(select(
                SemanticAccessBootstrapTargetModel,
            ).where(
                SemanticAccessBootstrapTargetModel.run_id == run.id,
                SemanticAccessBootstrapTargetModel.included == True,  # noqa: E712
            ))).scalars())
            policy_context = await _effective_policy_context(
                session,
                workspace_id,
                run.datasource_id,
                target_type=target_type,
                target_id=target_id,
                definition_overrides={
                    (row.target_type, row.target_id): deepcopy(row.definition_json or {})
                    for row in draft_targets
                },
            )
            effective_tables = list(policy_context["effective"].values())
            target = (await session.execute(select(
                SemanticAccessBootstrapTargetModel,
            ).where(
                SemanticAccessBootstrapTargetModel.run_id == run.id,
                SemanticAccessBootstrapTargetModel.target_type == target_type,
                SemanticAccessBootstrapTargetModel.target_id == target_id,
            ))).scalar_one_or_none()
            if target:
                _binding, active_definition, _active_version_id = await self._binding_snapshot(
                    session, run, target_type, target_id,
                )
                return {
                    "run": self._run_payload(run),
                    "target": self._target_payload(target),
                    "active_definition": active_definition,
                    "effective_tables": effective_tables,
                }
            binding, definition, active_version_id = await self._binding_snapshot(
                session, run, target_type, target_id,
            )
            return {
                "run": self._run_payload(run),
                "active_definition": definition,
                "effective_tables": effective_tables,
                "target": {
                    "id": None,
                    "run_id": run.id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "target_label": await self._resolve_target_label(
                        session, workspace_id, target_type, target_id,
                    ),
                    "include_descendants": target_type == "org_unit",
                    "base_binding_id": int(binding.id) if binding else None,
                    "base_revision": int(binding.revision or 0) if binding else 0,
                    "included": bool(definition.get("tables")),
                    "status": "inherited",
                    "confidence": 0.0,
                    "definition": definition,
                    "candidates": [],
                    "explanations": [],
                    "validation": {
                        "blockers": [],
                        "warnings": [],
                        "base_active_version_id": active_version_id,
                    },
                    "updated_at": None,
                },
            }

    async def upsert_target_draft(
        self,
        workspace_id: str,
        run_id: int,
        target_type: str,
        target_id: str,
        actor_id: str,
        expected_revision: int,
        *,
        definition: dict[str, Any],
        include_descendants: bool,
        reason: str | None,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id, lock=True)
            self._assert_reviewable(run)
            self._assert_revision(run, expected_revision)
            label = await self._resolve_target_label(
                session, workspace_id, target_type, target_id,
            )
            definition = self._normalize_draft_definition(definition)
            await self._validate_definition_assets(session, run, definition)
            target = (await session.execute(select(
                SemanticAccessBootstrapTargetModel,
            ).where(
                SemanticAccessBootstrapTargetModel.run_id == run.id,
                SemanticAccessBootstrapTargetModel.target_type == target_type,
                SemanticAccessBootstrapTargetModel.target_id == target_id,
            ).with_for_update())).scalar_one_or_none()
            if target is None:
                binding, previous_definition, active_version_id = await self._binding_snapshot(
                    session, run, target_type, target_id,
                )
                target = SemanticAccessBootstrapTargetModel(
                    workspace_id=workspace_id,
                    datasource_id=run.datasource_id,
                    run_id=run.id,
                    target_type=target_type,
                    target_id=target_id,
                    target_label=label,
                    include_descendants=target_type == "org_unit",
                    base_binding_id=int(binding.id) if binding else None,
                    base_revision=int(binding.revision or 0) if binding else 0,
                    included=bool(definition.get("tables")),
                    status="edited",
                    confidence=0.0,
                    definition_json=definition,
                    candidates_json=[],
                    explanation_json=[],
                    validation_json={
                        "blockers": [],
                        "warnings": [],
                        "base_active_version_id": active_version_id,
                    },
                    edited_by=actor_id,
                )
                session.add(target)
            else:
                previous_definition = dict(target.definition_json or {})
                candidates = deepcopy(target.candidates_json or [])
                next_rules = {
                    int(rule["table_id"]): rule
                    for rule in definition.get("tables") or []
                }
                for candidate in candidates:
                    table_id = int(candidate.get("table_id") or 0)
                    next_rule = next_rules.get(table_id)
                    candidate["review_state"] = (
                        "modified" if next_rule else "rejected"
                    )
                    if next_rule:
                        candidate["decision"] = next_rule.get("decision", "visible")
                        actual_hidden = set(next_rule.get("hidden_column_ids") or [])
                        for field in candidate.get("field_suggestions") or []:
                            field["review_state"] = (
                                "accepted"
                                if (
                                    int(field.get("column_id") or 0) in actual_hidden
                                ) == (field.get("decision") == "hidden")
                                else "modified"
                            )
                target.definition_json = definition
                target.candidates_json = candidates
                target.include_descendants = target_type == "org_unit"
                target.included = bool(definition.get("tables"))
                target.status = "edited"
                target.edited_by = actor_id

            if (
                target_type in {"position", "user"}
                and _definition_expands_access(previous_definition, definition)
                and not (reason or "").strip()
            ):
                raise SemanticAccessBootstrapError(
                    "扩大岗位或账号访问范围必须填写变更理由",
                    code="override_reason_required",
                )
            if (reason or "").strip():
                target.explanation_json = [
                    *(target.explanation_json or []),
                    {
                        "source": "manual_edit",
                        "reason": reason.strip()[:2000],
                        "edited_by": actor_id,
                        "edited_at": datetime.now().isoformat(),
                    },
                ]
            await session.flush()
            await self._revalidate_targets(session, run, [target])
            run.revision += 1
            await session.flush()
            return {
                "run": self._run_payload(run),
                "target": self._target_payload(target),
            }

    async def publish_target_tables(
        self,
        workspace_id: str,
        run_id: int,
        target_type: str,
        target_id: str,
        actor_id: str,
        expected_revision: int,
        *,
        table_ids: list[int],
        definition: dict[str, Any],
        include_descendants: bool,
        reason: str | None,
        confirm_warnings: bool = True,
    ) -> dict[str, Any]:
        """Persist and activate only the confirmed tables for one target."""
        selected_table_ids = {int(value) for value in table_ids if int(value) > 0}
        if not selected_table_ids or len(selected_table_ids) != len(table_ids):
            raise SemanticAccessBootstrapError(
                "生效表范围无效或重复", code="publish_tables_invalid",
            )
        if target_type not in {"baseline", "org_unit"}:
            raise SemanticAccessBootstrapError(
                "首次权限只支持按全员基线或部门生效", code="target_invalid",
            )

        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id, lock=True)
            self._assert_reviewable(run)
            self._assert_revision(run, expected_revision)
            _datasource, governed_tables, _columns, _metrics, semantic_fingerprint = (
                await self._governed_assets(session, workspace_id, run.datasource_id)
            )
            _departments, organization_fingerprint = await self._organization_snapshot(
                session, workspace_id,
            )
            if semantic_fingerprint != run.schema_fingerprint:
                raise SemanticAccessBootstrapError(
                    "语义 Schema 已变化，请重新生成", code="schema_changed", status_code=409,
                )
            if organization_fingerprint != run.organization_fingerprint:
                raise SemanticAccessBootstrapError(
                    "组织架构已变化，请重新生成", code="organization_changed", status_code=409,
                )
            governed_table_ids = {int(row.id) for row in governed_tables}
            if not selected_table_ids.issubset(governed_table_ids):
                raise SemanticAccessBootstrapError(
                    "生效范围包含已停用或不存在的语义表", code="table_not_found", status_code=404,
                )

            label = await self._resolve_target_label(
                session, workspace_id, target_type, target_id,
            )
            draft_definition = self._normalize_draft_definition(definition)
            await self._validate_definition_assets(session, run, draft_definition)
            target = (await session.execute(select(
                SemanticAccessBootstrapTargetModel,
            ).where(
                SemanticAccessBootstrapTargetModel.run_id == run.id,
                SemanticAccessBootstrapTargetModel.target_type == target_type,
                SemanticAccessBootstrapTargetModel.target_id == target_id,
            ).with_for_update())).scalar_one_or_none()
            if target is None:
                binding, _previous_definition, active_version_id = await self._binding_snapshot(
                    session, run, target_type, target_id,
                )
                target = SemanticAccessBootstrapTargetModel(
                    workspace_id=workspace_id,
                    datasource_id=run.datasource_id,
                    run_id=run.id,
                    target_type=target_type,
                    target_id=target_id,
                    target_label=label,
                    include_descendants=target_type == "org_unit",
                    base_binding_id=int(binding.id) if binding else None,
                    base_revision=int(binding.revision or 0) if binding else 0,
                    included=bool(draft_definition.get("tables")),
                    status="edited",
                    confidence=0.0,
                    definition_json=draft_definition,
                    candidates_json=[],
                    explanation_json=[],
                    validation_json={
                        "blockers": [], "warnings": [],
                        "base_active_version_id": active_version_id,
                    },
                    edited_by=actor_id,
                )
                session.add(target)
                await session.flush()
            else:
                target.definition_json = draft_definition
                target.include_descendants = target_type == "org_unit"
                target.included = bool(draft_definition.get("tables"))
                target.edited_by = actor_id

            binding = (await session.execute(select(SemanticPolicyBindingModel).where(
                SemanticPolicyBindingModel.workspace_id == workspace_id,
                SemanticPolicyBindingModel.datasource_id == run.datasource_id,
                SemanticPolicyBindingModel.target_type == target_type,
                SemanticPolicyBindingModel.target_id == target_id,
            ).with_for_update())).scalar_one_or_none()
            current_binding_id = int(binding.id) if binding else None
            if current_binding_id != target.base_binding_id:
                raise SemanticAccessBootstrapError(
                    "目标绑定已变化，请刷新后重试", code="target_binding_conflict", status_code=409,
                )
            current_active_version_id = int(binding.active_version_id) if binding and binding.active_version_id else None
            if current_active_version_id != (target.validation_json or {}).get("base_active_version_id"):
                raise SemanticAccessBootstrapError(
                    "目标生效版本已变化，请刷新后重试", code="target_configured_conflict", status_code=409,
                )
            if int(getattr(binding, "revision", 0) or 0) != int(target.base_revision or 0):
                raise SemanticAccessBootstrapError(
                    "目标策略修订已变化，请刷新后重试", code="target_revision_conflict", status_code=409,
                )
            active_definition: dict[str, Any] = {"name": label, "tables": []}
            if binding and binding.active_version_id:
                active_version = await session.get(
                    SemanticPolicyVersionModel, binding.active_version_id,
                )
                if active_version:
                    active_definition = deepcopy(active_version.definition_json or active_definition)
            if binding is None:
                binding = SemanticPolicyBindingModel(
                    workspace_id=workspace_id,
                    datasource_id=run.datasource_id,
                    target_type=target_type,
                    target_id=target_id,
                    include_descendants=target_type == "org_unit",
                    status=True,
                    created_by=actor_id,
                )
                session.add(binding)
                await session.flush()
            else:
                binding.include_descendants = target_type == "org_unit"
                binding.status = True

            published_definition = active_definition
            for table_id in sorted(selected_table_ids):
                published_definition = _merge_table_definition(
                    published_definition, draft_definition, table_id,
                )

            mappings = list((await session.execute(select(
                SemanticAccessBootstrapMappingModel,
            ).where(
                SemanticAccessBootstrapMappingModel.run_id == run.id,
                SemanticAccessBootstrapMappingModel.table_id.in_(selected_table_ids),
            ).with_for_update())).scalars())
            mapping_overrides = self._mapping_overrides(mappings)
            for mapping in mappings:
                if not mapping.accepted or mapping.status != "accepted":
                    continue
                current_mapping = (await session.execute(select(
                    SemanticOwnershipMappingModel,
                ).where(
                    SemanticOwnershipMappingModel.workspace_id == workspace_id,
                    SemanticOwnershipMappingModel.datasource_id == run.datasource_id,
                    SemanticOwnershipMappingModel.table_id == mapping.table_id,
                ).with_for_update())).scalar_one_or_none()
                if _mapping_payload(current_mapping) != dict(mapping.base_mapping_json or {}):
                    raise SemanticAccessBootstrapError(
                        f"{mapping.table_label} 的归属映射已变化，请刷新后重试",
                        code="mapping_conflict", status_code=409,
                    )
                await upsert_ownership_mapping_in_session(
                    session,
                    workspace_id=workspace_id,
                    datasource_id=run.datasource_id,
                    table_id=mapping.table_id,
                    payload=dict(mapping.proposed_mapping_json or {}),
                    actor_id=actor_id,
                )
                mapping.status = "applied"

            previous = {
                "active_version_id": binding.active_version_id,
                "revision": binding.revision,
            }
            try:
                version, _compilation, warnings = await persist_target_policy_version_in_session(
                    session,
                    binding,
                    published_definition,
                    actor_id,
                    source_text=reason or f"按表确认：{','.join(map(str, sorted(selected_table_ids)))}",
                    source_type="evidence_table_confirm",
                    source_ref=f"evidence_set:{run.evidence_set_id or 0}",
                    confirm_warnings=confirm_warnings,
                    mapping_overrides=mapping_overrides,
                )
            except SemanticBindingError as exc:
                raise SemanticAccessBootstrapError(
                    str(exc), code=exc.code, status_code=exc.status_code, details=exc.details,
                ) from exc

            draft_rules = {
                int(rule["table_id"]): rule
                for rule in draft_definition.get("tables") or []
            }
            target.candidates_json = _review_published_tables(
                target.candidates_json or [], draft_rules, selected_table_ids,
            )
            target.status = "partially_applied"
            target.base_binding_id = int(binding.id)
            target.base_revision = int(binding.revision or 0)
            target.validation_json = {
                **dict(target.validation_json or {}),
                "base_active_version_id": int(version.id),
                "last_published_table_ids": sorted(selected_table_ids),
            }

            evidence_set = None
            if run.evidence_set_id:
                evidence_set = (await session.execute(select(
                    SemanticAccessEvidenceSetModel,
                ).where(
                    SemanticAccessEvidenceSetModel.id == run.evidence_set_id,
                    SemanticAccessEvidenceSetModel.workspace_id == workspace_id,
                ).with_for_update())).scalar_one_or_none()
            if evidence_set:
                now = datetime.now()
                if target_type == "baseline":
                    rows = list((await session.execute(select(
                        SemanticAccessEvidenceAssetModel,
                    ).where(
                        SemanticAccessEvidenceAssetModel.evidence_set_id == evidence_set.id,
                        SemanticAccessEvidenceAssetModel.asset_type == "table",
                        SemanticAccessEvidenceAssetModel.table_id.in_(selected_table_ids),
                    ))).scalars())
                else:
                    rows = list((await session.execute(select(
                        SemanticAccessEvidenceRelationModel,
                    ).where(
                        SemanticAccessEvidenceRelationModel.evidence_set_id == evidence_set.id,
                        SemanticAccessEvidenceRelationModel.org_unit_id == int(target_id),
                        SemanticAccessEvidenceRelationModel.table_id.in_(selected_table_ids),
                    ))).scalars())
                for row in rows:
                    published_rule = draft_rules.get(int(row.table_id))
                    row.review_status = "modified"
                    row.reviewed_by = actor_id
                    row.reviewed_at = now
                    if target_type == "baseline":
                        row.baseline_access = (
                            "workspace_visible" if published_rule is not None else "controlled"
                        )
                        row.access_class = (
                            "workspace_public" if published_rule is not None else "restricted"
                        )
                    elif row.asset_type == "table":
                        row.access_level = (
                            "visible"
                            if published_rule is not None
                            and (published_rule.get("row_scope") or {}).get("type", "all") == "all"
                            else "partial" if published_rule is not None else "hidden"
                        )
                        row.access_decision = (
                            "visible" if published_rule is not None else "hidden"
                        )
                    elif row.asset_type == "column":
                        hidden_columns = set(
                            (published_rule or {}).get("hidden_column_ids") or []
                        )
                        row.field_decision = (
                            "visible"
                            if published_rule is not None and int(row.asset_id) not in hidden_columns
                            else "hidden"
                        )
                        row.access_decision = row.field_decision
                evidence_set.revision += 1
                from app.services.permission_evidence_service import get_permission_evidence_service
                await get_permission_evidence_service()._refresh_progress(session, evidence_set)

            await self._revalidate_targets(session, run, [target])

            datasource = await self._datasource(
                session, workspace_id, run.datasource_id,
            )
            datasource.access_bootstrap_required = False
            if evidence_set:
                datasource.active_evidence_set_id = evidence_set.id
            authorization_revision = await bump_authorization_revision(session, workspace_id)
            audit = await record_authorization_audit(
                session,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="semantic_policy.evidence_table.activate",
                target_type=f"semantic_{target_type}",
                target_id=target_id,
                before=previous,
                after={
                    "active_version_id": version.id,
                    "revision": binding.revision,
                    "table_ids": sorted(selected_table_ids),
                },
                reason=f"bootstrap_run:{run.id}",
            )
            run.revision += 1
            await session.flush()
            return {
                "run": self._run_payload(run),
                "target": self._target_payload(target),
                "active_definition": published_definition,
                "binding_id": binding.id,
                "binding_revision": binding.revision,
                "version_id": version.id,
                "version": version.version,
                "warnings": warnings,
                "audit_id": audit.id,
                "authorization_revision": authorization_revision,
            }

    async def _binding_snapshot(
        self,
        session,
        run: SemanticAccessBootstrapRunModel,
        target_type: str,
        target_id: str,
    ) -> tuple[Any, dict[str, Any], int | None]:
        binding = (await session.execute(select(
            SemanticPolicyBindingModel,
        ).where(
            SemanticPolicyBindingModel.workspace_id == run.workspace_id,
            SemanticPolicyBindingModel.datasource_id == run.datasource_id,
            SemanticPolicyBindingModel.target_type == target_type,
            SemanticPolicyBindingModel.target_id == target_id,
        ))).scalar_one_or_none()
        active_version_id = (
            int(binding.active_version_id)
            if binding and binding.active_version_id else None
        )
        version = (
            await session.get(SemanticPolicyVersionModel, active_version_id)
            if active_version_id else None
        )
        definition = deepcopy(
            version.definition_json if version else {
                "name": await self._resolve_target_label(
                    session, run.workspace_id, target_type, target_id,
                ),
                "tables": [],
            }
        )
        return binding, definition, active_version_id

    async def _resolve_target_label(
        self,
        session,
        workspace_id: str,
        target_type: str,
        target_id: str,
    ) -> str:
        if target_type == "baseline" and target_id == "*":
            return "全员基线"
        if target_type == "org_unit":
            row = await session.get(DepartmentModel, int(target_id))
            valid = row and row.workspace_id == workspace_id and bool(row.status)
            label = row.name if valid else None
        elif target_type == "position":
            row = await session.get(PositionModel, int(target_id))
            valid = row and row.workspace_id == workspace_id and bool(row.status)
            label = row.name if valid else None
        elif target_type == "user":
            row = await session.get(UserModel, str(target_id))
            valid = row and row.workspace_id == workspace_id and not bool(row.disabled)
            label = row.username if valid else None
        else:
            label = None
        if not label:
            raise SemanticAccessBootstrapError(
                "授权对象不存在或已停用",
                code="target_not_found",
                status_code=404,
            )
        return str(label)

    async def get_suggestions(
        self, workspace_id: str, run_id: int, actor_id: str,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id)
            targets = list((await session.execute(select(SemanticAccessBootstrapTargetModel).where(
                SemanticAccessBootstrapTargetModel.run_id == run.id,
            ).order_by(
                SemanticAccessBootstrapTargetModel.target_type,
                SemanticAccessBootstrapTargetModel.target_label,
            ))).scalars())
            mappings = list((await session.execute(select(SemanticAccessBootstrapMappingModel).where(
                SemanticAccessBootstrapMappingModel.run_id == run.id,
                SemanticAccessBootstrapMappingModel.status != "existing",
            ).order_by(SemanticAccessBootstrapMappingModel.table_label))).scalars())
            snapshot = dict(run.input_snapshot_json or {})
            assets = snapshot.get("assets")
            if not isinstance(assets, dict):
                _datasource, tables, columns, _metrics, semantic_fingerprint = await self._governed_assets(
                    session, workspace_id, run.datasource_id,
                )
                assets = (
                    _asset_snapshot(tables, columns)
                    if semantic_fingerprint == run.schema_fingerprint
                    else {"tables": [], "columns": []}
                )
            return {
                "run": self._run_payload(run),
                "targets": [self._target_payload(row) for row in targets],
                "ownership_mappings": [self._mapping_suggestion_payload(row) for row in mappings],
                "assets": assets,
            }

    def _target_payload(self, row: SemanticAccessBootstrapTargetModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "target_label": row.target_label,
            "include_descendants": bool(row.include_descendants),
            "base_binding_id": row.base_binding_id,
            "base_revision": int(row.base_revision or 0),
            "included": bool(row.included),
            "status": row.status,
            "confidence": float(row.confidence or 0),
            "definition": row.definition_json or {},
            "candidates": row.candidates_json or [],
            "explanations": row.explanation_json or [],
            "validation": row.validation_json or {},
            "updated_at": _serialize_datetime(row.updated_at),
        }

    def _rule_from_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        decision = candidate.get("decision") or (
            "visible" if candidate.get("selected") else "hidden"
        )
        rule = {
            "table_id": int(candidate["table_id"]),
            "decision": decision,
            "hidden_column_ids": [int(value) for value in candidate.get("hidden_column_ids") or []],
            "hidden_metric_ids": [int(value) for value in candidate.get("hidden_metric_ids") or []],
        }
        if decision == "visible":
            scope = candidate.get("row_scope") or "all"
            rule["row_scope"] = (
                deepcopy(scope) if isinstance(scope, dict) else {"type": scope}
            )
        return rule

    @staticmethod
    def _row_scope_suggestions_from_mapping(
        mapping: SemanticAccessBootstrapMappingModel,
    ) -> dict[str, dict[str, Any]]:
        """Build explicit custom conditions only for complete ownership domains."""
        proposed = dict(mapping.proposed_mapping_json or {})
        validation = dict(mapping.validation_json or {})
        if (
            list(validation.get("blockers") or [])
            or list(validation.get("unresolved_values") or [])
            or int(validation.get("null_rows") or 0) > 0
            or int(validation.get("empty_rows") or 0) > 0
        ):
            return {}

        column_id = int(proposed.get("org_column_id") or 0)
        value_mapping = proposed.get("org_value_mapping") or {}
        bindings = value_mapping.get("bindings") or []
        source_values = validation.get("source_values") or []
        if column_id <= 0 or not isinstance(bindings, list) or not source_values:
            return {}

        binding_by_source: dict[tuple[str, str], dict[str, Any]] = {}
        for binding in bindings:
            if not isinstance(binding, dict):
                return {}
            key = (
                str(binding.get("source_type") or ""),
                str(binding.get("source_value")),
            )
            if key in binding_by_source:
                return {}
            binding_by_source[key] = binding

        values_by_org: dict[str, list[Any]] = {}
        for source in source_values:
            if not isinstance(source, dict):
                return {}
            key = (
                str(source.get("source_type") or ""),
                str(source.get("source_value")),
            )
            binding = binding_by_source.get(key)
            if not binding or binding.get("target_kind") != "org_unit":
                return {}
            org_unit_id = int(binding.get("org_unit_id") or 0)
            if org_unit_id <= 0:
                return {}
            values_by_org.setdefault(str(org_unit_id), []).append(
                source.get("source_value"),
            )

        result: dict[str, dict[str, Any]] = {}
        for org_unit_id, values in values_by_org.items():
            unique_values = list(dict.fromkeys(values))
            if not unique_values:
                continue
            result[org_unit_id] = {
                "type": "custom",
                "condition": {
                    "column_id": column_id,
                    "operator": "=" if len(unique_values) == 1 else "in",
                    "value": unique_values[0] if len(unique_values) == 1 else unique_values,
                },
            }
        return result

    def _embed_mapping_row_scope_suggestion(
        self,
        targets: list[SemanticAccessBootstrapTargetModel],
        mapping: SemanticAccessBootstrapMappingModel,
        *,
        actor_id: str,
    ) -> bool:
        """Place a safe row-scope suggestion directly on the matching department table."""
        scope_by_org = self._row_scope_suggestions_from_mapping(mapping)
        if not scope_by_org:
            return False

        snapshot = dict((mapping.validation_json or {}).get("grant_snapshot") or {})
        baseline_visible = bool(snapshot.get("baseline_visible"))
        explicitly_granted = {
            str(value) for value in snapshot.get("explicit_org_unit_ids") or []
        }
        scoped_org_ids = {
            str(value) for value in snapshot.get("scoped_org_unit_ids") or []
        }
        table_id = int(mapping.table_id)
        embedded = False

        for target in targets:
            candidates = deepcopy(target.candidates_json or [])
            candidate = next((
                row for row in candidates
                if int(row.get("table_id") or 0) == table_id
            ), None)
            if candidate is None:
                continue
            rules = {
                int(row["table_id"]): deepcopy(row)
                for row in (target.definition_json or {}).get("tables") or []
            }

            if target.target_type == "baseline":
                if baseline_visible:
                    rules.pop(table_id, None)
                    candidate["selected"] = False
                    candidate["decision"] = "hidden"
                    candidate["row_scope"] = {"type": "all"}
                    candidate["requires_mapping"] = False
                target.definition_json = {
                    **dict(target.definition_json or {}),
                    "name": target.target_label,
                    "tables": list(rules.values()),
                }
                target.candidates_json = candidates
                continue

            if target.target_type != "org_unit":
                continue
            target_id = str(target.target_id)
            originally_granted = (
                target_id in explicitly_granted
                or baseline_visible and target_id in scoped_org_ids
            )
            suggested_scope = scope_by_org.get(target_id)
            if not originally_granted:
                continue
            if suggested_scope is None:
                if baseline_visible:
                    candidate["selected"] = True
                    candidate["decision"] = "visible"
                    candidate["row_scope"] = {"type": "all"}
                    candidate["requires_mapping"] = False
                    candidate.pop("row_scope_suggestion", None)
                    candidate.pop("row_scope_suggestion_id", None)
                    rules[table_id] = self._rule_from_candidate(candidate)
                    target.definition_json = {
                        **dict(target.definition_json or {}),
                        "name": target.target_label,
                        "tables": list(rules.values()),
                    }
                    target.candidates_json = candidates
                    target.included = True
                    target.status = "edited"
                    target.edited_by = actor_id
                continue

            candidate["selected"] = True
            candidate["decision"] = "visible"
            candidate["row_scope"] = deepcopy(suggested_scope)
            candidate["requires_mapping"] = False
            candidate["has_mapping_proposal"] = True
            candidate["row_scope_suggestion"] = True
            candidate["row_scope_suggestion_id"] = int(mapping.id)
            candidate["confidence"] = float(
                mapping.confidence or candidate.get("confidence") or 0,
            )
            candidate["confidence_level"] = (
                "high" if candidate["confidence"] >= HIGH_CONFIDENCE
                else "medium" if candidate["confidence"] >= REVIEW_CONFIDENCE
                else "low"
            )
            rules[table_id] = self._rule_from_candidate(candidate)
            target.definition_json = {
                **dict(target.definition_json or {}),
                "name": target.target_label,
                "tables": list(rules.values()),
            }
            target.candidates_json = candidates
            target.included = True
            target.status = "edited"
            target.edited_by = actor_id
            embedded = True
        return embedded

    @staticmethod
    def _normalize_draft_definition(definition: dict[str, Any]) -> dict[str, Any]:
        """Strip invalid row scopes from non-grant rules without losing edits."""
        normalized = deepcopy(definition)
        for rule in normalized.get("tables") or []:
            if rule.get("decision") != "visible":
                rule.pop("row_scope", None)
        return normalized

    async def _validate_definition_assets(
        self, session, run: SemanticAccessBootstrapRunModel, definition: dict[str, Any],
    ) -> None:
        _datasource, tables, columns, metrics, semantic_fingerprint = await self._governed_assets(
            session, run.workspace_id, run.datasource_id,
        )
        if semantic_fingerprint != run.schema_fingerprint:
            raise SemanticAccessBootstrapError(
                "语义 Schema 已变化，请重新生成", code="schema_changed", status_code=409,
            )
        table_ids = {int(row.id) for row in tables}
        column_ids_by_table: dict[int, set[int]] = {}
        metric_ids_by_table: dict[int, set[int]] = {}
        for column in columns:
            column_ids_by_table.setdefault(int(column.table_id), set()).add(int(column.id))
        for metric in metrics:
            metric_ids_by_table.setdefault(int(metric.table_id), set()).add(int(metric.id))
        seen: set[int] = set()
        for rule in definition.get("tables") or []:
            table_id = rule.get("table_id")
            if isinstance(table_id, bool) or not isinstance(table_id, int) or table_id not in table_ids:
                raise SemanticAccessBootstrapError(
                    "审核方案包含未启用的表", code="table_not_enabled",
                    details={"table_id": table_id},
                )
            if table_id in seen:
                raise SemanticAccessBootstrapError(
                    "审核方案包含重复表", code="table_duplicated",
                    details={"table_id": table_id},
                )
            seen.add(table_id)
            for key, valid_ids, code, label in (
                ("hidden_column_ids", column_ids_by_table.get(table_id, set()), "column_not_enabled", "字段"),
                ("hidden_metric_ids", metric_ids_by_table.get(table_id, set()), "metric_not_enabled", "指标"),
            ):
                values = rule.get(key) or []
                if not isinstance(values, list) or any(
                    isinstance(value, bool) or not isinstance(value, int) or value not in valid_ids
                    for value in values
                ):
                    raise SemanticAccessBootstrapError(
                        f"审核方案包含未启用的{label}", code=code,
                        details={"table_id": table_id},
                    )

    def _mapping_suggestion_payload(self, row: SemanticAccessBootstrapMappingModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "table_id": row.table_id,
            "table_label": row.table_label,
            "status": row.status,
            "accepted": bool(row.accepted),
            "confidence": float(row.confidence or 0),
            "base_mapping": row.base_mapping_json or {},
            "proposed_mapping": row.proposed_mapping_json or {},
            "evidence": row.evidence_json or [],
            "validation": row.validation_json or {},
            "updated_at": _serialize_datetime(row.updated_at),
        }

    async def _apply_mapping_scope_decision(
        self,
        session,
        run: SemanticAccessBootstrapRunModel,
        mapping: SemanticAccessBootstrapMappingModel,
        *,
        accepted: bool,
        actor_id: str,
    ) -> list[SemanticAccessBootstrapTargetModel]:
        """Deterministically scope every department that already had table access."""
        targets = list((await session.execute(select(
            SemanticAccessBootstrapTargetModel,
        ).where(
            SemanticAccessBootstrapTargetModel.run_id == run.id,
        ).with_for_update())).scalars())
        snapshot = dict((mapping.validation_json or {}).get("grant_snapshot") or {})
        baseline_visible = bool(snapshot.get("baseline_visible"))
        explicit_org_ids = {str(value) for value in snapshot.get("explicit_org_unit_ids") or []}
        scoped_org_ids = {str(value) for value in snapshot.get("scoped_org_unit_ids") or []}
        table_id = int(mapping.table_id)
        for target in targets:
            candidates = deepcopy(target.candidates_json or [])
            candidate = next((
                row for row in candidates
                if int(row.get("table_id") or 0) == table_id
            ), None)
            if candidate is None:
                continue
            rules = {
                int(row["table_id"]): deepcopy(row)
                for row in (target.definition_json or {}).get("tables") or []
            }
            if accepted:
                should_grant = (
                    target.target_type == "org_unit"
                    and str(target.target_id) in scoped_org_ids
                )
            else:
                should_grant = (
                    target.target_type == "baseline" and baseline_visible
                    or target.target_type == "org_unit"
                    and str(target.target_id) in explicit_org_ids
                )
            candidate["selected"] = bool(should_grant)
            candidate["decision"] = "visible" if should_grant else "hidden"
            candidate["row_scope"] = (
                {
                    "type": "target_org_tree",
                    "unowned_access": "table_grantees",
                }
                if accepted and should_grant else {"type": "all"}
            )
            candidate["requires_mapping"] = bool(accepted and should_grant)
            candidate["has_mapping_proposal"] = True
            if should_grant:
                rules[table_id] = self._rule_from_candidate(candidate)
            else:
                rules.pop(table_id, None)
            target.definition_json = {
                **dict(target.definition_json or {}),
                "name": target.target_label,
                "tables": list(rules.values()),
            }
            target.candidates_json = candidates
            target.included = bool(rules)
            target.status = "edited"
            target.edited_by = actor_id
        return targets

    async def update_target(
        self,
        workspace_id: str,
        run_id: int,
        suggestion_id: int,
        actor_id: str,
        expected_revision: int,
        *,
        included: bool,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id, lock=True)
            self._assert_reviewable(run)
            self._assert_revision(run, expected_revision)
            target = (await session.execute(select(SemanticAccessBootstrapTargetModel).where(
                SemanticAccessBootstrapTargetModel.id == suggestion_id,
                SemanticAccessBootstrapTargetModel.run_id == run.id,
            ).with_for_update())).scalar_one_or_none()
            if not target:
                raise SemanticAccessBootstrapError("目标草案不存在", code="suggestion_not_found", status_code=404)
            await self._validate_definition_assets(session, run, definition)
            before_rules = {
                int(rule["table_id"]): rule
                for rule in (target.definition_json or {}).get("tables") or []
            }
            next_rules = {
                int(rule["table_id"]): rule for rule in definition.get("tables") or []
            }
            candidates = deepcopy(target.candidates_json or [])
            for candidate in candidates:
                table_id = int(candidate.get("table_id") or 0)
                if before_rules.get(table_id) == next_rules.get(table_id):
                    continue
                next_rule = next_rules.get(table_id)
                candidate["review_state"] = "modified" if next_rule else "rejected"
                actual_hidden = set((next_rule or {}).get("hidden_column_ids") or [])
                for field in candidate.get("field_suggestions") or []:
                    if next_rule is None:
                        field["review_state"] = "rejected"
                        continue
                    expected_hidden = field.get("effective_decision") == "hidden"
                    field["review_state"] = (
                        "accepted"
                        if (int(field.get("column_id") or 0) in actual_hidden) == expected_hidden
                        else "modified"
                    )
            target.included = bool(included)
            target.definition_json = definition
            target.candidates_json = candidates
            target.status = "edited"
            target.edited_by = actor_id
            await self._revalidate_targets(session, run, [target])
            run.revision += 1
            await session.flush()
            return {"run": self._run_payload(run), "target": self._target_payload(target)}

    async def update_mapping(
        self,
        workspace_id: str,
        run_id: int,
        suggestion_id: int,
        actor_id: str,
        expected_revision: int,
        *,
        accepted: bool,
        proposed_mapping: dict[str, Any],
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id, lock=True)
            self._assert_reviewable(run)
            self._assert_revision(run, expected_revision)
            row = (await session.execute(select(SemanticAccessBootstrapMappingModel).where(
                SemanticAccessBootstrapMappingModel.id == suggestion_id,
                SemanticAccessBootstrapMappingModel.run_id == run.id,
            ).with_for_update())).scalar_one_or_none()
            if not row:
                raise SemanticAccessBootstrapError("归属映射建议不存在", code="mapping_suggestion_not_found", status_code=404)
            if accepted:
                await self._validate_mapping_payload(
                    session,
                    run,
                    row.table_id,
                    proposed_mapping,
                    row.validation_json or {},
                )
            row.accepted = bool(accepted)
            row.status = "accepted" if accepted else "rejected"
            row.proposed_mapping_json = proposed_mapping
            row.edited_by = actor_id
            targets = await self._apply_mapping_scope_decision(
                session=session,
                run=run,
                mapping=row,
                accepted=accepted,
                actor_id=actor_id,
            )
            run.revision += 1
            await self._revalidate_targets(session, run, targets)
            all_mappings = list((await session.execute(select(
                SemanticAccessBootstrapMappingModel,
            ).where(
                SemanticAccessBootstrapMappingModel.run_id == run.id,
            ))).scalars())
            accepted_count = sum(
                1 for item in all_mappings
                if item.accepted and item.status == "accepted"
            )
            run.summary_json = {
                **dict(run.summary_json or {}),
                "row_ownership_accepted_count": accepted_count,
                "row_ownership_rejected_count": sum(
                    1 for item in all_mappings if item.status == "rejected"
                ),
                "row_level_configured": accepted_count > 0,
            }
            await session.flush()
            return {
                "run": self._run_payload(run),
                "mapping": self._mapping_suggestion_payload(row),
                "targets": [self._target_payload(target) for target in targets],
            }

    async def review_ownership_mappings(
        self,
        workspace_id: str,
        run_id: int,
        actor_id: str,
        expected_revision: int,
        decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Apply row-ownership reviews atomically and advance the revision once."""
        if not decisions or len(decisions) > 1000:
            raise SemanticAccessBootstrapError(
                "批量行归属审核需要提交 1 到 1000 个决定",
                code="mapping_reviews_invalid",
            )
        suggestion_ids = [int(row.get("suggestion_id") or 0) for row in decisions]
        if any(value <= 0 for value in suggestion_ids) or len(set(suggestion_ids)) != len(suggestion_ids):
            raise SemanticAccessBootstrapError(
                "批量行归属审核包含无效或重复的建议",
                code="mapping_reviews_duplicated",
            )
        for decision in decisions:
            if decision.get("action") not in {"accept", "reject"}:
                raise SemanticAccessBootstrapError(
                    "批量行归属审核操作无效",
                    code="mapping_review_action_invalid",
                )

        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id, lock=True)
            self._assert_reviewable(run)
            self._assert_revision(run, expected_revision)
            mappings = list((await session.execute(select(
                SemanticAccessBootstrapMappingModel,
            ).where(
                SemanticAccessBootstrapMappingModel.run_id == run.id,
                SemanticAccessBootstrapMappingModel.id.in_(suggestion_ids),
            ).with_for_update())).scalars())
            mapping_by_id = {int(row.id): row for row in mappings}
            if set(suggestion_ids) != set(mapping_by_id):
                raise SemanticAccessBootstrapError(
                    "批量行归属审核包含不存在的建议",
                    code="mapping_suggestion_not_found",
                    status_code=404,
                )

            targets: list[SemanticAccessBootstrapTargetModel] = []
            for decision in decisions:
                mapping = mapping_by_id[int(decision["suggestion_id"])]
                accepted = decision["action"] == "accept"
                proposed_mapping = (
                    decision.get("proposed_mapping")
                    if decision.get("proposed_mapping") is not None
                    else dict(mapping.proposed_mapping_json or {})
                )
                if accepted:
                    await self._validate_mapping_payload(
                        session,
                        run,
                        mapping.table_id,
                        proposed_mapping,
                        mapping.validation_json or {},
                    )
                mapping.accepted = accepted
                mapping.status = "accepted" if accepted else "rejected"
                mapping.proposed_mapping_json = proposed_mapping
                mapping.edited_by = actor_id
                targets = await self._apply_mapping_scope_decision(
                    session=session,
                    run=run,
                    mapping=mapping,
                    accepted=accepted,
                    actor_id=actor_id,
                )

            await self._revalidate_targets(session, run, targets or None)
            all_mappings = list((await session.execute(select(
                SemanticAccessBootstrapMappingModel,
            ).where(
                SemanticAccessBootstrapMappingModel.run_id == run.id,
            ).order_by(SemanticAccessBootstrapMappingModel.table_id))).scalars())
            accepted_count = sum(
                1 for row in all_mappings
                if row.accepted and row.status == "accepted"
            )
            rejected_count = sum(1 for row in all_mappings if row.status == "rejected")
            run.summary_json = {
                **dict(run.summary_json or {}),
                "row_ownership_accepted_count": accepted_count,
                "row_ownership_rejected_count": rejected_count,
                "row_level_configured": accepted_count > 0,
            }
            run.revision += 1
            await session.flush()
            return {
                "run": self._run_payload(run),
                "ownership_mappings": [
                    self._mapping_suggestion_payload(row) for row in all_mappings
                ],
                "targets": [self._target_payload(target) for target in targets],
            }

    async def review_decisions(
        self,
        workspace_id: str,
        run_id: int,
        actor_id: str,
        expected_revision: int,
        decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not decisions or len(decisions) > 1000:
            raise SemanticAccessBootstrapError(
                "批量审核需要提交 1 到 1000 个决策", code="review_decisions_invalid",
            )
        pairs = [
            (int(row.get("target_suggestion_id") or 0), int(row.get("table_id") or 0))
            for row in decisions
        ]
        if len(set(pairs)) != len(pairs):
            raise SemanticAccessBootstrapError(
                "批量审核包含重复单元格", code="review_decisions_duplicated",
            )
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id, lock=True)
            self._assert_reviewable(run)
            self._assert_revision(run, expected_revision)
            target_ids = sorted({target_id for target_id, _table_id in pairs})
            targets = list((await session.execute(select(SemanticAccessBootstrapTargetModel).where(
                SemanticAccessBootstrapTargetModel.run_id == run.id,
                SemanticAccessBootstrapTargetModel.id.in_(target_ids),
            ).with_for_update())).scalars())
            target_by_id = {int(row.id): row for row in targets}
            if set(target_ids) != set(target_by_id):
                raise SemanticAccessBootstrapError(
                    "批量审核包含无效目标", code="suggestion_not_found", status_code=404,
                )
            decisions_by_target: dict[int, list[dict[str, Any]]] = {}
            for decision in decisions:
                action = decision.get("action")
                if action not in {"accept", "reject", "reset"}:
                    raise SemanticAccessBootstrapError(
                        "批量审核操作无效", code="review_action_invalid",
                    )
                decisions_by_target.setdefault(int(decision["target_suggestion_id"]), []).append(decision)

            for target_id, target_decisions in decisions_by_target.items():
                target = target_by_id[target_id]
                candidates = deepcopy(target.candidates_json or [])
                candidate_by_table = {
                    int(row.get("table_id") or 0): row for row in candidates
                }
                rules = {
                    int(row["table_id"]): deepcopy(row)
                    for row in (target.definition_json or {}).get("tables") or []
                }
                for decision in target_decisions:
                    table_id = int(decision["table_id"])
                    candidate = candidate_by_table.get(table_id)
                    if candidate is None:
                        raise SemanticAccessBootstrapError(
                            "该表没有可审核的 AI 建议",
                            code="candidate_not_found",
                            details={"target_suggestion_id": target_id, "table_id": table_id},
                        )
                    action = decision["action"]
                    if action == "accept":
                        rules[table_id] = self._rule_from_candidate(candidate)
                        candidate["review_state"] = "accepted"
                        for field in candidate.get("field_suggestions") or []:
                            field["review_state"] = "accepted"
                    elif action == "reject":
                        rules.pop(table_id, None)
                        candidate["review_state"] = "rejected"
                        for field in candidate.get("field_suggestions") or []:
                            field["review_state"] = "rejected"
                    else:
                        candidate["review_state"] = "pending"
                        for field in candidate.get("field_suggestions") or []:
                            field["review_state"] = "pending"
                        if candidate.get("selected"):
                            rules[table_id] = self._rule_from_candidate(candidate)
                        else:
                            rules.pop(table_id, None)
                definition = {
                    **dict(target.definition_json or {}),
                    "name": target.target_label,
                    "tables": list(rules.values()),
                }
                await self._validate_definition_assets(session, run, definition)
                target.definition_json = definition
                target.candidates_json = candidates
                target.included = bool(rules)
                target.status = "reviewed"
                target.edited_by = actor_id

            await self._revalidate_targets(session, run, targets)
            run.revision += 1
            await session.flush()
            return {
                "run": self._run_payload(run),
                "targets": [self._target_payload(row) for row in targets],
            }

    async def _validate_mapping_payload(
        self,
        session,
        run,
        table_id: int,
        payload: dict[str, Any],
        validation: dict[str, Any] | None = None,
    ) -> None:
        column_id = payload.get("org_column_id")
        if column_id is None:
            raise SemanticAccessBootstrapError(
                "组织归属字段不能为空", code="ownership_column_required",
            )
        column = (await session.execute(select(SemanticColumnModel).where(
            SemanticColumnModel.workspace_id == run.workspace_id,
            SemanticColumnModel.datasource_id == run.datasource_id,
            SemanticColumnModel.table_id == table_id,
            SemanticColumnModel.id == int(column_id),
            SemanticColumnModel.status == "confirmed",
            SemanticColumnModel.sync_state == "current",
            SemanticColumnModel.is_queryable == True,  # noqa: E712
        ))).scalar_one_or_none()
        if column is None:
            raise SemanticAccessBootstrapError(
                "组织归属字段无效", code="ownership_column_invalid",
            )
        value_kind = payload.get("org_value_kind")
        if value_kind not in {"id", "code", "external"}:
            raise SemanticAccessBootstrapError(
                "组织归属值类型无效", code="ownership_value_kind_invalid",
            )
        value_mapping = payload.get("org_value_mapping") or {}
        if not isinstance(value_mapping, dict):
            raise SemanticAccessBootstrapError(
                "组织值映射格式无效", code="ownership_value_mapping_invalid",
            )
        bindings = value_mapping.get("bindings") or []
        if not isinstance(bindings, list):
            raise SemanticAccessBootstrapError(
                "组织值映射格式无效", code="ownership_value_mapping_invalid",
            )
        source_values = list((validation or {}).get("source_values") or [])
        source_keys = {
            (str(row.get("source_type") or ""), str(row.get("source_value")))
            for row in source_values if isinstance(row, dict)
        }
        binding_keys: set[tuple[str, str]] = set()
        requested_org_ids: set[int] = set()
        for binding in bindings:
            if not isinstance(binding, dict):
                raise SemanticAccessBootstrapError(
                    "组织值映射格式无效", code="ownership_value_mapping_invalid",
                )
            source_type = str(binding.get("source_type") or "")
            source_value = binding.get("source_value")
            if (
                source_type not in {"string", "integer"}
                or isinstance(source_value, (bool, list, dict))
                or source_type == "string" and not isinstance(source_value, str)
                or source_type == "integer" and not isinstance(source_value, int)
            ):
                raise SemanticAccessBootstrapError(
                    "组织源值格式无效", code="ownership_source_value_invalid",
                )
            key = (source_type, str(source_value))
            if key in binding_keys:
                raise SemanticAccessBootstrapError(
                    "组织源值重复", code="ownership_source_value_duplicated",
                )
            if source_keys and key not in source_keys:
                raise SemanticAccessBootstrapError(
                    "组织值映射包含探测范围外的源值", code="ownership_source_value_unknown",
                )
            binding_keys.add(key)
            if binding.get("target_kind") == "org_unit":
                org_unit_id = int(binding.get("org_unit_id") or 0)
                if not org_unit_id:
                    raise SemanticAccessBootstrapError(
                        "组织源值缺少目标部门", code="ownership_target_required",
                    )
                requested_org_ids.add(org_unit_id)
            elif binding.get("target_kind") == "unowned":
                if not binding.get("manual_unowned") or not str(binding.get("reason") or "").strip():
                    raise SemanticAccessBootstrapError(
                        "非空值标记为无归属需要明确确认并填写原因",
                        code="ownership_unowned_confirmation_required",
                    )
            else:
                raise SemanticAccessBootstrapError(
                    "组织源值目标无效", code="ownership_target_invalid",
                )
        departments = list((await session.execute(select(DepartmentModel).where(
            DepartmentModel.workspace_id == run.workspace_id,
            DepartmentModel.status == True,  # noqa: E712
        ))).scalars())
        department_by_id = {int(row.id): row for row in departments}
        if not requested_org_ids.issubset(department_by_id):
            raise SemanticAccessBootstrapError(
                "组织值映射包含无效目标部门", code="ownership_target_invalid",
            )
        if value_kind == "external" and binding_keys != source_keys:
            raise SemanticAccessBootstrapError(
                "仍有非空归属值未映射",
                code="ownership_values_unresolved",
                details={"unresolved_count": len(source_keys - binding_keys)},
            )
        if value_kind == "id":
            valid_ids = set(department_by_id)
            if any(
                row.get("source_type") != "integer"
                or int(row.get("source_value")) not in valid_ids
                for row in source_values
            ):
                raise SemanticAccessBootstrapError(
                    "归属字段包含无法匹配的部门 ID", code="ownership_values_unresolved",
                )
        if value_kind == "code":
            codes = [str(row.code) for row in departments if row.code not in {None, ""}]
            if len(codes) != len(set(codes)):
                raise SemanticAccessBootstrapError(
                    "工作区部门编码不唯一", code="organization_code_conflict",
                )
            valid_codes = set(codes)
            if any(
                row.get("source_type") != "string"
                or str(row.get("source_value")) not in valid_codes
                for row in source_values
            ):
                raise SemanticAccessBootstrapError(
                    "归属字段包含无法匹配的部门编码", code="ownership_values_unresolved",
                )
        expected_fingerprint = (validation or {}).get("source_domain_fingerprint")
        if expected_fingerprint and value_mapping.get("source_domain_fingerprint") != expected_fingerprint:
            raise SemanticAccessBootstrapError(
                "归属值域指纹不一致", code="ownership_value_domain_changed", status_code=409,
            )

    def _assert_reviewable(self, run) -> None:
        if run.status != "review_ready":
            raise SemanticAccessBootstrapError(
                "当前运行不能编辑或发布", code="run_state_invalid", status_code=409,
            )

    async def apply_run(
        self,
        workspace_id: str,
        run_id: int,
        actor_id: str,
        expected_revision: int,
        confirm_warnings: bool = False,
    ) -> dict[str, Any]:
        try:
            return await self._apply_run_transaction(
                workspace_id,
                run_id,
                actor_id,
                expected_revision,
                confirm_warnings,
            )
        except SemanticAccessBootstrapError as exc:
            if exc.code in {
                "schema_changed",
                "organization_changed",
                "mapping_conflict",
                "target_binding_conflict",
                "target_configured_conflict",
                "target_revision_conflict",
            }:
                try:
                    await self._mark_run_stale(workspace_id, run_id, str(exc))
                except Exception:  # noqa: BLE001
                    logger.warning("failed to mark conflicted bootstrap run stale: %s", run_id, exc_info=True)
            raise

    async def _mark_run_stale(
        self, workspace_id: str, run_id: int, message: str,
    ) -> None:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            run = await self._run(session, workspace_id, run_id, lock=True)
            if run.status != "review_ready":
                return
            run.status = "stale"
            run.stage = "stale"
            run.error_message = message[:2000]
            run.completed_at = datetime.now()
            run.revision += 1

    async def _apply_run_transaction(
        self,
        workspace_id: str,
        run_id: int,
        actor_id: str,
        expected_revision: int,
        confirm_warnings: bool = False,
    ) -> dict[str, Any]:
        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id, lock=True)
            self._assert_reviewable(run)
            self._assert_revision(run, expected_revision)
            _datasource, _tables, _columns, _metrics, semantic_fingerprint = await self._governed_assets(
                session, workspace_id, run.datasource_id,
            )
            _departments, organization_fingerprint = await self._organization_snapshot(session, workspace_id)
            if semantic_fingerprint != run.schema_fingerprint:
                raise SemanticAccessBootstrapError("语义 Schema 已变化，请重新生成", code="schema_changed", status_code=409)
            if organization_fingerprint != run.organization_fingerprint:
                raise SemanticAccessBootstrapError("组织架构已变化，请重新生成", code="organization_changed", status_code=409)

            mapping_suggestions = list((await session.execute(select(
                SemanticAccessBootstrapMappingModel,
            ).where(
                SemanticAccessBootstrapMappingModel.run_id == run.id,
            ).with_for_update())).scalars())
            targets = list((await session.execute(select(SemanticAccessBootstrapTargetModel).where(
                SemanticAccessBootstrapTargetModel.run_id == run.id,
            ).with_for_update())).scalars())
            included_targets = [
                row for row in targets
                if row.included and bool((row.definition_json or {}).get("tables"))
            ]
            if not included_targets:
                raise SemanticAccessBootstrapError("没有可发布的目标策略", code="targets_empty")

            for mapping in mapping_suggestions:
                current = (await session.execute(select(SemanticOwnershipMappingModel).where(
                    SemanticOwnershipMappingModel.workspace_id == workspace_id,
                    SemanticOwnershipMappingModel.datasource_id == run.datasource_id,
                    SemanticOwnershipMappingModel.table_id == mapping.table_id,
                ).with_for_update())).scalar_one_or_none()
                if _mapping_payload(current) != dict(mapping.base_mapping_json or {}):
                    raise SemanticAccessBootstrapError(
                        f"{mapping.table_label} 的归属映射已变化，请重新生成",
                        code="mapping_conflict",
                        status_code=409,
                        details={"table_id": mapping.table_id},
                    )

            binding_by_target: dict[tuple[str, str], SemanticPolicyBindingModel] = {}
            for target in included_targets:
                binding = (await session.execute(select(SemanticPolicyBindingModel).where(
                    SemanticPolicyBindingModel.workspace_id == workspace_id,
                    SemanticPolicyBindingModel.datasource_id == run.datasource_id,
                    SemanticPolicyBindingModel.target_type == target.target_type,
                    SemanticPolicyBindingModel.target_id == target.target_id,
                ).with_for_update())).scalar_one_or_none()
                current_binding_id = int(binding.id) if binding is not None else None
                if current_binding_id != target.base_binding_id:
                    raise SemanticAccessBootstrapError(
                        f"{target.target_label} 的目标绑定已变化，请重新生成",
                        code="target_binding_conflict",
                        status_code=409,
                    )
                expected_active_version_id = (
                    target.validation_json or {}
                ).get("base_active_version_id")
                current_active_version_id = (
                    int(binding.active_version_id)
                    if binding and binding.active_version_id else None
                )
                if current_active_version_id != expected_active_version_id:
                    raise SemanticAccessBootstrapError(
                        f"{target.target_label} 的生效版本已变化，请刷新后重试",
                        code="target_configured_conflict",
                        status_code=409,
                        details={"target_type": target.target_type, "target_id": target.target_id},
                    )
                if int(getattr(binding, "revision", 0) or 0) != int(target.base_revision or 0):
                    raise SemanticAccessBootstrapError(
                        f"{target.target_label} 的策略修订已变化，请重新生成",
                        code="target_revision_conflict",
                        status_code=409,
                    )
                if binding is None:
                    binding = SemanticPolicyBindingModel(
                        workspace_id=workspace_id,
                        datasource_id=run.datasource_id,
                        target_type=target.target_type,
                        target_id=target.target_id,
                        include_descendants=(target.target_type == "org_unit"),
                        status=True,
                        created_by=actor_id,
                    )
                    session.add(binding)
                    await session.flush()
                binding.include_descendants = target.target_type == "org_unit"
                binding.status = True
                binding_by_target[(target.target_type, target.target_id)] = binding

            mapping_overrides = self._mapping_overrides(mapping_suggestions)
            await self._revalidate_targets(session, run, included_targets)
            blockers = [
                {"target": row.target_label, **issue}
                for row in included_targets
                for issue in (row.validation_json or {}).get("blockers") or []
            ]
            warnings = [
                {"target": row.target_label, **issue}
                for row in included_targets
                for issue in (row.validation_json or {}).get("warnings") or []
            ]
            if blockers:
                raise SemanticAccessBootstrapError(
                    "审核方案存在阻断项", code="validation_blocked",
                    details={"blockers": blockers, "warnings": warnings},
                )
            if warnings and not confirm_warnings:
                raise SemanticAccessBootstrapError(
                    "审核方案存在待确认提醒",
                    code="warnings_confirmation_required",
                    status_code=409,
                    details={"warnings": warnings},
                )

            mapping_audits = []
            for mapping in mapping_suggestions:
                if not mapping.accepted or mapping.status != "accepted":
                    continue
                _row, before, after = await upsert_ownership_mapping_in_session(
                    session,
                    workspace_id=workspace_id,
                    datasource_id=run.datasource_id,
                    table_id=mapping.table_id,
                    payload=dict(mapping.proposed_mapping_json or {}),
                    actor_id=actor_id,
                )
                audit = await record_authorization_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    action="semantic_ownership_mapping.ai_bootstrap",
                    target_type="semantic_ownership_mapping",
                    target_id=mapping.table_id,
                    before=before,
                    after=after,
                    reason=f"bootstrap_run:{run.id}",
                )
                mapping.status = "applied"
                mapping_audits.append(audit.id)

            target_results = []
            for target in included_targets:
                binding = binding_by_target[(target.target_type, target.target_id)]
                previous = {
                    "active_version_id": binding.active_version_id,
                    "revision": binding.revision,
                }
                try:
                    evidence_source = bool(run.evidence_set_id)
                    version, _compilation, version_warnings = await persist_target_policy_version_in_session(
                        session,
                        binding,
                        dict(target.definition_json or {}),
                        actor_id,
                        source_text=(
                            f"访问依据集 #{run.evidence_set_id}"
                            if evidence_source else f"AI 首次配置 run#{run.id}"
                        ),
                        source_type=(
                            "evidence_bootstrap" if evidence_source else "ai_bootstrap"
                        ),
                        source_ref=(
                            f"evidence_set:{run.evidence_set_id}"
                            if evidence_source else f"bootstrap_run:{run.id}"
                        ),
                        confirm_warnings=confirm_warnings,
                        mapping_overrides=mapping_overrides,
                    )
                except SemanticBindingError as exc:
                    raise SemanticAccessBootstrapError(
                        str(exc), code=exc.code, status_code=exc.status_code, details=exc.details,
                    ) from exc
                audit = await record_authorization_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    action="semantic_policy.ai_bootstrap.activate",
                    target_type=f"semantic_{target.target_type}",
                    target_id=target.target_id,
                    before=previous,
                    after={"active_version_id": version.id, "revision": binding.revision},
                    reason=f"bootstrap_run:{run.id}",
                )
                target.status = "applied"
                target_results.append({
                    "target_type": target.target_type,
                    "target_id": target.target_id,
                    "target_label": target.target_label,
                    "binding_id": binding.id,
                    "version_id": version.id,
                    "version": version.version,
                    "warnings": version_warnings,
                    "audit_id": audit.id,
                })

            authorization_revision = await bump_authorization_revision(session, workspace_id)
            batch_audit = await record_authorization_audit(
                session,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="semantic_access.ai_bootstrap.apply",
                target_type="semantic_access_bootstrap_run",
                target_id=run.id,
                before={"status": run.status, "revision": run.revision},
                after={
                    "target_count": len(target_results),
                    "mapping_count": len(mapping_audits),
                    "authorization_revision": authorization_revision,
                },
            )
            run.status = "applied"
            run.stage = "applied"
            run.progress = 100
            run.applied_by = actor_id
            run.applied_at = datetime.now()
            run.completed_at = run.applied_at
            run.revision += 1
            run.summary_json = {
                **(run.summary_json or {}),
                "applied_target_count": len(target_results),
                "applied_mapping_count": len(mapping_audits),
                "authorization_revision": authorization_revision,
                "batch_audit_id": batch_audit.id,
            }
            if run.evidence_set_id:
                evidence_set = (await session.execute(select(
                    SemanticAccessEvidenceSetModel,
                ).where(
                    SemanticAccessEvidenceSetModel.id == run.evidence_set_id,
                    SemanticAccessEvidenceSetModel.workspace_id == workspace_id,
                ).with_for_update())).scalar_one_or_none()
                if not evidence_set:
                    raise SemanticAccessBootstrapError(
                        "关联的访问依据集不存在",
                        code="evidence_set_not_found",
                        status_code=409,
                    )
                datasource = await self._datasource(
                    session, workspace_id, run.datasource_id,
                )
                datasource.access_bootstrap_required = False
                datasource.active_evidence_set_id = evidence_set.id
                evidence_set.status = "published"
                evidence_set.confirmed_by = actor_id
                evidence_set.confirmed_at = datetime.now()
                evidence_set.published_at = datetime.now()
                evidence_set.compile_result_json = {
                    "fingerprint": _stable_fingerprint([
                        {
                            "target_type": row.target_type,
                            "target_id": row.target_id,
                            "definition": row.definition_json or {},
                        }
                        for row in included_targets
                    ]),
                    "bootstrap_run_id": run.id,
                    "targets": target_results,
                }
                evidence_set.revision += 1
            return {
                "run": self._run_payload(run),
                "targets": target_results,
                "mapping_audit_ids": mapping_audits,
                "authorization_revision": authorization_revision,
                "batch_audit_id": batch_audit.id,
            }

    async def preview_run(
        self,
        workspace_id: str,
        run_id: int,
        actor_id: str,
        user_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        from app.services.semantic_access_policy_service import get_semantic_access_policy_service

        db = get_async_db_manager()
        async with db.session_scope() as session:
            await self._assert_workspace_manage(session, workspace_id, actor_id)
            run = await self._run(session, workspace_id, run_id)
            if run.status != "review_ready":
                raise SemanticAccessBootstrapError("当前运行不能预览", code="run_state_invalid", status_code=409)
            targets = list((await session.execute(select(SemanticAccessBootstrapTargetModel).where(
                SemanticAccessBootstrapTargetModel.run_id == run.id,
                SemanticAccessBootstrapTargetModel.included == True,  # noqa: E712
            ))).scalars())
            mappings = list((await session.execute(select(SemanticAccessBootstrapMappingModel).where(
                SemanticAccessBootstrapMappingModel.run_id == run.id,
            ))).scalars())
            mapping_overrides = self._mapping_overrides(mappings)
            await self._revalidate_targets(session, run, targets)
            blockers = [
                {"target": row.target_label, **issue}
                for row in targets for issue in (row.validation_json or {}).get("blockers") or []
            ]
            if blockers:
                return {"run_id": run.id, "items": [], "missing_departments": [], "blockers": blockers}
            if user_ids:
                selected_user_ids = list(dict.fromkeys(str(item) for item in user_ids))[:50]
                missing_departments: list[dict[str, Any]] = []
            else:
                selected_user_ids, missing_departments = await self._representative_users(
                    session, workspace_id, targets,
                )
            datasource = await self._datasource(session, workspace_id, run.datasource_id)
            tables = list((await session.execute(select(SemanticTableModel).where(
                SemanticTableModel.workspace_id == workspace_id,
                SemanticTableModel.datasource_id == run.datasource_id,
                SemanticTableModel.status == "confirmed",
                SemanticTableModel.sync_state == "current",
                SemanticTableModel.is_queryable == True,  # noqa: E712
            ).order_by(SemanticTableModel.id))).scalars())
            columns = list((await session.execute(select(SemanticColumnModel).where(
                SemanticColumnModel.workspace_id == workspace_id,
                SemanticColumnModel.datasource_id == run.datasource_id,
            ))).scalars())
            columns_by_table: dict[int, dict[int, Any]] = {}
            for column in columns:
                columns_by_table.setdefault(int(column.table_id), {})[int(column.id)] = column
            access_service = get_semantic_access_policy_service()

            compiled_targets = []
            for target in targets:
                binding = SimpleNamespace(
                    id=f"bootstrap:{target.id}",
                    workspace_id=workspace_id,
                    datasource_id=run.datasource_id,
                    target_type=target.target_type,
                    target_id=target.target_id,
                    include_descendants=target.target_type == "org_unit",
                )
                prepared, _ownership = await _prepare_definition_v3(
                    session,
                    binding,
                    dict(target.definition_json or {}),
                    actor_id,
                    mapping_overrides=mapping_overrides,
                )
                compilation = await access_service._compile_definition(
                    session,
                    workspace_id,
                    run.datasource_id,
                    prepared,
                    schema_fingerprint=datasource.schema_fingerprint,
                )
                compiled_targets.append((target, compilation["effects"]))

            items = []
            for user_id in selected_user_ids:
                user = await session.get(UserModel, user_id)
                if not user or user.workspace_id != workspace_id:
                    continue
                context = await build_effective_access_context(session, workspace_id, user_id)
                runtime = await load_bound_semantic_runtime(workspace_id, run.datasource_id, user_id)
                runtime = runtime or {"is_admin": False, "effects_by_asset": {}, "policy_ids": []}
                if runtime.get("is_admin"):
                    items.append({
                        "user_id": user_id,
                        "username": user.username,
                        "assignments": context.to_dict().get("assignments") or [],
                        "semantic": {
                            "is_admin": True,
                            "decisions": [{
                                "asset_key": f"table:{table.id}",
                                "allowed": True,
                                "reason": "workspace_admin_bypass",
                                "sources": [],
                            } for table in tables],
                            "table_predicates": {
                                f"table:{table.id}": "" for table in tables
                            },
                        },
                    })
                    continue
                effects_by_asset = deepcopy(runtime.get("effects_by_asset") or {})
                assignment_org_ids = {int(item.org_unit_id) for item in context.assignments}
                parent_by_id = dict((await session.execute(select(
                    DepartmentModel.id, DepartmentModel.parent_id,
                ).where(DepartmentModel.workspace_id == workspace_id))).all())
                ancestors = set(assignment_org_ids)
                for org_id in list(assignment_org_ids):
                    current = parent_by_id.get(org_id)
                    while current is not None and int(current) not in ancestors:
                        ancestors.add(int(current))
                        current = parent_by_id.get(current)
                for target, effects in compiled_targets:
                    applies = target.target_type == "baseline" or (
                        target.target_type == "org_unit" and int(target.target_id) in ancestors
                    )
                    if not applies:
                        continue
                    for effect in effects:
                        key = f'{effect["asset_type"]}:{effect["asset_id"]}'
                        effects_by_asset.setdefault(key, []).append({
                            **effect,
                            "policy_id": f"bootstrap:{target.id}",
                            "binding_id": f"bootstrap:{target.id}",
                            "target_type": target.target_type,
                            "target_id": target.target_id,
                        })
                for table in tables:
                    effects_by_asset.setdefault(f"table:{table.id}", [])
                decisions = []
                for asset_key, effects in effects_by_asset.items():
                    denied = any(row["effect_type"] == "hidden" for row in effects)
                    allowed = any(row["effect_type"] == "visible" for row in effects) and not denied
                    decisions.append({
                        "asset_key": asset_key,
                        "allowed": allowed,
                        "reason": "explicit_hidden" if denied else "explicit_visible" if allowed else "default_deny",
                        "sources": [{
                            "binding_id": row.get("binding_id"),
                            "version_id": row.get("policy_id"),
                            "target_type": row.get("target_type"),
                            "target_id": row.get("target_id"),
                            "effect_type": row.get("effect_type"),
                        } for row in effects],
                    })
                primary = next((row for row in context.assignments if row.is_primary), None)
                user_access = {
                    "user_id": user_id,
                    "username": user.username,
                    "dept_id": getattr(primary, "org_unit_id", None),
                    "scope_dept_ids": sorted(assignment_org_ids),
                }
                predicates: dict[str, str] = {}
                for key, effects in effects_by_asset.items():
                    if not key.startswith("table:"):
                        continue
                    if any(row["effect_type"] == "hidden" for row in effects):
                        predicates[key] = "1=0"
                        continue
                    visible = [row for row in effects if row["effect_type"] == "visible"]
                    if not visible:
                        predicates[key] = "1=0"
                        continue
                    if any((row.get("condition_json") or {}).get("row_scope", {}).get("type") == "all" for row in visible):
                        predicates[key] = ""
                        continue
                    table_id = int(key.split(":", 1)[1])
                    compiled = []
                    for effect in effects:
                        if effect["effect_type"] != "row_filter":
                            continue
                        try:
                            compiled.append(access_service.compile_condition(
                                effect.get("condition_json") or {},
                                columns_by_table.get(table_id, {}),
                                alias="t",
                                table_id=table_id,
                                user_access=user_access,
                            ))
                        except Exception:
                            compiled.append("1=0")
                    compiled = [row for row in compiled if row]
                    predicates[key] = " OR ".join(f"({row})" for row in compiled) if compiled else "1=0"
                items.append({
                    "user_id": user_id,
                    "username": user.username,
                    "assignments": context.to_dict().get("assignments") or [],
                    "semantic": {
                        "decisions": decisions,
                        "table_predicates": predicates,
                    },
                })
            return {
                "run_id": run.id,
                "items": items,
                "missing_departments": missing_departments,
                "blockers": [],
            }

    async def _representative_users(self, session, workspace_id: str, targets) -> tuple[list[str], list[dict[str, Any]]]:
        now = datetime.utcnow()
        org_targets = [row for row in targets if row.target_type == "org_unit"]
        selected: list[str] = []
        missing: list[dict[str, Any]] = []
        for target in org_targets:
            org_ids = list((await session.execute(select(DepartmentModel.id).where(
                DepartmentModel.workspace_id == workspace_id,
                DepartmentModel.status == True,  # noqa: E712
                or_(
                    DepartmentModel.id == int(target.target_id),
                    DepartmentModel.ancestors.like(f"%/{target.target_id}/%"),
                ),
            ))).scalars())
            user_id = (await session.execute(select(AssignmentModel.user_id).join(
                PositionModel, PositionModel.id == AssignmentModel.position_id,
            ).where(
                AssignmentModel.workspace_id == workspace_id,
                AssignmentModel.status == True,  # noqa: E712
                AssignmentModel.is_primary == True,  # noqa: E712
                AssignmentModel.starts_at <= now,
                or_(AssignmentModel.ends_at.is_(None), AssignmentModel.ends_at > now),
                PositionModel.org_unit_id.in_(org_ids or [-1]),
            ).order_by(AssignmentModel.user_id).limit(1))).scalar_one_or_none()
            if user_id:
                selected.append(str(user_id))
            else:
                missing.append({"target_id": target.target_id, "target_label": target.target_label})
        return list(dict.fromkeys(selected)), missing


_service: SemanticAccessBootstrapService | None = None


def get_semantic_access_bootstrap_service() -> SemanticAccessBootstrapService:
    global _service
    if _service is None:
        _service = SemanticAccessBootstrapService()
    return _service
