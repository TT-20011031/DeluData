"""Evidence-driven semantic governance orchestration and decision service."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func, select

from app.core.db.database import get_async_db_manager
from app.core.db.read_only_executor import ReadOnlyExecutor, get_readonly_pool
from app.models.config.db_config import get_user_db_config_async, get_workspace_db_config_async
from app.models.config.semantic import (
    SemanticBusinessSuggestionModel,
    SemanticColumnModel,
    SemanticColumnProfileModel,
    SemanticDatasourceModel,
    SemanticEvaluationRunModel,
    SemanticEvidenceFactModel,
    SemanticGovernanceCandidateModel,
    SemanticGovernanceEventModel,
    SemanticGovernancePolicyModel,
    SemanticGovernanceRunModel,
    SemanticMetricModel,
    SemanticQueryRunModel,
    SemanticRelationshipModel,
    SemanticTableModel,
)
from app.models.config.sql_example import SqlExampleModel
from app.services.semantic_evidence import (
    SCORE_VERSION,
    SOURCE_RELIABILITY,
    auto_eligible,
    compare_execution_results,
    score_evidence,
    stable_fingerprint,
)
from app.services.semantic_query_service import SemanticQueryError

logger = logging.getLogger(__name__)

PROFILE_TTL_DAYS = 7
RUNTIME_EVIDENCE_TTL_DAYS = 30
RUNTIME_TRIGGER_MINUTES = 30
RUNTIME_TRIGGER_QUERY_COUNT = 10
DEFAULT_RUN_AFTER_SCAN = False
DEFAULT_SAMPLE_ROW_LIMIT = 1000
DEFAULT_TABLE_TIMEOUT_SEC = 3
DEFAULT_MAX_TABLES_PER_RUN = 5
DEFAULT_MAX_RUN_SECONDS = 90
DEFAULT_AUTO_ACTIONS = [
    "refresh_evidence",
    "supersede_duplicate",
    "block_conflict",
    "mark_sensitive",
    "disable_invalid_asset",
]
SAFE_AUTO_CANDIDATE_TYPES = {"mark_sensitive", "disable_invalid_asset", "supersede_duplicate", "block_conflict"}
TERMINAL_CANDIDATE_STATUSES = {"accepted", "rejected", "auto_applied", "superseded", "expired", "rolled_back"}
ACTIVE_RUN_STATUSES = {"pending", "running", "cancel_requested"}
RUNNING_RUN_STATUSES = {"running", "cancel_requested"}
STRONG_BUSINESS_EVIDENCE_SOURCES = {
    "human_confirmation",
    "golden_sql",
    "aggregate_profile",
    "runtime_pattern",
    "shadow_equivalent",
    "evaluation_equivalent",
}


class GovernanceRunCancelled(Exception):
    """Raised when a semantic governance run is cancelled cooperatively."""


def _quote_identifier(value: str) -> str:
    return f"`{str(value).replace('`', '``')}`"


def _quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _profile_is_numeric(data_type: str) -> bool:
    return any(token in str(data_type).lower() for token in ("int", "decimal", "numeric", "float", "double", "real"))


def _profile_is_temporal(data_type: str) -> bool:
    return any(token in str(data_type).lower() for token in ("date", "time", "year"))


def _profile_is_string(data_type: str) -> bool:
    return any(token in str(data_type).lower() for token in ("char", "text", "enum", "set"))


def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class SemanticAutoGovernanceService:
    async def _active_datasource(self, session, workspace_id: str) -> SemanticDatasourceModel:
        datasource = (
            await session.execute(
                select(SemanticDatasourceModel)
                .where(SemanticDatasourceModel.workspace_id == workspace_id, SemanticDatasourceModel.is_active == True)  # noqa: E712
                .order_by(SemanticDatasourceModel.updated_at.desc())
            )
        ).scalars().first()
        if not datasource:
            raise SemanticQueryError("datasource_missing", "请先建立语义数据源", safe_to_fallback=False)
        return datasource

    async def _get_or_create_policy(self, session, workspace_id: str, datasource_id: int) -> SemanticGovernancePolicyModel:
        policy = (
            await session.execute(
                select(SemanticGovernancePolicyModel).where(
                    SemanticGovernancePolicyModel.workspace_id == workspace_id,
                    SemanticGovernancePolicyModel.datasource_id == datasource_id,
                )
            )
        ).scalar_one_or_none()
        if not policy:
            policy = SemanticGovernancePolicyModel(
                workspace_id=workspace_id,
                datasource_id=datasource_id,
                enabled=True,
                observe_only=True,
                run_after_scan=DEFAULT_RUN_AFTER_SCAN,
                sample_row_limit=DEFAULT_SAMPLE_ROW_LIMIT,
                table_timeout_sec=DEFAULT_TABLE_TIMEOUT_SEC,
                max_tables_per_run=DEFAULT_MAX_TABLES_PER_RUN,
                max_run_seconds=DEFAULT_MAX_RUN_SECONDS,
                auto_action_types=DEFAULT_AUTO_ACTIONS,
            )
            session.add(policy)
            await session.flush()
        return policy

    def _policy_payload(self, policy: SemanticGovernancePolicyModel) -> dict[str, Any]:
        return {
            "id": policy.id,
            "enabled": bool(policy.enabled),
            "observe_only": bool(policy.observe_only),
            "run_after_scan": bool(policy.run_after_scan),
            "exact_row_threshold": policy.exact_row_threshold,
            "sample_row_limit": policy.sample_row_limit,
            "table_timeout_sec": policy.table_timeout_sec,
            "max_tables_per_run": policy.max_tables_per_run,
            "max_run_seconds": policy.max_run_seconds,
            "review_threshold": policy.review_threshold,
            "high_confidence_threshold": policy.high_confidence_threshold,
            "auto_apply_threshold": policy.auto_apply_threshold,
            "min_auto_evidence_sources": policy.min_auto_evidence_sources,
            "auto_action_types": policy.auto_action_types or [],
            "policy_version": policy.policy_version,
            "updated_at": _serialize_datetime(policy.updated_at),
        }

    async def get_policy(self, workspace_id: str) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = await self._active_datasource(session, workspace_id)
            policy = await self._get_or_create_policy(session, workspace_id, datasource.id)
            return self._policy_payload(policy)

    async def update_policy(self, workspace_id: str, actor_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "enabled", "observe_only", "exact_row_threshold", "sample_row_limit",
            "table_timeout_sec", "max_tables_per_run", "max_run_seconds", "review_threshold",
            "high_confidence_threshold", "auto_apply_threshold", "min_auto_evidence_sources",
            "auto_action_types",
        }
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = await self._active_datasource(session, workspace_id)
            policy = await self._get_or_create_policy(session, workspace_id, datasource.id)
            for key, value in patch.items():
                if key in allowed:
                    setattr(policy, key, value)
            policy.auto_apply_threshold = max(0.98, float(policy.auto_apply_threshold))
            policy.min_auto_evidence_sources = max(2, int(policy.min_auto_evidence_sources))
            policy.auto_action_types = [
                item for item in (policy.auto_action_types or [])
                if item in DEFAULT_AUTO_ACTIONS
            ]
            policy.updated_by = actor_id
            policy.updated_at = datetime.now()
            session.add(SemanticGovernanceEventModel(
                workspace_id=workspace_id,
                datasource_id=datasource.id,
                actor_id=actor_id,
                action="governance_policy_updated",
                payload_json={key: value for key, value in patch.items() if key in allowed},
            ))
            await session.flush()
            return self._policy_payload(policy)

    async def create_run(
        self,
        workspace_id: str,
        actor_id: Optional[str],
        *,
        trigger_type: str = "manual",
        scan_id: Optional[int] = None,
    ) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = await self._active_datasource(session, workspace_id)
            await session.execute(
                select(SemanticDatasourceModel.id)
                .where(SemanticDatasourceModel.id == datasource.id)
                .with_for_update()
            )
            policy = await self._get_or_create_policy(session, workspace_id, datasource.id)
            active = (
                await session.execute(
                    select(SemanticGovernanceRunModel)
                    .where(
                        SemanticGovernanceRunModel.workspace_id == workspace_id,
                        SemanticGovernanceRunModel.datasource_id == datasource.id,
                        SemanticGovernanceRunModel.status.in_(list(ACTIVE_RUN_STATUSES)),
                    )
                    .order_by(SemanticGovernanceRunModel.created_at.desc())
                )
            ).scalars().first()
            if active:
                return self._run_payload(active, coalesced=True)
            run = SemanticGovernanceRunModel(
                workspace_id=workspace_id,
                datasource_id=datasource.id,
                trigger_type=trigger_type,
                triggered_by=actor_id,
                scan_id=scan_id,
                schema_fingerprint=datasource.schema_fingerprint,
                status="pending",
                stage="queued",
                progress=0,
                observe_only=bool(policy.observe_only),
                summary_json={},
                attempt=0,
                max_attempts=2,
                budget_json={
                    "exact_row_threshold": policy.exact_row_threshold,
                    "sample_row_limit": policy.sample_row_limit,
                    "table_timeout_sec": policy.table_timeout_sec,
                    "max_tables_per_run": policy.max_tables_per_run,
                    "max_run_seconds": policy.max_run_seconds,
                },
            )
            session.add(run)
            await session.flush()
            return self._run_payload(run)

    async def trigger_and_schedule(
        self,
        workspace_id: str,
        actor_id: Optional[str],
        *,
        trigger_type: str = "manual",
        scan_id: Optional[int] = None,
    ) -> dict[str, Any]:
        if trigger_type == "schema_apply":
            return {"run_id": None, "status": "skipped", "reason": "schema_apply_decoupled"}
        payload = await self.create_run(workspace_id, actor_id, trigger_type=trigger_type, scan_id=scan_id)
        return payload

    async def maybe_trigger_runtime_governance(
        self,
        workspace_id: str,
        actor_id: Optional[str],
    ) -> dict[str, Any]:
        now = datetime.now()
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = await self._active_datasource(session, workspace_id)
            policy = await self._get_or_create_policy(session, workspace_id, datasource.id)
            if not policy.enabled:
                return {"run_id": None, "status": "skipped", "reason": "policy_disabled"}
            active = (await session.execute(select(SemanticGovernanceRunModel).where(
                SemanticGovernanceRunModel.workspace_id == workspace_id,
                SemanticGovernanceRunModel.datasource_id == datasource.id,
                SemanticGovernanceRunModel.status.in_(list(ACTIVE_RUN_STATUSES)),
            ))).scalars().first()
            if active:
                return self._run_payload(active, coalesced=True)
            last_run = (await session.execute(select(SemanticGovernanceRunModel).where(
                SemanticGovernanceRunModel.workspace_id == workspace_id,
                SemanticGovernanceRunModel.datasource_id == datasource.id,
            ).order_by(SemanticGovernanceRunModel.created_at.desc()))).scalars().first()
            if last_run and last_run.created_at and last_run.created_at > now - timedelta(minutes=RUNTIME_TRIGGER_MINUTES):
                return {"run_id": None, "status": "skipped", "reason": "recent_runtime_run"}
            cutoff = last_run.created_at if last_run and last_run.created_at else now - timedelta(minutes=RUNTIME_TRIGGER_MINUTES)
            query_count = (await session.execute(select(func.count(SemanticQueryRunModel.id)).where(
                SemanticQueryRunModel.workspace_id == workspace_id,
                SemanticQueryRunModel.created_at > cutoff,
            ))).scalar_one()
            if int(query_count or 0) < RUNTIME_TRIGGER_QUERY_COUNT:
                return {
                    "run_id": None,
                    "status": "skipped",
                    "reason": "below_runtime_threshold",
                    "query_count": int(query_count or 0),
                }
        return await self.create_run(workspace_id, actor_id, trigger_type="runtime_threshold")

    def _run_payload(self, run: SemanticGovernanceRunModel, *, coalesced: bool = False) -> dict[str, Any]:
        return {
            "run_id": run.id,
            "status": run.status,
            "stage": run.stage,
            "progress": run.progress,
            "trigger_type": run.trigger_type,
            "scan_id": run.scan_id,
            "schema_fingerprint": run.schema_fingerprint,
            "observe_only": bool(run.observe_only),
            "summary": run.summary_json or {},
            "budget": run.budget_json or {},
            "error_message": run.error_message,
            "worker_id": run.worker_id,
            "attempt": int(run.attempt or 0),
            "max_attempts": int(run.max_attempts or 0),
            "lease_expires_at": _serialize_datetime(run.lease_expires_at),
            "heartbeat_at": _serialize_datetime(run.heartbeat_at),
            "cancel_requested_at": _serialize_datetime(run.cancel_requested_at),
            "retryable": run.status in {"failed", "partial", "cancelled"},
            "created_at": _serialize_datetime(run.created_at),
            "started_at": _serialize_datetime(run.started_at),
            "completed_at": _serialize_datetime(run.completed_at),
            "coalesced": coalesced,
        }

    async def list_runs(self, workspace_id: str, limit: int = 30) -> list[dict[str, Any]]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            rows = list((await session.execute(
                select(SemanticGovernanceRunModel)
                .where(SemanticGovernanceRunModel.workspace_id == workspace_id)
                .order_by(SemanticGovernanceRunModel.created_at.desc())
                .limit(min(max(limit, 1), 100))
            )).scalars())
            return [self._run_payload(row) for row in rows]

    async def get_run(self, workspace_id: str, run_id: int) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            run = (await session.execute(select(SemanticGovernanceRunModel).where(
                SemanticGovernanceRunModel.id == run_id,
                SemanticGovernanceRunModel.workspace_id == workspace_id,
            ))).scalar_one_or_none()
            if not run:
                raise SemanticQueryError("not_found", "治理运行不存在", safe_to_fallback=False)
            return self._run_payload(run)

    async def claim_next_run(
        self,
        *,
        worker_id: str,
        lease_timeout_sec: int,
        workspace_max_running: int = 1,
    ) -> Optional[dict[str, Any]]:
        lease_timeout_sec = max(30, int(lease_timeout_sec or 120))
        workspace_max_running = max(1, int(workspace_max_running or 1))
        now = datetime.now()
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            pending_runs = list((await session.execute(
                select(SemanticGovernanceRunModel)
                .where(SemanticGovernanceRunModel.status == "pending")
                .order_by(SemanticGovernanceRunModel.created_at.asc())
                .limit(25)
                .with_for_update(skip_locked=True)
            )).scalars())
            for run in pending_runs:
                running_count = (await session.execute(
                    select(func.count(SemanticGovernanceRunModel.id)).where(
                        SemanticGovernanceRunModel.workspace_id == run.workspace_id,
                        SemanticGovernanceRunModel.status.in_(list(RUNNING_RUN_STATUSES)),
                    )
                )).scalar_one()
                if int(running_count or 0) >= workspace_max_running:
                    continue
                run.status = "running"
                run.stage = "queued"
                run.worker_id = worker_id
                run.run_token = uuid.uuid4().hex
                run.lease_expires_at = now + timedelta(seconds=lease_timeout_sec)
                run.heartbeat_at = now
                run.started_at = run.started_at or now
                run.completed_at = None
                run.error_message = None
                run.cancel_requested_at = None
                run.attempt = int(run.attempt or 0) + 1
                await session.flush()
                payload = self._run_payload(run)
                payload["run_token"] = run.run_token
                return payload
        return None

    async def heartbeat_run(
        self,
        run_id: int,
        *,
        worker_id: str,
        run_token: str,
        lease_timeout_sec: int,
    ) -> bool:
        now = datetime.now()
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            run = (await session.execute(select(SemanticGovernanceRunModel).where(
                SemanticGovernanceRunModel.id == run_id,
                SemanticGovernanceRunModel.worker_id == worker_id,
                SemanticGovernanceRunModel.run_token == run_token,
                SemanticGovernanceRunModel.status.in_(list(RUNNING_RUN_STATUSES)),
            ))).scalar_one_or_none()
            if not run:
                return False
            run.heartbeat_at = now
            run.lease_expires_at = now + timedelta(seconds=max(30, int(lease_timeout_sec or 120)))
            return True

    async def cancel_run(self, workspace_id: str, actor_id: str, run_id: int) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            run = (await session.execute(select(SemanticGovernanceRunModel).where(
                SemanticGovernanceRunModel.id == run_id,
                SemanticGovernanceRunModel.workspace_id == workspace_id,
            ).with_for_update())).scalar_one_or_none()
            if not run:
                raise SemanticQueryError("not_found", "治理运行不存在", safe_to_fallback=False)
            now = datetime.now()
            if run.status == "pending":
                run.status = "cancelled"
                run.stage = "cancelled"
                run.progress = 100
                run.cancel_requested_at = now
                run.completed_at = now
                run.error_message = "治理候选生成已取消"
            elif run.status == "running":
                run.status = "cancel_requested"
                run.cancel_requested_at = now
                run.stage = "cancelling"
            session.add(SemanticGovernanceEventModel(
                workspace_id=workspace_id,
                datasource_id=run.datasource_id,
                actor_id=actor_id,
                action="governance_run_cancel_requested",
                payload_json={"run_id": run.id, "status": run.status},
            ))
            await session.flush()
            return self._run_payload(run)

    async def retry_run(self, workspace_id: str, actor_id: str, run_id: int) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            source = (await session.execute(select(SemanticGovernanceRunModel).where(
                SemanticGovernanceRunModel.id == run_id,
                SemanticGovernanceRunModel.workspace_id == workspace_id,
            ))).scalar_one_or_none()
            if not source:
                raise SemanticQueryError("not_found", "治理运行不存在", safe_to_fallback=False)
            if source.status not in {"failed", "partial", "cancelled"}:
                raise SemanticQueryError("invalid_state", "只有失败、部分完成或已取消的运行可以重试", safe_to_fallback=False)
            active = (await session.execute(select(SemanticGovernanceRunModel).where(
                SemanticGovernanceRunModel.workspace_id == workspace_id,
                SemanticGovernanceRunModel.datasource_id == source.datasource_id,
                SemanticGovernanceRunModel.status.in_(list(ACTIVE_RUN_STATUSES)),
            ).order_by(SemanticGovernanceRunModel.created_at.desc()))).scalars().first()
            if active:
                return self._run_payload(active, coalesced=True)
            policy = await self._get_or_create_policy(session, workspace_id, source.datasource_id)
            run = SemanticGovernanceRunModel(
                workspace_id=workspace_id,
                datasource_id=source.datasource_id,
                trigger_type="retry",
                triggered_by=actor_id,
                scan_id=source.scan_id,
                schema_fingerprint=source.schema_fingerprint,
                status="pending",
                stage="queued",
                progress=0,
                observe_only=bool(policy.observe_only),
                summary_json={"retry_of": source.id},
                budget_json={
                    "exact_row_threshold": policy.exact_row_threshold,
                    "sample_row_limit": policy.sample_row_limit,
                    "table_timeout_sec": policy.table_timeout_sec,
                    "max_tables_per_run": policy.max_tables_per_run,
                    "max_run_seconds": policy.max_run_seconds,
                },
                attempt=0,
                max_attempts=2,
            )
            session.add(run)
            session.add(SemanticGovernanceEventModel(
                workspace_id=workspace_id,
                datasource_id=source.datasource_id,
                actor_id=actor_id,
                action="governance_run_retried",
                payload_json={"run_id": run.id, "retry_of": source.id},
            ))
            await session.flush()
            return self._run_payload(run)

    async def is_run_cancel_requested(self, run_id: int) -> bool:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            status = (await session.execute(select(SemanticGovernanceRunModel.status).where(
                SemanticGovernanceRunModel.id == run_id,
            ))).scalar_one_or_none()
            return status == "cancel_requested"

    async def _raise_if_cancel_requested(self, run_id: int) -> None:
        if await self.is_run_cancel_requested(run_id):
            raise GovernanceRunCancelled()

    async def execute_run(self, run_id: int) -> None:
        started_monotonic = time.monotonic()
        context: Optional[dict[str, Any]] = None
        try:
            await self._raise_if_cancel_requested(run_id)
            await self._set_run_state(run_id, status="running", stage="collecting_schema", progress=5, started_at=datetime.now())
            context = await self._load_run_context(run_id)
            await self._raise_if_cancel_requested(run_id)
            schema_facts = await self._collect_schema_evidence(context)
            await self._raise_if_cancel_requested(run_id)
            await self._set_run_state(run_id, stage="checking_profile_connection", progress=18)
            connection_result = await self._check_profile_connection(context)
            if connection_result["connection_check"] == "passed":
                await self._set_run_state(run_id, stage="profiling", progress=20)
                profile_result = await self._collect_profiles_guarded(context, started_monotonic)
            else:
                get_readonly_pool().dispose_user(self._profile_executor_key(context["run"]))
                profile_result = {
                    "profiled_tables": 0,
                    "profiled_columns": 0,
                    "failed_tables": 0,
                    "profile_skipped_reason": connection_result.get("connection_error") or "profile_connection_unavailable",
                }
            await self._raise_if_cancel_requested(run_id)
            await self._set_run_state(run_id, stage="collecting_runtime", progress=55)
            runtime_facts = await self._collect_runtime_evidence(context)
            await self._raise_if_cancel_requested(run_id)
            await self._set_run_state(run_id, stage="generating_candidates", progress=75)
            candidate_result = await self._generate_candidates(context)
            await self._raise_if_cancel_requested(run_id)
            elapsed = round(time.monotonic() - started_monotonic, 3)
            status = "partial" if profile_result["failed_tables"] or connection_result["connection_check"] != "passed" else "completed"
            await self._set_run_state(
                run_id,
                status=status,
                stage="completed",
                progress=100,
                completed_at=datetime.now(),
                run_token=None,
                lease_expires_at=None,
                summary_json={
                    "business_suggestion_status": "decoupled",
                    **connection_result,
                    "profile_skipped_reason": profile_result.get("profile_skipped_reason"),
                    "profile_budget": {
                        "max_tables_per_run": context["policy"].max_tables_per_run,
                        "sample_row_limit": context["policy"].sample_row_limit,
                        "table_timeout_sec": context["policy"].table_timeout_sec,
                        "max_run_seconds": context["policy"].max_run_seconds,
                    },
                    "schema_facts": schema_facts,
                    "profiled_tables": profile_result["profiled_tables"],
                    "profiled_columns": profile_result["profiled_columns"],
                    "failed_tables": profile_result["failed_tables"],
                    "runtime_facts": runtime_facts,
                    **candidate_result,
                    "elapsed_seconds": elapsed,
                },
            )
        except GovernanceRunCancelled:
            await self._set_run_state(
                run_id,
                status="cancelled",
                stage="cancelled",
                progress=100,
                completed_at=datetime.now(),
                run_token=None,
                lease_expires_at=None,
                error_message="治理候选生成已取消",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("semantic governance run %s failed", run_id)
            await self._set_run_state(
                run_id,
                status="failed",
                stage="failed",
                completed_at=datetime.now(),
                run_token=None,
                lease_expires_at=None,
                error_message=str(exc)[:4000],
            )
        finally:
            if context:
                get_readonly_pool().dispose_user(self._profile_executor_key(context["run"]))

    async def _set_run_state(self, run_id: int, **patch: Any) -> None:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            run = (await session.execute(select(SemanticGovernanceRunModel).where(SemanticGovernanceRunModel.id == run_id))).scalar_one_or_none()
            if not run:
                return
            for key, value in patch.items():
                setattr(run, key, value)

    async def _load_run_context(self, run_id: int) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            run = (await session.execute(select(SemanticGovernanceRunModel).where(SemanticGovernanceRunModel.id == run_id))).scalar_one()
            policy = await self._get_or_create_policy(session, run.workspace_id, run.datasource_id)
            datasource = (await session.execute(select(SemanticDatasourceModel).where(SemanticDatasourceModel.id == run.datasource_id))).scalar_one()
            tables = list((await session.execute(select(SemanticTableModel).where(
                SemanticTableModel.workspace_id == run.workspace_id,
                SemanticTableModel.datasource_id == run.datasource_id,
            ))).scalars())
            columns = list((await session.execute(select(SemanticColumnModel).where(
                SemanticColumnModel.workspace_id == run.workspace_id,
                SemanticColumnModel.datasource_id == run.datasource_id,
            ))).scalars())
            return {"run": run, "policy": policy, "datasource": datasource, "tables": tables, "columns": columns}

    async def _upsert_fact(self, session, context: dict[str, Any], payload: dict[str, Any]) -> SemanticEvidenceFactModel:
        run = context["run"]
        fingerprint_payload = {
            "subject_type": payload["subject_type"],
            "subject_id": payload.get("subject_id"),
            "claim_type": payload["claim_type"],
            "claim_key": payload["claim_key"],
            "source_type": payload["source_type"],
            "source_ref": payload.get("source_ref"),
            "value": payload.get("value_json") or {},
            "schema_fingerprint": run.schema_fingerprint,
        }
        fingerprint = stable_fingerprint(fingerprint_payload)
        model = (await session.execute(select(SemanticEvidenceFactModel).where(
            SemanticEvidenceFactModel.workspace_id == run.workspace_id,
            SemanticEvidenceFactModel.datasource_id == run.datasource_id,
            SemanticEvidenceFactModel.fact_fingerprint == fingerprint,
        ))).scalar_one_or_none()
        values = {
            "run_id": run.id,
            "value_json": payload.get("value_json") or {},
            "direction": payload.get("direction") or "support",
            "reliability": float(payload.get("reliability", SOURCE_RELIABILITY.get(payload["source_type"], 0.5))),
            "strength": float(payload.get("strength", 1.0)),
            "schema_fingerprint": run.schema_fingerprint,
            "observed_at": datetime.now(),
            "expires_at": payload.get("expires_at"),
        }
        if model:
            for key, value in values.items():
                setattr(model, key, value)
            return model
        model = SemanticEvidenceFactModel(
            workspace_id=run.workspace_id,
            datasource_id=run.datasource_id,
            subject_type=payload["subject_type"],
            subject_id=payload.get("subject_id"),
            claim_type=payload["claim_type"],
            claim_key=payload["claim_key"],
            source_type=payload["source_type"],
            source_ref=payload.get("source_ref"),
            fact_fingerprint=fingerprint,
            **values,
        )
        session.add(model)
        await session.flush()
        return model

    async def _collect_schema_evidence(self, context: dict[str, Any]) -> int:
        db_manager = get_async_db_manager()
        count = 0
        async with db_manager.session_scope() as session:
            for table in context["tables"]:
                await self._upsert_fact(session, context, {
                    "subject_type": "table", "subject_id": table.id,
                    "claim_type": "schema_identity", "claim_key": table.physical_name,
                    "value_json": {"physical_name": table.physical_name, "comment_present": bool(table.physical_comment)},
                    "source_type": "deterministic_validation", "source_ref": f"table:{table.id}",
                })
                count += 1
            for column in context["columns"]:
                await self._upsert_fact(session, context, {
                    "subject_type": "column", "subject_id": column.id,
                    "claim_type": "schema_role", "claim_key": f"{column.physical_table}.{column.physical_name}",
                    "value_json": {
                        "data_type": column.data_type,
                        "primary_key": bool(column.is_primary_key),
                        "indexed": bool(column.is_indexed),
                    },
                    "source_type": "deterministic_validation", "source_ref": f"column:{column.id}",
                })
                if re.search(r"(^|_)(email|mail|phone|mobile|tel|id_card|identity|password|passwd)(_|$)", column.physical_name.lower()):
                    await self._upsert_fact(session, context, {
                        "subject_type": "column", "subject_id": column.id,
                        "claim_type": "sensitive", "claim_key": "sensitive:true",
                        "value_json": {"matched_name": column.physical_name},
                        "source_type": "naming_rule", "source_ref": f"sensitive-name:{column.id}",
                    })
                    count += 1
                count += 1
        return count

    def _profile_executor_key(self, run: SemanticGovernanceRunModel) -> str:
        return f"semantic-governance-{run.workspace_id}-{run.id}-profile"

    async def _profile_connection_url(self, run: SemanticGovernanceRunModel) -> Optional[str]:
        config = await get_workspace_db_config_async(run.workspace_id)
        if not config and run.triggered_by:
            config = await get_user_db_config_async(run.triggered_by)
        if not config:
            return None
        return config.get_readonly_connection_url() if config.has_readonly_config() else config.get_connection_url()

    def _check_profile_connection_sync(
        self,
        executor: ReadOnlyExecutor,
        timeout_sec: int,
    ) -> dict[str, Any]:
        result = executor.execute_query("SELECT 1 AS ok", timeout_sec=timeout_sec, max_rows=2)
        if result.error:
            return {"connection_check": "failed", "connection_error": result.error}
        return {"connection_check": "passed", "connection_error": None}

    async def _check_profile_connection(self, context: dict[str, Any]) -> dict[str, Any]:
        run = context["run"]
        policy = context["policy"]
        connection_url = await self._profile_connection_url(run)
        if not connection_url:
            return {"connection_check": "skipped", "connection_error": "未配置数据库连接"}
        try:
            executor = ReadOnlyExecutor(
                self._profile_executor_key(run),
                connection_url,
                connect_timeout_sec=max(1, int(policy.table_timeout_sec)),
            )
            return await asyncio.to_thread(
                self._check_profile_connection_sync,
                executor,
                max(1, int(policy.table_timeout_sec)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("semantic governance profile connection check failed for run %s: %s", run.id, exc)
            return {"connection_check": "failed", "connection_error": str(exc)[:1000]}

    def _profile_table_sync(
        self,
        executor: ReadOnlyExecutor,
        table: SemanticTableModel,
        columns: list[SemanticColumnModel],
        policy: SemanticGovernancePolicyModel,
    ) -> dict[str, Any]:
        estimate_sql = (
            "SELECT COALESCE(TABLE_ROWS, 0) AS estimated_rows FROM information_schema.tables "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = {_quote_literal(table.physical_name)}"
        )
        estimate_result = executor.execute_query(estimate_sql, timeout_sec=policy.table_timeout_sec, max_rows=2)
        estimated_rows = int(estimate_result.rows[0][0]) if estimate_result.rows else 0
        # MySQL/InnoDB may report TABLE_ROWS as 0 or NULL before statistics are
        # warmed. Treat unknown row counts as bounded sampling so profiling never
        # accidentally full-scans a large production table.
        sample_method = "exact" if 0 < estimated_rows <= policy.exact_row_threshold else "bounded"
        profiles: list[dict[str, Any]] = []
        profile_group_size = 5
        for offset in range(0, len(columns), profile_group_size):
            group = columns[offset: offset + profile_group_size]
            select_columns = ", ".join(_quote_identifier(item.physical_name) for item in group)
            if sample_method == "bounded":
                source = f"(SELECT {select_columns} FROM {_quote_identifier(table.physical_name)} LIMIT {policy.sample_row_limit}) AS bounded_profile"
            else:
                source = _quote_identifier(table.physical_name)
            expressions = ["COUNT(*) AS sampled_rows"]
            for index, column in enumerate(group):
                name = _quote_identifier(column.physical_name)
                expressions.append(f"COUNT({name}) AS c{index}_non_null")
                if _profile_is_string(column.data_type):
                    expressions.append(f"0 AS c{index}_distinct")
                else:
                    expressions.append(f"COUNT(DISTINCT {name}) AS c{index}_distinct")
                expressions.append(f"AVG(CHAR_LENGTH(CAST({name} AS CHAR))) AS c{index}_avg_length")
                if _profile_is_numeric(column.data_type) or _profile_is_temporal(column.data_type):
                    expressions.extend([f"MIN({name}) AS c{index}_min", f"MAX({name}) AS c{index}_max"])
                # Keep the baseline profiler intentionally light. Some MySQL
                # deployments drop PyMySQL connections on large multi-column
                # REGEXP aggregates, so format-sensitive evidence should be
                # collected by a narrower follow-up scanner instead of blocking
                # coverage for ordinary null/distinct/range evidence.
            query = f"SELECT {', '.join(expressions)} FROM {source}"
            result = executor.execute_query(query, timeout_sec=policy.table_timeout_sec, max_rows=2)
            if result.error or not result.rows:
                return {"status": "failed", "error": result.error or "画像查询无结果", "estimated_rows": estimated_rows, "profiles": []}
            row = dict(zip(result.columns, result.rows[0]))
            sampled_rows = int(row.get("sampled_rows") or 0)
            for index, column in enumerate(group):
                non_null = int(row.get(f"c{index}_non_null") or 0)
                distinct = int(row.get(f"c{index}_distinct") or 0)
                range_json = {}
                if f"c{index}_min" in row:
                    range_json = {"min": str(row.get(f"c{index}_min")) if row.get(f"c{index}_min") is not None else None, "max": str(row.get(f"c{index}_max")) if row.get(f"c{index}_max") is not None else None}
                patterns = {}
                if f"c{index}_email" in row:
                    denominator = max(non_null, 1)
                    patterns = {
                        "email_ratio": round(float(row.get(f"c{index}_email") or 0) / denominator, 6),
                        "phone_ratio": round(float(row.get(f"c{index}_phone") or 0) / denominator, 6),
                    }
                profiles.append({
                    "column": column,
                    "sample_method": sample_method,
                    "estimated_rows": estimated_rows,
                    "sampled_rows": sampled_rows,
                    "non_null_count": non_null,
                    "null_ratio": round((sampled_rows - non_null) / max(sampled_rows, 1), 6),
                    "distinct_count": distinct,
                    "distinct_ratio": round(distinct / max(non_null, 1), 6),
                    "range_json": range_json,
                    "avg_length": float(row.get(f"c{index}_avg_length")) if row.get(f"c{index}_avg_length") is not None else None,
                    "pattern_ratios_json": patterns,
                })
        return {"status": "complete", "error": None, "estimated_rows": estimated_rows, "profiles": profiles}

    async def _collect_profiles_guarded(self, context: dict[str, Any], started_monotonic: float) -> dict[str, Any]:
        run = context["run"]
        policy = context["policy"]
        profile_connection_url = await self._profile_connection_url(run)
        if not profile_connection_url:
            return {
                "profiled_tables": 0,
                "profiled_columns": 0,
                "failed_tables": len(context["tables"]),
                "profile_skipped_reason": "missing_database_connection",
            }

        columns_by_table: dict[int, list[SemanticColumnModel]] = {}
        for column in context["columns"]:
            columns_by_table.setdefault(column.table_id, []).append(column)

        tables = [
            item
            for item in context["tables"]
            if item.sync_state == "current" and item.status != "disabled"
        ][: policy.max_tables_per_run]
        profiled_tables = profiled_columns = failed_tables = 0
        executor = ReadOnlyExecutor(
            self._profile_executor_key(run),
            profile_connection_url,
            connect_timeout_sec=max(1, int(policy.table_timeout_sec)),
        )

        for table in tables:
            await self._raise_if_cancel_requested(run.id)
            if time.monotonic() - started_monotonic > policy.max_run_seconds:
                failed_tables += len(tables) - profiled_tables
                break

            result = await asyncio.to_thread(
                self._profile_table_sync,
                executor,
                table,
                columns_by_table.get(table.id, []),
                policy,
            )
            db_manager = get_async_db_manager()
            async with db_manager.session_scope() as session:
                if result["status"] != "complete":
                    failed_tables += 1
                    for column in columns_by_table.get(table.id, []):
                        session.add(SemanticColumnProfileModel(
                            workspace_id=run.workspace_id,
                            datasource_id=run.datasource_id,
                            run_id=run.id,
                            table_id=table.id,
                            column_id=column.id,
                            schema_fingerprint=run.schema_fingerprint,
                            profile_status="failed",
                            sample_method="none",
                            sampled_rows=0,
                            non_null_count=0,
                            range_json={},
                            pattern_ratios_json={},
                            error_message=result["error"],
                            expires_at=datetime.now() + timedelta(days=PROFILE_TTL_DAYS),
                        ))
                    continue

                profiled_tables += 1
                for profile in result["profiles"]:
                    column = profile.pop("column")
                    model = SemanticColumnProfileModel(
                        workspace_id=run.workspace_id,
                        datasource_id=run.datasource_id,
                        run_id=run.id,
                        table_id=table.id,
                        column_id=column.id,
                        schema_fingerprint=run.schema_fingerprint,
                        profile_status="complete",
                        expires_at=datetime.now() + timedelta(days=PROFILE_TTL_DAYS),
                        **profile,
                    )
                    session.add(model)
                    await session.flush()
                    profiled_columns += 1
                    await self._upsert_fact(session, context, {
                        "subject_type": "column",
                        "subject_id": column.id,
                        "claim_type": "aggregate_profile",
                        "claim_key": f"profile:{column.id}",
                        "value_json": {
                            "sample_method": model.sample_method,
                            "estimated_rows": model.estimated_rows,
                            "sampled_rows": model.sampled_rows,
                            "null_ratio": model.null_ratio,
                            "distinct_ratio": model.distinct_ratio,
                            "range": model.range_json if (_profile_is_numeric(column.data_type) or _profile_is_temporal(column.data_type)) else {},
                            "avg_length": model.avg_length,
                            "pattern_ratios": model.pattern_ratios_json,
                        },
                        "source_type": "aggregate_profile",
                        "source_ref": f"profile:{model.id}",
                        "expires_at": model.expires_at,
                    })
                    pattern_ratios = model.pattern_ratios_json or {}
                    strongest_sensitive_ratio = max(
                        float(pattern_ratios.get("email_ratio") or 0),
                        float(pattern_ratios.get("phone_ratio") or 0),
                    )
                    if strongest_sensitive_ratio >= 0.98:
                        await self._upsert_fact(session, context, {
                            "subject_type": "column",
                            "subject_id": column.id,
                            "claim_type": "sensitive",
                            "claim_key": "sensitive:true",
                            "value_json": {
                                "format_match_ratio": strongest_sensitive_ratio,
                                "profile_id": model.id,
                            },
                            "source_type": "deterministic_validation",
                            "source_ref": f"sensitive-format-profile:{model.id}",
                            "expires_at": model.expires_at,
                        })

        return {
            "profiled_tables": profiled_tables,
            "profiled_columns": profiled_columns,
            "failed_tables": failed_tables,
            "profile_skipped_reason": None,
        }

    async def _collect_profiles(self, context: dict[str, Any], started_monotonic: float) -> dict[str, Any]:
        return await self._collect_profiles_guarded(context, started_monotonic)

    async def _collect_runtime_evidence(self, context: dict[str, Any]) -> int:
        run = context["run"]
        cutoff = datetime.now() - timedelta(days=RUNTIME_EVIDENCE_TTL_DAYS)
        db_manager = get_async_db_manager()
        count = 0
        async with db_manager.session_scope() as session:
            query_runs = list((await session.execute(select(SemanticQueryRunModel).where(
                SemanticQueryRunModel.workspace_id == run.workspace_id,
                SemanticQueryRunModel.created_at >= cutoff,
            ).order_by(SemanticQueryRunModel.created_at.desc()).limit(500))).scalars())
            table_usage: dict[str, dict[str, int]] = {}
            equivalent_shadow_runs: dict[str, list[int]] = {}
            table_cooccurrence: dict[tuple[str, str], int] = {}
            for query_run in query_runs:
                referenced_tables = sorted({str(item) for item in (query_run.referenced_tables or [])})
                for left_index, left_table in enumerate(referenced_tables):
                    for right_table in referenced_tables[left_index + 1:]:
                        pair = (left_table, right_table)
                        table_cooccurrence[pair] = table_cooccurrence.get(pair, 0) + 1
                for table_name in referenced_tables:
                    bucket = table_usage.setdefault(str(table_name), {"success": 0, "failure": 0})
                    bucket["success" if query_run.status == "success" else "failure"] += 1
                    comparison = query_run.comparison_json or {}
                    if comparison.get("verdict") == "equivalent":
                        equivalent_shadow_runs.setdefault(str(table_name), []).append(query_run.id)
            table_by_name = {item.physical_name: item for item in context["tables"]}
            for table_name, usage in table_usage.items():
                table = table_by_name.get(table_name)
                if not table:
                    continue
                total = usage["success"] + usage["failure"]
                await self._upsert_fact(session, context, {
                    "subject_type": "table", "subject_id": table.id,
                    "claim_type": "runtime_usage", "claim_key": f"runtime:{table.id}",
                    "value_json": {**usage, "total": total}, "source_type": "runtime_pattern",
                    "source_ref": f"query-runs:{cutoff.date().isoformat()}",
                    "strength": min(1.0, total / 20), "expires_at": datetime.now() + timedelta(days=RUNTIME_EVIDENCE_TTL_DAYS),
                })
                count += 1
                equivalent_ids = equivalent_shadow_runs.get(table_name, [])
                if equivalent_ids:
                    await self._upsert_fact(session, context, {
                        "subject_type": "table",
                        "subject_id": table.id,
                        "claim_type": "shadow_equivalence",
                        "claim_key": f"shadow:{table.id}",
                        "value_json": {
                            "equivalent_run_count": len(equivalent_ids),
                            "latest_run_ids": equivalent_ids[:20],
                        },
                        "source_type": "shadow_equivalent",
                        "source_ref": f"shadow-runs:{cutoff.date().isoformat()}",
                        "strength": min(1.0, len(equivalent_ids) / 10),
                        "expires_at": datetime.now() + timedelta(days=RUNTIME_EVIDENCE_TTL_DAYS),
                    })
                    count += 1
            for (left_table, right_table), frequency in table_cooccurrence.items():
                await self._upsert_fact(session, context, {
                    "subject_type": "datasource", "subject_id": run.datasource_id,
                    "claim_type": "runtime_join_cooccurrence",
                    "claim_key": f"cooccurrence:{left_table}:{right_table}",
                    "value_json": {"left_table": left_table, "right_table": right_table, "frequency": frequency},
                    "source_type": "runtime_pattern", "source_ref": f"query-runs:{cutoff.date().isoformat()}",
                    "strength": min(1.0, frequency / 20),
                    "expires_at": datetime.now() + timedelta(days=RUNTIME_EVIDENCE_TTL_DAYS),
                })
                count += 1

            eval_runs = list((await session.execute(select(SemanticEvaluationRunModel).where(
                SemanticEvaluationRunModel.workspace_id == run.workspace_id,
                SemanticEvaluationRunModel.created_at >= cutoff,
            ).order_by(SemanticEvaluationRunModel.created_at.desc()).limit(100))).scalars())
            for evaluation in eval_runs:
                comparison = compare_execution_results(evaluation.semantic_result or {}, evaluation.legacy_result or {})
                if comparison["verdict"] != "equivalent":
                    continue
                await self._upsert_fact(session, context, {
                    "subject_type": "datasource", "subject_id": run.datasource_id,
                    "claim_type": "evaluation_equivalence", "claim_key": evaluation.case_id,
                    "value_json": comparison, "source_type": "evaluation_equivalent",
                    "source_ref": f"evaluation:{evaluation.id}",
                    "expires_at": datetime.now() + timedelta(days=RUNTIME_EVIDENCE_TTL_DAYS),
                })
                count += 1

            examples = list((await session.execute(select(SqlExampleModel).where(
                SqlExampleModel.workspace_id == run.workspace_id,
                SqlExampleModel.is_active == True,  # noqa: E712
            ).limit(200))).scalars())
            column_by_physical = {
                (item.physical_table.lower(), item.physical_name.lower()): item
                for item in context["columns"]
            }
            for example in examples:
                assets = self._sql_assets(example.sql)
                for table_name in assets["tables"]:
                    table = table_by_name.get(table_name)
                    if not table:
                        continue
                    await self._upsert_fact(session, context, {
                        "subject_type": "table", "subject_id": table.id,
                        "claim_type": "golden_sql_reference", "claim_key": f"golden:{example.id}:{table.id}",
                        "value_json": {"example_id": example.id, "question": example.question[:160]},
                        "source_type": "golden_sql", "source_ref": f"sql-example:{example.id}",
                    })
                    count += 1
                for table_name, column_name in assets["columns"]:
                    column = column_by_physical.get((table_name.lower(), column_name.lower()))
                    if not column:
                        continue
                    await self._upsert_fact(session, context, {
                        "subject_type": "column", "subject_id": column.id,
                        "claim_type": "golden_sql_reference", "claim_key": f"golden:{example.id}:{column.id}",
                        "value_json": {"example_id": example.id, "question": example.question[:160]},
                        "source_type": "golden_sql", "source_ref": f"sql-example:{example.id}",
                    })
                    count += 1
                for left_table, left_column, right_table, right_column in assets["joins"]:
                    await self._upsert_fact(session, context, {
                        "subject_type": "datasource", "subject_id": run.datasource_id,
                        "claim_type": "golden_sql_join",
                        "claim_key": f"golden-join:{left_table}.{left_column}:{right_table}.{right_column}",
                        "value_json": {
                            "example_id": example.id,
                            "left": {"table": left_table, "column": left_column},
                            "right": {"table": right_table, "column": right_column},
                        },
                        "source_type": "golden_sql", "source_ref": f"sql-example:{example.id}",
                    })
                    count += 1
        return count

    def _sql_tables(self, sql: str) -> set[str]:
        return self._sql_assets(sql)["tables"]

    def _sql_assets(self, sql: str) -> dict[str, Any]:
        try:
            import sqlglot
            from sqlglot import exp

            parsed = sqlglot.parse_one(sql, read="mysql")
            tables = {str(table.name) for table in parsed.find_all(exp.Table) if table.name}
            aliases = {str(table.alias_or_name): str(table.name) for table in parsed.find_all(exp.Table) if table.name}
            columns = set()
            for column in parsed.find_all(exp.Column):
                qualifier = aliases.get(str(column.table), str(column.table))
                if qualifier and column.name:
                    columns.add((qualifier, str(column.name)))
            joins = set()
            for predicate in parsed.find_all(exp.EQ):
                left = predicate.left
                right = predicate.right
                if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                    continue
                left_table = aliases.get(str(left.table), str(left.table))
                right_table = aliases.get(str(right.table), str(right.table))
                if left_table and right_table and left_table != right_table:
                    joins.add((left_table, str(left.name), right_table, str(right.name)))
            return {"tables": tables, "columns": columns, "joins": joins}
        except Exception:  # noqa: BLE001
            tables = set(re.findall(r"(?:from|join)\s+[`\"]?([\w\u4e00-\u9fff]+)", sql or "", re.IGNORECASE))
            return {"tables": tables, "columns": set(), "joins": set()}

    async def _generate_candidates(self, context: dict[str, Any]) -> dict[str, int]:
        run = context["run"]
        policy = context["policy"]
        db_manager = get_async_db_manager()
        generated = auto_applied_count = blocked = 0
        async with db_manager.session_scope() as session:
            facts = list((await session.execute(select(SemanticEvidenceFactModel).where(
                SemanticEvidenceFactModel.workspace_id == run.workspace_id,
                SemanticEvidenceFactModel.datasource_id == run.datasource_id,
                (SemanticEvidenceFactModel.expires_at.is_(None)) | (SemanticEvidenceFactModel.expires_at > datetime.now()),
            ))).scalars())
            facts_by_subject: dict[tuple[str, Optional[int]], list[SemanticEvidenceFactModel]] = {}
            for fact in facts:
                facts_by_subject.setdefault((fact.subject_type, fact.subject_id), []).append(fact)

            suggestions = list((await session.execute(select(SemanticBusinessSuggestionModel).where(
                SemanticBusinessSuggestionModel.workspace_id == run.workspace_id,
                SemanticBusinessSuggestionModel.datasource_id == run.datasource_id,
                SemanticBusinessSuggestionModel.status == "__disabled__",
            ))).scalars())
            for suggestion in suggestions:
                target_type = "tables" if suggestion.object_type == "table" else "columns"
                subject_facts = facts_by_subject.get((suggestion.object_type, suggestion.object_id), [])
                evidence = [self._fact_for_score(item) for item in subject_facts]
                evidence.append({
                    "source_type": "llm_proposal" if "llm" in suggestion.source else "naming_rule",
                    "direction": "support", "strength": max(0.1, min(1.0, suggestion.confidence)),
                    "source_ref": f"business-suggestion:{suggestion.id}",
                })
                result = score_evidence(evidence)
                has_strong_business_evidence = bool(set(result["source_types"]) & STRONG_BUSINESS_EVIDENCE_SOURCES)
                status = (
                    "blocked" if result["conflicting"]
                    else "needs_review" if has_strong_business_evidence and result["score"] >= policy.review_threshold
                    else "proposed" if has_strong_business_evidence
                    else "superseded"
                )
                await self._upsert_candidate(session, context, {
                    "target_type": target_type, "target_id": suggestion.object_id,
                    "candidate_type": "business_semantics",
                    "title": f"业务语义建议：{suggestion.suggested_business_name}",
                    "before_json": {},
                    "proposed_patch_json": {
                        "business_name": suggestion.suggested_business_name,
                        "description": suggestion.suggested_description or "",
                        "synonyms": suggestion.suggested_synonyms or [],
                    },
                    "risk_level": "medium", "status": status,
                    "score_result": result, "deterministic_check_passed": False,
                })
                generated += 1

            profiles = list((await session.execute(select(SemanticColumnProfileModel).where(
                SemanticColumnProfileModel.run_id == run.id,
                SemanticColumnProfileModel.profile_status == "complete",
            ))).scalars())
            columns = {item.id: item for item in context["columns"]}
            for profile in profiles:
                column = columns.get(profile.column_id)
                if not column or column.is_sensitive:
                    continue
                patterns = profile.pattern_ratios_json or {}
                if max(float(patterns.get("email_ratio") or 0), float(patterns.get("phone_ratio") or 0)) < 0.8:
                    continue
                evidence = [self._fact_for_score(item) for item in facts_by_subject.get(("column", column.id), []) if item.claim_type in {"sensitive", "aggregate_profile", "governance_feedback"}]
                result = score_evidence(evidence)
                deterministic = any(item.get("source_type") == "deterministic_validation" for item in evidence)
                eligible = auto_eligible(
                    result["score"], result["source_types"], has_conflict=bool(result["conflicting"]),
                    deterministic_check_passed=deterministic, threshold=policy.auto_apply_threshold,
                    minimum_sources=policy.min_auto_evidence_sources,
                )
                candidate = await self._upsert_candidate(session, context, {
                    "target_type": "columns", "target_id": column.id, "candidate_type": "mark_sensitive",
                    "title": f"标记敏感字段：{column.business_name}",
                    "before_json": {"is_sensitive": bool(column.is_sensitive)},
                    "proposed_patch_json": {"is_sensitive": True}, "risk_level": "high",
                    "status": "blocked" if result["conflicting"] else "needs_review" if result["score"] >= policy.review_threshold else "proposed",
                    "score_result": result, "deterministic_check_passed": deterministic,
                    "auto_eligible": eligible,
                })
                generated += 1
                if await self._maybe_auto_apply(session, context, candidate):
                    auto_applied_count += 1

            metrics = list((await session.execute(select(SemanticMetricModel).where(
                SemanticMetricModel.workspace_id == run.workspace_id,
                SemanticMetricModel.datasource_id == run.datasource_id,
            ))).scalars())
            relationships = list((await session.execute(select(SemanticRelationshipModel).where(
                SemanticRelationshipModel.workspace_id == run.workspace_id,
                SemanticRelationshipModel.datasource_id == run.datasource_id,
            ))).scalars())
            column_current = {item.id: item.sync_state == "current" for item in context["columns"]}
            for target_type, items in (("metrics", metrics), ("relationships", relationships)):
                for item in items:
                    if item.management_mode != "system" or item.status != "suggested":
                        continue
                    dependency_ids = [getattr(item, key, None) for key in ("column_id", "time_column_id", "left_column_id", "right_column_id")]
                    dependency_ids = [value for value in dependency_ids if value is not None]
                    dependencies_valid = bool(dependency_ids) and all(column_current.get(value, False) for value in dependency_ids)
                    subject_type = "metric" if target_type == "metrics" else "relationship"
                    evidence = [
                        self._fact_for_score(fact)
                        for fact in facts_by_subject.get((subject_type, item.id), [])
                        if fact.claim_type == "governance_feedback"
                    ] + [
                        {"source_type": "naming_rule", "direction": "support", "strength": max(0.1, min(1.0, float(item.confidence or 0.5))), "source_ref": f"rule-scan:{target_type}:{item.id}"},
                    ]
                    if dependencies_valid:
                        evidence.append({"source_type": "deterministic_validation", "direction": "support", "strength": 1.0, "source_ref": "dependency-validator"})
                    result = score_evidence(evidence)
                    has_strong_semantic_evidence = bool(set(result["source_types"]) & STRONG_BUSINESS_EVIDENCE_SOURCES)
                    candidate_type = "metric" if target_type == "metrics" else "relationship"
                    title_value = getattr(item, "business_name", None) or getattr(item, "description", None) or str(item.id)
                    await self._upsert_candidate(session, context, {
                        "target_type": target_type,
                        "target_id": item.id,
                        "candidate_type": candidate_type,
                        "title": f"{('指标' if target_type == 'metrics' else '关系')}建议：{title_value}",
                        "before_json": {"status": item.status, "is_queryable": bool(item.is_queryable)},
                        "proposed_patch_json": {"status": "confirmed", "is_queryable": True, "sync_state": "current"},
                        "risk_level": "high",
                        "status": (
                            "blocked" if result["conflicting"]
                            else "needs_review" if has_strong_semantic_evidence and result["score"] >= policy.review_threshold
                            else "proposed" if has_strong_semantic_evidence
                            else "superseded"
                        ),
                        "score_result": result,
                        "deterministic_check_passed": dependencies_valid,
                        "auto_eligible": False,
                    })
                    generated += 1
            for target_type, items in (("metrics", metrics), ("relationships", relationships)):
                for item in items:
                    dependency_ids = [getattr(item, key, None) for key in ("column_id", "time_column_id", "left_column_id", "right_column_id")]
                    dependency_ids = [value for value in dependency_ids if value is not None]
                    invalid = bool(dependency_ids and any(not column_current.get(value, False) for value in dependency_ids))
                    if not invalid or item.management_mode != "system" or not item.is_queryable:
                        continue
                    subject_type = "metric" if target_type == "metrics" else "relationship"
                    evidence = [
                        self._fact_for_score(fact)
                        for fact in facts_by_subject.get((subject_type, item.id), [])
                        if fact.claim_type == "governance_feedback"
                    ] + [
                        {"source_type": "deterministic_validation", "direction": "support", "strength": 1.0, "source_ref": "dependency-validator"},
                        {"source_type": "explicit_fk", "direction": "support", "strength": 1.0, "source_ref": "schema-state"},
                    ]
                    result = score_evidence(evidence)
                    eligible = auto_eligible(
                        result["score"], result["source_types"], has_conflict=bool(result["conflicting"]),
                        deterministic_check_passed=True, threshold=policy.auto_apply_threshold,
                        minimum_sources=policy.min_auto_evidence_sources,
                    )
                    candidate = await self._upsert_candidate(session, context, {
                        "target_type": target_type, "target_id": item.id, "candidate_type": "disable_invalid_asset",
                        "title": f"停用失效{('指标' if target_type == 'metrics' else '关系')}：{getattr(item, 'business_name', item.id)}",
                        "before_json": {"status": item.status, "is_queryable": bool(item.is_queryable)},
                        "proposed_patch_json": {"status": "disabled", "is_queryable": False},
                        "risk_level": "high", "status": "blocked" if result["conflicting"] else "needs_review", "score_result": result,
                        "deterministic_check_passed": True, "auto_eligible": eligible,
                    })
                    generated += 1
                    if await self._maybe_auto_apply(session, context, candidate):
                        auto_applied_count += 1

            blocked = sum(1 for item in facts if item.direction == "conflict")
        return {"candidates_generated": generated, "auto_applied": auto_applied_count, "conflicts": blocked}

    def _fact_for_score(self, fact: SemanticEvidenceFactModel) -> dict[str, Any]:
        return {
            "fact_id": fact.id,
            "source_type": fact.source_type,
            "source_ref": fact.source_ref,
            "direction": fact.direction,
            "reliability": fact.reliability,
            "strength": fact.strength,
            "claim_type": fact.claim_type,
            "value": fact.value_json or {},
        }

    async def _upsert_candidate(self, session, context: dict[str, Any], payload: dict[str, Any]) -> SemanticGovernanceCandidateModel:
        run = context["run"]
        score_result = payload["score_result"]
        key_payload = {
            "target_type": payload["target_type"], "target_id": payload.get("target_id"),
            "candidate_type": payload["candidate_type"], "patch": payload.get("proposed_patch_json") or {},
            "schema_fingerprint": run.schema_fingerprint,
        }
        candidate_key = stable_fingerprint(key_payload)
        candidate = (await session.execute(select(SemanticGovernanceCandidateModel).where(
            SemanticGovernanceCandidateModel.workspace_id == run.workspace_id,
            SemanticGovernanceCandidateModel.datasource_id == run.datasource_id,
            SemanticGovernanceCandidateModel.candidate_key == candidate_key,
        ))).scalar_one_or_none()
        if not candidate:
            candidate = (await session.execute(select(SemanticGovernanceCandidateModel).where(
                SemanticGovernanceCandidateModel.workspace_id == run.workspace_id,
                SemanticGovernanceCandidateModel.datasource_id == run.datasource_id,
                SemanticGovernanceCandidateModel.target_type == payload["target_type"],
                SemanticGovernanceCandidateModel.target_id == payload.get("target_id"),
                SemanticGovernanceCandidateModel.candidate_type == payload["candidate_type"],
                SemanticGovernanceCandidateModel.status.in_(["proposed", "needs_review", "blocked"]),
            ).order_by(SemanticGovernanceCandidateModel.updated_at.desc()))).scalars().first()
            if candidate:
                candidate.candidate_key = candidate_key
        target = await self._target(session, run.workspace_id, payload["target_type"], payload.get("target_id"))
        values = {
            "run_id": run.id, "title": payload["title"], "before_json": payload.get("before_json") or {},
            "proposed_patch_json": payload.get("proposed_patch_json") or {},
            "supporting_evidence_json": score_result["supporting"],
            "conflicting_evidence_json": score_result["conflicting"],
            "evidence_fact_ids": [item.get("fact_id") for item in score_result["supporting"] + score_result["conflicting"] if item.get("fact_id")],
            "source_types": score_result["source_types"], "score": score_result["score"],
            "score_version": score_result["score_version"], "risk_level": payload.get("risk_level") or "medium",
            "auto_eligible": bool(payload.get("auto_eligible")),
            "deterministic_check_passed": bool(payload.get("deterministic_check_passed")),
            "policy_version": context["policy"].policy_version,
            "target_updated_at": getattr(target, "updated_at", None), "updated_at": datetime.now(),
        }
        if candidate:
            if candidate.status not in TERMINAL_CANDIDATE_STATUSES:
                for key, value in values.items():
                    setattr(candidate, key, value)
                candidate.status = payload.get("status") or candidate.status
            return candidate
        candidate = SemanticGovernanceCandidateModel(
            workspace_id=run.workspace_id, datasource_id=run.datasource_id,
            target_type=payload["target_type"], target_id=payload.get("target_id"),
            candidate_type=payload["candidate_type"], candidate_key=candidate_key,
            status=payload.get("status") or "proposed", applied_patch_json={},
            **values,
        )
        session.add(candidate)
        await session.flush()
        return candidate

    async def _maybe_auto_apply(self, session, context: dict[str, Any], candidate: SemanticGovernanceCandidateModel) -> bool:
        policy = context["policy"]
        if context["run"].observe_only or policy.observe_only or not candidate.auto_eligible:
            return False
        if candidate.candidate_type not in SAFE_AUTO_CANDIDATE_TYPES or candidate.candidate_type not in (policy.auto_action_types or []):
            return False
        target = await self._target(session, candidate.workspace_id, candidate.target_type, candidate.target_id)
        if not target or getattr(target, "management_mode", "human") != "system" or getattr(target, "sync_state", "current") == "orphaned":
            return False
        await self._apply_patch(session, target, candidate.proposed_patch_json, human=False)
        now = datetime.now()
        target.updated_at = now
        candidate.status = "auto_applied"
        candidate.applied_patch_json = {**(candidate.proposed_patch_json or {}), "target_version_after": now.isoformat()}
        candidate.applied_at = now
        candidate.decided_at = now
        session.add(SemanticGovernanceEventModel(
            workspace_id=candidate.workspace_id, datasource_id=candidate.datasource_id,
            action="governance_candidate_auto_applied", object_type=candidate.target_type,
            object_id=candidate.target_id, payload_json={"candidate_id": candidate.id, "before": candidate.before_json, "after": candidate.proposed_patch_json, "score": candidate.score},
        ))
        return True

    async def _target(self, session, workspace_id: str, target_type: str, target_id: Optional[int]):
        if target_id is None:
            return None
        model = {
            "tables": SemanticTableModel,
            "columns": SemanticColumnModel,
            "metrics": SemanticMetricModel,
            "relationships": SemanticRelationshipModel,
        }.get(target_type)
        if not model:
            return None
        return (await session.execute(select(model).where(model.id == target_id, model.workspace_id == workspace_id))).scalar_one_or_none()

    async def _apply_patch(self, session, target: Any, patch: dict[str, Any], *, human: bool) -> None:
        allowed = {
            "business_name", "description", "synonyms", "is_sensitive", "status", "is_queryable",
            "formula", "aggregation", "time_column_id", "default_grain", "left_table_id", "right_table_id",
            "left_column_id", "right_column_id", "relationship_type", "sync_state",
        }
        for key, value in patch.items():
            if key in allowed and hasattr(target, key):
                setattr(target, key, value)
        if human:
            target.management_mode = "human"
            target.confirmed_at = datetime.now()

    def _candidate_payload(self, candidate: SemanticGovernanceCandidateModel, target_context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return {
            "candidate_id": candidate.id,
            "run_id": candidate.run_id,
            "target_type": candidate.target_type,
            "target_id": candidate.target_id,
            "candidate_type": candidate.candidate_type,
            "title": candidate.title,
            "before": candidate.before_json or {},
            "proposed_patch": candidate.proposed_patch_json or {},
            "applied_patch": candidate.applied_patch_json or {},
            "supporting_evidence": candidate.supporting_evidence_json or [],
            "conflicting_evidence": candidate.conflicting_evidence_json or [],
            "source_types": candidate.source_types or [],
            "score": candidate.score,
            "score_version": candidate.score_version,
            "risk_level": candidate.risk_level,
            "status": candidate.status,
            "auto_eligible": bool(candidate.auto_eligible),
            "deterministic_check_passed": bool(candidate.deterministic_check_passed),
            "policy_version": candidate.policy_version,
            "decision_reason": candidate.decision_reason,
            "decided_by": candidate.decided_by,
            "decided_at": _serialize_datetime(candidate.decided_at),
            "applied_at": _serialize_datetime(candidate.applied_at),
            "created_at": _serialize_datetime(candidate.created_at),
            "updated_at": _serialize_datetime(candidate.updated_at),
            "priority_reason": self._candidate_priority_reason(candidate),
            "evidence_summaries": self._evidence_summaries(candidate),
            "editable_fields": self._editable_fields(candidate),
            "target_context": target_context or {},
        }

    def _is_deprecated_business_candidate(self, candidate: SemanticGovernanceCandidateModel) -> bool:
        if candidate.candidate_type != "business_semantics":
            return False
        evidence = list(candidate.supporting_evidence_json or []) + list(candidate.conflicting_evidence_json or [])
        return any(str(item.get("source_ref") or "").startswith("business-suggestion:") for item in evidence)

    async def _candidate_target_context(self, session, candidate: SemanticGovernanceCandidateModel) -> dict[str, Any]:
        if candidate.target_type == "tables":
            table = (await session.execute(select(SemanticTableModel).where(
                SemanticTableModel.id == candidate.target_id,
                SemanticTableModel.workspace_id == candidate.workspace_id,
            ))).scalar_one_or_none()
            if not table:
                return {}
            return {
                "table_id": table.id,
                "table_physical_name": table.physical_name,
                "table_business_name": table.business_name,
            }
        if candidate.target_type == "columns":
            column = (await session.execute(select(SemanticColumnModel).where(
                SemanticColumnModel.id == candidate.target_id,
                SemanticColumnModel.workspace_id == candidate.workspace_id,
            ))).scalar_one_or_none()
            if not column:
                return {}
            return {
                "table_id": column.table_id,
                "table_physical_name": column.physical_table,
                "column_id": column.id,
                "column_physical_name": column.physical_name,
                "column_business_name": column.business_name,
                "data_type": column.data_type,
            }
        if candidate.target_type == "metrics":
            metric = (await session.execute(select(SemanticMetricModel).where(
                SemanticMetricModel.id == candidate.target_id,
                SemanticMetricModel.workspace_id == candidate.workspace_id,
            ))).scalar_one_or_none()
            if not metric:
                return {}
            table = (await session.execute(select(SemanticTableModel).where(SemanticTableModel.id == metric.table_id))).scalar_one_or_none()
            column = (await session.execute(select(SemanticColumnModel).where(SemanticColumnModel.id == metric.column_id))).scalar_one_or_none() if metric.column_id else None
            time_column = (await session.execute(select(SemanticColumnModel).where(SemanticColumnModel.id == metric.time_column_id))).scalar_one_or_none() if metric.time_column_id else None
            return {
                "metric_name": metric.name,
                "metric_business_name": metric.business_name,
                "formula": metric.formula,
                "table_id": metric.table_id,
                "table_physical_name": table.physical_name if table else None,
                "table_business_name": table.business_name if table else None,
                "column_id": metric.column_id,
                "column_physical_name": column.physical_name if column else None,
                "column_business_name": column.business_name if column else None,
                "time_column_id": metric.time_column_id,
                "time_column_physical_name": time_column.physical_name if time_column else None,
                "time_column_business_name": time_column.business_name if time_column else None,
            }
        if candidate.target_type == "relationships":
            rel = (await session.execute(select(SemanticRelationshipModel).where(
                SemanticRelationshipModel.id == candidate.target_id,
                SemanticRelationshipModel.workspace_id == candidate.workspace_id,
            ))).scalar_one_or_none()
            if not rel:
                return {}
            left_table = (await session.execute(select(SemanticTableModel).where(SemanticTableModel.id == rel.left_table_id))).scalar_one_or_none()
            right_table = (await session.execute(select(SemanticTableModel).where(SemanticTableModel.id == rel.right_table_id))).scalar_one_or_none()
            left_column = (await session.execute(select(SemanticColumnModel).where(SemanticColumnModel.id == rel.left_column_id))).scalar_one_or_none()
            right_column = (await session.execute(select(SemanticColumnModel).where(SemanticColumnModel.id == rel.right_column_id))).scalar_one_or_none()
            return {
                "left_table_id": rel.left_table_id,
                "left_table_physical_name": left_table.physical_name if left_table else None,
                "left_table_business_name": left_table.business_name if left_table else None,
                "left_column_id": rel.left_column_id,
                "left_column_physical_name": left_column.physical_name if left_column else None,
                "left_column_business_name": left_column.business_name if left_column else None,
                "right_table_id": rel.right_table_id,
                "right_table_physical_name": right_table.physical_name if right_table else None,
                "right_table_business_name": right_table.business_name if right_table else None,
                "right_column_id": rel.right_column_id,
                "right_column_physical_name": right_column.physical_name if right_column else None,
                "right_column_business_name": right_column.business_name if right_column else None,
                "relationship_type": rel.relationship_type,
            }
        return {}

    def _candidate_priority(self, candidate: SemanticGovernanceCandidateModel) -> tuple[int, float, float]:
        if candidate.candidate_type == "disable_invalid_asset":
            priority = 500
        elif candidate.candidate_type == "mark_sensitive":
            priority = 400
        elif candidate.risk_level in {"critical", "high"}:
            priority = 350
        elif candidate.candidate_type == "business_semantics" and set(candidate.source_types or []) & STRONG_BUSINESS_EVIDENCE_SOURCES:
            priority = 300
        elif candidate.candidate_type in {"metric", "relationship"}:
            priority = 250
        else:
            priority = 100
        if candidate.status == "blocked":
            priority += 50
        return (priority, float(candidate.score or 0), (candidate.updated_at or candidate.created_at or datetime.min).timestamp())

    def _candidate_priority_reason(self, candidate: SemanticGovernanceCandidateModel) -> str:
        if candidate.candidate_type == "disable_invalid_asset":
            return "依赖已失效，会阻塞或误导问数，需优先处理。"
        if candidate.candidate_type == "mark_sensitive":
            return "疑似敏感字段，关系到权限与数据安全。"
        if candidate.status == "blocked":
            return "存在冲突证据，需要管理员先裁决。"
        if candidate.candidate_type == "business_semantics" and set(candidate.source_types or []) & STRONG_BUSINESS_EVIDENCE_SOURCES:
            return "业务语义建议带有运行、评估或人工证据，可优先复核。"
        if candidate.candidate_type in {"metric", "relationship"}:
            return "系统发现可启用的指标或关联建议，确认后可提升问数覆盖。"
        return "普通候选，建议在高风险和高收益项之后处理。"

    def _evidence_summaries(self, candidate: SemanticGovernanceCandidateModel) -> list[str]:
        items = list(candidate.supporting_evidence_json or []) + list(candidate.conflicting_evidence_json or [])
        return [self._evidence_summary(item) for item in items[:8]]

    def _evidence_summary(self, item: dict[str, Any]) -> str:
        source_type = str(item.get("source_type") or "unknown")
        claim_type = str(item.get("claim_type") or "")
        direction = "冲突证据" if item.get("direction") == "conflict" else "支持证据"
        value = item.get("value") or {}
        if source_type == "golden_sql":
            question = value.get("question") if isinstance(value, dict) else None
            return f"{direction}: 黄金 SQL 曾引用该对象" + (f"，示例问题：{question}" if question else "。")
        if source_type == "shadow_equivalent":
            count = value.get("equivalent_run_count") if isinstance(value, dict) else None
            return f"{direction}: Shadow 对照结果一致" + (f" {count} 次。" if count else "。")
        if source_type == "evaluation_equivalent":
            return f"{direction}: 回归评估显示新旧链路结果等价。"
        if source_type == "runtime_pattern":
            total = value.get("total") if isinstance(value, dict) else None
            return f"{direction}: 最近问数运行稳定命中该对象" + (f" {total} 次。" if total else "。")
        if source_type == "aggregate_profile":
            ratios = value.get("pattern_ratios") if isinstance(value, dict) else {}
            if isinstance(ratios, dict):
                email = float(ratios.get("email_ratio") or 0)
                phone = float(ratios.get("phone_ratio") or 0)
                if max(email, phone) > 0:
                    return f"{direction}: 字段画像显示邮箱/手机号格式命中率约 {round(max(email, phone) * 100)}%。"
            return f"{direction}: 字段画像提供了空值率、基数、范围等聚合证据。"
        if source_type == "deterministic_validation":
            if claim_type == "sensitive":
                return f"{direction}: 确定性规则识别到敏感格式。"
            return f"{direction}: 通过确定性结构校验。"
        if source_type == "explicit_fk":
            return f"{direction}: 数据库结构或依赖关系提供了明确证据。"
        if source_type == "human_confirmation":
            return f"{direction}: 管理员历史决策已沉淀为反馈证据。"
        if source_type == "llm_proposal":
            return f"{direction}: LLM 根据表名、字段名和注释提出建议，仍需人工确认。"
        if source_type == "naming_rule":
            return f"{direction}: 命名规则提出建议，适合作为低风险初筛。"
        return f"{direction}: {source_type} · {claim_type or 'evidence'}。"

    def _editable_fields(self, candidate: SemanticGovernanceCandidateModel) -> list[dict[str, Any]]:
        patch = candidate.proposed_patch_json or {}
        if candidate.candidate_type == "business_semantics":
            return [
                {"key": "business_name", "label": "业务名", "type": "text", "value": patch.get("business_name", "")},
                {"key": "description", "label": "业务说明", "type": "textarea", "value": patch.get("description", "")},
                {"key": "synonyms", "label": "同义词", "type": "tags", "value": patch.get("synonyms") or []},
            ]
        if candidate.candidate_type == "mark_sensitive":
            return [{"key": "is_sensitive", "label": "标记为敏感", "type": "boolean", "value": bool(patch.get("is_sensitive"))}]
        fields = []
        if "status" in patch:
            fields.append({"key": "status", "label": "状态", "type": "select", "value": patch.get("status")})
        if "is_queryable" in patch:
            fields.append({"key": "is_queryable", "label": "允许问数", "type": "boolean", "value": bool(patch.get("is_queryable"))})
        if "sync_state" in patch:
            fields.append({"key": "sync_state", "label": "同步状态", "type": "select", "value": patch.get("sync_state")})
        return fields

    async def list_candidates(
        self,
        workspace_id: str,
        *,
        status: Optional[str] = None,
        target_type: Optional[str] = None,
        candidate_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        min_score: float = 0.0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            query = select(SemanticGovernanceCandidateModel).where(
                SemanticGovernanceCandidateModel.workspace_id == workspace_id,
                SemanticGovernanceCandidateModel.score >= max(0.0, min(1.0, min_score)),
            )
            if status and status != "all":
                query = query.where(SemanticGovernanceCandidateModel.status == status)
            elif not status:
                query = query.where(SemanticGovernanceCandidateModel.status.in_(["proposed", "needs_review", "blocked"]))
            if target_type:
                query = query.where(SemanticGovernanceCandidateModel.target_type == target_type)
            if candidate_type:
                query = query.where(SemanticGovernanceCandidateModel.candidate_type == candidate_type)
            if risk_level:
                query = query.where(SemanticGovernanceCandidateModel.risk_level == risk_level)
            rows = list((await session.execute(
                query.order_by(SemanticGovernanceCandidateModel.updated_at.desc()).limit(500)
            )).scalars())
            rows = [row for row in rows if not self._is_deprecated_business_candidate(row)]
            sorted_rows = sorted(rows, key=self._candidate_priority, reverse=True)
            payloads = []
            for row in sorted_rows[:min(max(limit, 1), 500)]:
                payloads.append(self._candidate_payload(row, await self._candidate_target_context(session, row)))
            return payloads

    async def get_candidate(self, workspace_id: str, candidate_id: int) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            candidate = await self._candidate(session, workspace_id, candidate_id)
            return self._candidate_payload(candidate, await self._candidate_target_context(session, candidate))

    async def _candidate(self, session, workspace_id: str, candidate_id: int) -> SemanticGovernanceCandidateModel:
        candidate = (await session.execute(select(SemanticGovernanceCandidateModel).where(
            SemanticGovernanceCandidateModel.id == candidate_id,
            SemanticGovernanceCandidateModel.workspace_id == workspace_id,
        ))).scalar_one_or_none()
        if not candidate:
            raise SemanticQueryError("not_found", "治理候选不存在", safe_to_fallback=False)
        return candidate

    async def accept_candidate(self, workspace_id: str, actor_id: str, candidate_id: int, edited_patch: Optional[dict[str, Any]] = None, reason: Optional[str] = None) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            candidate = await self._candidate(session, workspace_id, candidate_id)
            if candidate.status not in {"proposed", "needs_review", "blocked"}:
                raise SemanticQueryError("invalid_candidate_status", "该候选当前不能接受", safe_to_fallback=False)
            target = await self._target(session, workspace_id, candidate.target_type, candidate.target_id)
            if not target:
                raise SemanticQueryError("not_found", "候选目标不存在", safe_to_fallback=False)
            patch = edited_patch if edited_patch is not None else candidate.proposed_patch_json or {}
            await self._apply_patch(session, target, patch, human=True)
            now = datetime.now()
            target.confirmed_by = actor_id
            target.updated_at = now
            candidate.status = "accepted"
            candidate.applied_patch_json = {**patch, "target_version_after": now.isoformat()}
            candidate.decided_by = actor_id
            candidate.decision_reason = reason
            candidate.decided_at = now
            candidate.applied_at = now
            session.add(SemanticGovernanceEventModel(
                workspace_id=workspace_id, datasource_id=candidate.datasource_id, actor_id=actor_id,
                action="governance_candidate_accepted", object_type=candidate.target_type, object_id=candidate.target_id,
                payload_json={"candidate_id": candidate.id, "edited": edited_patch is not None, "patch": patch},
            ))
            await self._record_feedback_fact(session, candidate, "support", "human_confirmation", actor_id)
            await session.flush()
            return self._candidate_payload(candidate)

    async def reject_candidate(self, workspace_id: str, actor_id: str, candidate_id: int, reason: str, category: str) -> dict[str, Any]:
        allowed_categories = {"incorrect_mapping", "wrong_formula", "unsafe", "duplicate", "not_relevant"}
        if category not in allowed_categories:
            raise SemanticQueryError("invalid_rejection_category", "拒绝原因分类无效", safe_to_fallback=False)
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            candidate = await self._candidate(session, workspace_id, candidate_id)
            if candidate.status not in {"proposed", "needs_review", "blocked"}:
                raise SemanticQueryError("invalid_candidate_status", "该候选当前不能拒绝", safe_to_fallback=False)
            candidate.status = "rejected"
            candidate.decided_by = actor_id
            candidate.decision_reason = f"{category}: {reason}"[:2000]
            candidate.decided_at = datetime.now()
            session.add(SemanticGovernanceEventModel(
                workspace_id=workspace_id, datasource_id=candidate.datasource_id, actor_id=actor_id,
                action="governance_candidate_rejected", object_type=candidate.target_type, object_id=candidate.target_id,
                payload_json={"candidate_id": candidate.id, "category": category, "reason": reason},
            ))
            await self._record_feedback_fact(session, candidate, "conflict", "human_confirmation", actor_id)
            await session.flush()
            return self._candidate_payload(candidate)

    async def _record_feedback_fact(self, session, candidate: SemanticGovernanceCandidateModel, direction: str, source_type: str, actor_id: str) -> None:
        fingerprint = stable_fingerprint({"candidate": candidate.candidate_key, "direction": direction, "actor": actor_id})
        existing = (await session.execute(select(SemanticEvidenceFactModel).where(
            SemanticEvidenceFactModel.workspace_id == candidate.workspace_id,
            SemanticEvidenceFactModel.datasource_id == candidate.datasource_id,
            SemanticEvidenceFactModel.fact_fingerprint == fingerprint,
        ))).scalar_one_or_none()
        if existing:
            return
        session.add(SemanticEvidenceFactModel(
            workspace_id=candidate.workspace_id, datasource_id=candidate.datasource_id, run_id=candidate.run_id,
            subject_type=candidate.target_type.rstrip("s"), subject_id=candidate.target_id,
            claim_type="governance_feedback", claim_key=candidate.candidate_key,
            value_json={"candidate_id": candidate.id, "status": candidate.status}, source_type=source_type,
            source_ref=f"candidate:{candidate.id}", direction=direction, reliability=1.0, strength=1.0,
            fact_fingerprint=fingerprint, observed_at=datetime.now(), expires_at=None,
        ))

    async def sync_legacy_suggestion_decision(
        self,
        workspace_id: str,
        suggestion_id: int,
        actor_id: str,
        *,
        accepted: bool,
    ) -> None:
        """Keep the deprecated suggestion API mapped to candidates and feedback facts."""

        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            suggestion = (await session.execute(select(SemanticBusinessSuggestionModel).where(
                SemanticBusinessSuggestionModel.id == suggestion_id,
                SemanticBusinessSuggestionModel.workspace_id == workspace_id,
            ))).scalar_one_or_none()
            if not suggestion:
                return
            target_type = "tables" if suggestion.object_type == "table" else "columns"
            candidate = (await session.execute(select(SemanticGovernanceCandidateModel).where(
                SemanticGovernanceCandidateModel.workspace_id == workspace_id,
                SemanticGovernanceCandidateModel.datasource_id == suggestion.datasource_id,
                SemanticGovernanceCandidateModel.target_type == target_type,
                SemanticGovernanceCandidateModel.target_id == suggestion.object_id,
                SemanticGovernanceCandidateModel.candidate_type == "business_semantics",
            ).order_by(SemanticGovernanceCandidateModel.updated_at.desc()))).scalars().first()
            if candidate and candidate.status in {"proposed", "needs_review", "blocked"}:
                now = datetime.now()
                candidate.status = "accepted" if accepted else "rejected"
                candidate.decided_by = actor_id
                candidate.decided_at = now
                candidate.decision_reason = "deprecated_business_suggestion_api"
                if accepted:
                    candidate.applied_patch_json = candidate.proposed_patch_json or {}
                    candidate.applied_at = now
                await self._record_feedback_fact(
                    session,
                    candidate,
                    "support" if accepted else "conflict",
                    "human_confirmation",
                    actor_id,
                )
            else:
                fingerprint = stable_fingerprint({
                    "legacy_suggestion": suggestion.id,
                    "accepted": accepted,
                    "actor": actor_id,
                })
                session.add(SemanticEvidenceFactModel(
                    workspace_id=workspace_id,
                    datasource_id=suggestion.datasource_id,
                    subject_type=suggestion.object_type,
                    subject_id=suggestion.object_id,
                    claim_type="governance_feedback",
                    claim_key=f"business-suggestion:{suggestion.id}",
                    value_json={"suggestion_id": suggestion.id, "accepted": accepted},
                    source_type="human_confirmation",
                    source_ref=f"business-suggestion:{suggestion.id}",
                    direction="support" if accepted else "conflict",
                    reliability=1.0,
                    strength=1.0,
                    fact_fingerprint=fingerprint,
                    observed_at=datetime.now(),
                    expires_at=None,
                ))

    async def batch_decide(self, workspace_id: str, actor_id: str, candidate_ids: list[int], action: str, *, reason: str = "", category: str = "not_relevant") -> dict[str, Any]:
        if action not in {"accept", "reject"}:
            raise SemanticQueryError("invalid_batch_action", "批量操作必须是 accept 或 reject", safe_to_fallback=False)
        succeeded, failed = [], []
        for candidate_id in dict.fromkeys(candidate_ids[:100]):
            try:
                if action == "accept":
                    await self.accept_candidate(workspace_id, actor_id, candidate_id, reason=reason)
                else:
                    await self.reject_candidate(workspace_id, actor_id, candidate_id, reason, category)
                succeeded.append(candidate_id)
            except Exception as exc:  # noqa: BLE001
                failed.append({"candidate_id": candidate_id, "error": str(exc)})
        return {"succeeded": succeeded, "failed": failed}

    async def rollback_candidate(self, workspace_id: str, actor_id: str, candidate_id: int) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            candidate = await self._candidate(session, workspace_id, candidate_id)
            if candidate.status != "auto_applied":
                raise SemanticQueryError("invalid_candidate_status", "只能回滚自动应用的候选", safe_to_fallback=False)
            target = await self._target(session, workspace_id, candidate.target_type, candidate.target_id)
            if not target:
                raise SemanticQueryError("not_found", "候选目标不存在", safe_to_fallback=False)
            expected = (candidate.applied_patch_json or {}).get("target_version_after")
            actual = _serialize_datetime(getattr(target, "updated_at", None))
            if expected and actual and expected != actual:
                raise SemanticQueryError("candidate_conflict", "资产在自动应用后已被修改，不能直接回滚", safe_to_fallback=False)
            await self._apply_patch(session, target, candidate.before_json or {}, human=False)
            target.updated_at = datetime.now()
            candidate.status = "rolled_back"
            candidate.decided_by = actor_id
            candidate.decision_reason = "manual_rollback"
            candidate.decided_at = datetime.now()
            session.add(SemanticGovernanceEventModel(
                workspace_id=workspace_id, datasource_id=candidate.datasource_id, actor_id=actor_id,
                action="governance_candidate_rolled_back", object_type=candidate.target_type, object_id=candidate.target_id,
                payload_json={"candidate_id": candidate.id, "restored": candidate.before_json},
            ))
            await session.flush()
            return self._candidate_payload(candidate)

    async def evidence_for_object(self, workspace_id: str, object_type: str, object_id: int, limit: int = 100) -> list[dict[str, Any]]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            rows = list((await session.execute(select(SemanticEvidenceFactModel).where(
                SemanticEvidenceFactModel.workspace_id == workspace_id,
                SemanticEvidenceFactModel.subject_type == object_type.rstrip("s"),
                SemanticEvidenceFactModel.subject_id == object_id,
            ).order_by(SemanticEvidenceFactModel.observed_at.desc()).limit(min(max(limit, 1), 500)))).scalars())
            return [self._evidence_payload(row) for row in rows]

    def _evidence_payload(self, fact: SemanticEvidenceFactModel) -> dict[str, Any]:
        return {
            "evidence_id": fact.id, "run_id": fact.run_id, "subject_type": fact.subject_type,
            "subject_id": fact.subject_id, "claim_type": fact.claim_type, "claim_key": fact.claim_key,
            "value": fact.value_json or {}, "source_type": fact.source_type, "source_ref": fact.source_ref,
            "direction": fact.direction, "reliability": fact.reliability, "strength": fact.strength,
            "observed_at": _serialize_datetime(fact.observed_at), "expires_at": _serialize_datetime(fact.expires_at),
        }

    async def readiness_summary(self, workspace_id: str, datasource_id: int) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            last_run = (await session.execute(select(SemanticGovernanceRunModel).where(
                SemanticGovernanceRunModel.workspace_id == workspace_id,
                SemanticGovernanceRunModel.datasource_id == datasource_id,
            ).order_by(SemanticGovernanceRunModel.created_at.desc()))).scalars().first()
            candidates = list((await session.execute(select(SemanticGovernanceCandidateModel).where(
                SemanticGovernanceCandidateModel.workspace_id == workspace_id,
                SemanticGovernanceCandidateModel.datasource_id == datasource_id,
                SemanticGovernanceCandidateModel.status.in_(["proposed", "needs_review", "blocked"]),
            ))).scalars())
            candidates = [item for item in candidates if not self._is_deprecated_business_candidate(item)]
            latest_profile_at = (await session.execute(select(SemanticColumnProfileModel.profiled_at).where(
                SemanticColumnProfileModel.workspace_id == workspace_id,
                SemanticColumnProfileModel.datasource_id == datasource_id,
                SemanticColumnProfileModel.profile_status == "complete",
            ).order_by(SemanticColumnProfileModel.profiled_at.desc()).limit(1))).scalar_one_or_none()
            profiled_columns = len(set((await session.execute(select(SemanticColumnProfileModel.column_id).where(
                SemanticColumnProfileModel.workspace_id == workspace_id,
                SemanticColumnProfileModel.datasource_id == datasource_id,
                SemanticColumnProfileModel.expires_at > datetime.now(),
                SemanticColumnProfileModel.profile_status == "complete",
            ))).scalars()))
            profile_table_ids = list((await session.execute(select(SemanticTableModel.id).where(
                SemanticTableModel.workspace_id == workspace_id,
                SemanticTableModel.datasource_id == datasource_id,
                SemanticTableModel.sync_state == "current",
                SemanticTableModel.status != "disabled",
            ))).scalars())
            total_columns = len(list((await session.execute(select(SemanticColumnModel.id).where(
                SemanticColumnModel.workspace_id == workspace_id,
                SemanticColumnModel.datasource_id == datasource_id,
                SemanticColumnModel.sync_state == "current",
                SemanticColumnModel.status != "disabled",
                SemanticColumnModel.table_id.in_(profile_table_ids or [-1]),
            ))).scalars()))
            return {
                "last_run": self._run_payload(last_run) if last_run else None,
                "evidence_freshness": "fresh" if latest_profile_at and latest_profile_at >= datetime.now() - timedelta(days=PROFILE_TTL_DAYS) else "stale" if latest_profile_at else "missing",
                "last_profile_at": _serialize_datetime(latest_profile_at),
                "profile_coverage": round(profiled_columns / max(total_columns, 1), 4),
                "profiled_columns": profiled_columns,
                "total_columns": total_columns,
                "pending_candidates": len(candidates),
                "high_risk_candidates": sum(1 for item in candidates if item.risk_level in {"high", "critical"}),
                "recent_auto_applied": len(list((await session.execute(select(SemanticGovernanceCandidateModel.id).where(
                    SemanticGovernanceCandidateModel.workspace_id == workspace_id,
                    SemanticGovernanceCandidateModel.datasource_id == datasource_id,
                    SemanticGovernanceCandidateModel.status == "auto_applied",
                    SemanticGovernanceCandidateModel.applied_at >= datetime.now() - timedelta(days=30),
                ))).scalars())),
            }

    async def high_risk_candidates_for_objects(self, workspace_id: str, referenced: dict[str, Any]) -> list[dict[str, Any]]:
        object_pairs = []
        for target_type, key in (("tables", "table_ids"), ("columns", "column_ids"), ("metrics", "metric_ids"), ("relationships", "relationship_ids")):
            object_pairs.extend((target_type, int(value)) for value in referenced.get(key, []) if value is not None)
        if not object_pairs:
            return []
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            rows = list((await session.execute(select(SemanticGovernanceCandidateModel).where(
                SemanticGovernanceCandidateModel.workspace_id == workspace_id,
                SemanticGovernanceCandidateModel.status.in_(["needs_review", "blocked"]),
                SemanticGovernanceCandidateModel.risk_level.in_(["high", "critical"]),
            ))).scalars())
            wanted = set(object_pairs)
            return [self._candidate_payload(item) for item in rows if (item.target_type, item.target_id) in wanted]

    async def mark_stale_runs_failed(self, older_than_minutes: int = 30) -> int:
        now = datetime.now()
        legacy_cutoff = now - timedelta(minutes=older_than_minutes)
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            rows = list((await session.execute(select(SemanticGovernanceRunModel).where(
                SemanticGovernanceRunModel.status.in_(list(RUNNING_RUN_STATUSES)),
                (
                    (SemanticGovernanceRunModel.lease_expires_at.is_not(None) & (SemanticGovernanceRunModel.lease_expires_at < now))
                    | (SemanticGovernanceRunModel.lease_expires_at.is_(None) & (SemanticGovernanceRunModel.started_at < legacy_cutoff))
                ),
            ))).scalars())
            for run in rows:
                if run.status == "cancel_requested":
                    run.status = "cancelled"
                    run.stage = "cancelled"
                    run.progress = 100
                    run.error_message = "治理候选生成已取消"
                    run.completed_at = now
                elif int(run.attempt or 0) < int(run.max_attempts or 1):
                    run.status = "pending"
                    run.stage = "queued"
                    run.error_message = None
                    run.started_at = None
                    run.completed_at = None
                else:
                    run.status = "failed"
                    run.stage = "interrupted"
                    run.error_message = "治理运行因 worker 租约过期未完成，请重试"
                    run.completed_at = now
                run.worker_id = None
                run.run_token = None
                run.lease_expires_at = None
                run.heartbeat_at = None
            return len(rows)


_service: Optional[SemanticAutoGovernanceService] = None


def get_semantic_auto_governance_service() -> SemanticAutoGovernanceService:
    global _service
    if _service is None:
        _service = SemanticAutoGovernanceService()
    return _service
