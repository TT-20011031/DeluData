"""Trusted semantic governance: schema previews, atomic apply and readiness."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import inspect, select

from app.core.db.database import get_async_db_manager
from app.core.db.mysql_connection_policy import create_mysql_engine
from app.models.config.db_config import (
    UserDBConfig,
    get_user_db_config_async,
    get_workspace_db_config_async,
)
from app.models.config.semantic import (
    SemanticColumnModel,
    SemanticDatasourceModel,
    SemanticGovernanceEventModel,
    SemanticMetricModel,
    SemanticRelationshipModel,
    SemanticScanRunModel,
    SemanticTableModel,
)
from app.services.semantic_query_service import SemanticQueryError
from app.services import semantic_schema_diff as _schema_diff


SCAN_TTL_HOURS = 24
RUNTIME_MODES = {"disabled", "shadow", "trusted"}


# Runtime service and tests share dependency-free primitives.
normalize_schema_snapshot = _schema_diff.normalize_schema_snapshot
schema_fingerprint = _schema_diff.schema_fingerprint
diff_schema_snapshots = _schema_diff.diff_schema_snapshots
summarize_diff = _schema_diff.summarize_diff


class SemanticGovernanceService:
    async def _get_config(self, workspace_id: str, user_id: Optional[str] = None) -> UserDBConfig:
        config = await get_workspace_db_config_async(workspace_id)
        if not config and user_id:
            config = await get_user_db_config_async(user_id)
        if not config or not config.is_active:
            raise SemanticQueryError("datasource_missing", "工作区未配置可扫描的数据库连接", safe_to_fallback=False)
        return config

    def inspect_snapshot(self, config: UserDBConfig) -> dict[str, Any]:
        connection_url = config.get_connection_url()
        engine = create_mysql_engine(
            connection_url,
            pool_pre_ping=True,
            pool_size=1,
        )
        try:
            inspector = inspect(engine)
            table_items: list[dict[str, Any]] = []
            names = [(name, "table") for name in inspector.get_table_names()]
            try:
                names.extend((name, "view") for name in inspector.get_view_names())
            except Exception:  # noqa: BLE001
                pass
            for table_name, kind in names:
                indexed = {
                    column
                    for index in (inspector.get_indexes(table_name) or [])
                    for column in (index.get("column_names") or [])
                    if column
                }
                try:
                    comment = str((inspector.get_table_comment(table_name) or {}).get("text") or "")
                except Exception:  # noqa: BLE001
                    comment = ""
                table_items.append(
                    {
                        "name": table_name,
                        "kind": kind,
                        "comment": comment,
                        "columns": [
                            {
                                "name": column["name"],
                                "type": str(column.get("type") or ""),
                                "comment": str(column.get("comment") or ""),
                                "primary_key": bool(column.get("primary_key")),
                                "indexed": column["name"] in indexed,
                                "ordinal_position": position,
                            }
                            for position, column in enumerate(inspector.get_columns(table_name))
                        ],
                    }
                )
            return normalize_schema_snapshot(table_items)
        finally:
            engine.dispose()

    async def _get_or_create_datasource(self, workspace_id: str, config: UserDBConfig):
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = (
                await session.execute(
                    select(SemanticDatasourceModel).where(
                        SemanticDatasourceModel.workspace_id == workspace_id,
                        SemanticDatasourceModel.host == config.host,
                        SemanticDatasourceModel.port == config.port,
                        SemanticDatasourceModel.database == config.database,
                    )
                )
            ).scalar_one_or_none()
            if not datasource:
                datasource = SemanticDatasourceModel(
                    workspace_id=workspace_id,
                    name=config.database,
                    host=config.host,
                    port=config.port,
                    database=config.database,
                    dialect="mysql",
                    runtime_mode="disabled",
                    semantic_sql_enabled=False,
                    semantic_sql_fallback_enabled=True,
                )
                session.add(datasource)
                await session.flush()
            return datasource.id

    async def preview_scan(self, workspace_id: str, actor_id: str) -> dict[str, Any]:
        config = await self._get_config(workspace_id, actor_id)
        datasource_id = await self._get_or_create_datasource(workspace_id, config)
        snapshot = self.inspect_snapshot(config)
        target_fingerprint = schema_fingerprint(snapshot)
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = (
                await session.execute(select(SemanticDatasourceModel).where(SemanticDatasourceModel.id == datasource_id))
            ).scalar_one()
            base_snapshot = None
            if datasource.last_applied_scan_id:
                previous = (
                    await session.execute(
                        select(SemanticScanRunModel).where(SemanticScanRunModel.id == datasource.last_applied_scan_id)
                    )
                ).scalar_one_or_none()
                base_snapshot = previous.snapshot_json if previous else None
            diff = diff_schema_snapshots(base_snapshot, snapshot)
            impacts = await self._build_impacts(session, workspace_id, datasource_id, diff)
            now = datetime.now()
            run = SemanticScanRunModel(
                workspace_id=workspace_id,
                datasource_id=datasource_id,
                created_by=actor_id,
                status="previewed",
                base_fingerprint=datasource.schema_fingerprint,
                target_fingerprint=target_fingerprint,
                snapshot_json=snapshot,
                diff_json=diff,
                impact_json=impacts,
                summary_json=summarize_diff(diff),
                created_at=now,
                expires_at=now + timedelta(hours=SCAN_TTL_HOURS),
            )
            session.add(run)
            await session.flush()
            datasource.last_scan_at = now
            return self._scan_payload(run)

    async def _build_impacts(self, session, workspace_id: str, datasource_id: int, diff: list[dict[str, Any]]) -> list[dict[str, Any]]:
        changed_columns = {item["physical_identity"] for item in diff if item["object_type"] == "column" and item.get("blocking")}
        changed_tables = {item["physical_identity"] for item in diff if item["object_type"] == "table" and item.get("blocking")}
        columns = list((await session.execute(select(SemanticColumnModel).where(SemanticColumnModel.workspace_id == workspace_id, SemanticColumnModel.datasource_id == datasource_id))).scalars())
        impacted_column_ids = {item.id for item in columns if f"{item.physical_table}.{item.physical_name}" in changed_columns or item.physical_table in changed_tables}
        impacted_table_ids = {item.table_id for item in columns if item.id in impacted_column_ids}
        metrics = list((await session.execute(select(SemanticMetricModel).where(SemanticMetricModel.workspace_id == workspace_id, SemanticMetricModel.datasource_id == datasource_id))).scalars())
        relationships = list((await session.execute(select(SemanticRelationshipModel).where(SemanticRelationshipModel.workspace_id == workspace_id, SemanticRelationshipModel.datasource_id == datasource_id))).scalars())
        impacts = [
            {"object_type": "metric", "object_id": item.id, "name": item.business_name, "reason": "schema_dependency_changed"}
            for item in metrics
            if item.table_id in impacted_table_ids or item.column_id in impacted_column_ids or item.time_column_id in impacted_column_ids
        ]
        impacts.extend(
            {"object_type": "relationship", "object_id": item.id, "name": item.description or str(item.id), "reason": "schema_dependency_changed"}
            for item in relationships
            if item.left_table_id in impacted_table_ids or item.right_table_id in impacted_table_ids or item.left_column_id in impacted_column_ids or item.right_column_id in impacted_column_ids
        )
        return impacts

    def _scan_payload(self, run: SemanticScanRunModel) -> dict[str, Any]:
        return {
            "scan_id": run.id,
            "status": run.status,
            "base_fingerprint": run.base_fingerprint,
            "target_fingerprint": run.target_fingerprint,
            "expires_at": run.expires_at.isoformat() if run.expires_at else None,
            "applied_at": run.applied_at.isoformat() if run.applied_at else None,
            "summary": run.summary_json or {},
            "diff_items": run.diff_json or [],
            "affected_assets": run.impact_json or [],
            "blocking_changes": [item for item in (run.diff_json or []) if item.get("blocking")],
        }

    async def list_scans(self, workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            # Do not load snapshot_json for the history list. Real schema snapshots can be large,
            # and sorting full rows may exhaust MySQL sort memory on small production instances.
            rows = (await session.execute(
                select(
                    SemanticScanRunModel.id,
                    SemanticScanRunModel.status,
                    SemanticScanRunModel.base_fingerprint,
                    SemanticScanRunModel.target_fingerprint,
                    SemanticScanRunModel.diff_json,
                    SemanticScanRunModel.impact_json,
                    SemanticScanRunModel.summary_json,
                    SemanticScanRunModel.expires_at,
                    SemanticScanRunModel.applied_at,
                )
                .where(SemanticScanRunModel.workspace_id == workspace_id)
                .order_by(SemanticScanRunModel.created_at.desc())
                .limit(min(limit, 100))
            )).mappings().all()
            runs = [SimpleNamespace(**dict(row)) for row in rows]
            return [self._scan_payload(run) for run in runs]

    async def get_scan(self, workspace_id: str, scan_id: int) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            run = (await session.execute(select(SemanticScanRunModel).where(SemanticScanRunModel.id == scan_id, SemanticScanRunModel.workspace_id == workspace_id))).scalar_one_or_none()
            if not run:
                raise SemanticQueryError("not_found", "扫描记录不存在", safe_to_fallback=False)
            return self._scan_payload(run)

    async def apply_scan(self, workspace_id: str, scan_id: int, actor_id: str) -> dict[str, Any]:
        db_manager = get_async_db_manager()
        applied_payload: dict[str, Any]
        newly_applied = False
        async with db_manager.session_scope() as session:
            run = (await session.execute(select(SemanticScanRunModel).where(SemanticScanRunModel.id == scan_id, SemanticScanRunModel.workspace_id == workspace_id).with_for_update())).scalar_one_or_none()
            if not run:
                raise SemanticQueryError("not_found", "扫描记录不存在", safe_to_fallback=False)
            if run.status == "applied":
                return self._scan_payload(run)
            if run.status != "previewed" or run.expires_at <= datetime.now():
                run.status = "expired"
                await session.commit()
                raise SemanticQueryError("scan_expired", "扫描预览已过期，请重新扫描", safe_to_fallback=False)
            datasource = (await session.execute(select(SemanticDatasourceModel).where(SemanticDatasourceModel.id == run.datasource_id, SemanticDatasourceModel.workspace_id == workspace_id).with_for_update())).scalar_one()
            if datasource.schema_fingerprint != run.base_fingerprint:
                raise SemanticQueryError("scan_conflict", "语义目录基线已变化，请重新扫描", safe_to_fallback=False)
            config = await self._get_config(workspace_id, actor_id)
            live_snapshot = self.inspect_snapshot(config)
            if schema_fingerprint(live_snapshot) != run.target_fingerprint:
                raise SemanticQueryError("scan_conflict", "数据库 Schema 在预览后发生变化，请重新扫描", safe_to_fallback=False)
            counts = await self._apply_snapshot(session, run, datasource)
            now = datetime.now()
            run.status = "applied"
            run.applied_at = now
            run.summary_json = {**(run.summary_json or {}), **counts}
            datasource.schema_fingerprint = run.target_fingerprint
            datasource.last_applied_scan_id = run.id
            datasource.last_scan_at = now
            session.add(SemanticGovernanceEventModel(workspace_id=workspace_id, datasource_id=datasource.id, actor_id=actor_id, action="scan_applied", scan_id=run.id, payload_json=run.summary_json))
            applied_payload = self._scan_payload(run)
            newly_applied = True
        return applied_payload

    async def _apply_snapshot(self, session, run: SemanticScanRunModel, datasource: SemanticDatasourceModel) -> dict[str, int]:
        from app.services.semantic_query_service import _is_sensitive_column_name, _should_auto_disable_table

        existing_tables = list((await session.execute(select(SemanticTableModel).where(SemanticTableModel.workspace_id == run.workspace_id, SemanticTableModel.datasource_id == datasource.id))).scalars())
        table_by_name = {item.physical_name: item for item in existing_tables}
        seen_tables: set[str] = set()
        seen_columns: set[tuple[int, str]] = set()
        changed_column_ids: set[int] = set()
        orphaned_table_ids: set[int] = set()
        created_tables = created_columns = 0

        for table_payload in run.snapshot_json.get("tables", []):
            name = table_payload["name"]
            seen_tables.add(name)
            table = table_by_name.get(name)
            if not table:
                disabled = _should_auto_disable_table(name)
                table = SemanticTableModel(workspace_id=run.workspace_id, datasource_id=datasource.id, physical_name=name, business_name=name, description="", physical_comment=table_payload.get("comment") or "", synonyms=[], status="disabled" if disabled else "confirmed", is_queryable=not disabled, sync_state="current", origin_source="physical_scan", management_mode="system", confidence=1.0, evidence_json={"scan_id": run.id}, stale_reason_json={}, schema_fingerprint=run.target_fingerprint, last_seen_scan_id=run.id)
                session.add(table)
                await session.flush()
                table_by_name[name] = table
                created_tables += 1
            else:
                table.physical_comment = table_payload.get("comment") or ""
                table.last_seen_scan_id = run.id
                table.schema_fingerprint = run.target_fingerprint
                if table.sync_state == "orphaned":
                    table.sync_state = "stale" if table.management_mode == "human" else "current"
                    table.stale_reason_json = {"reason": "physical_object_restored", "scan_id": run.id} if table.management_mode == "human" else {}

            columns = list((await session.execute(select(SemanticColumnModel).where(SemanticColumnModel.table_id == table.id, SemanticColumnModel.workspace_id == run.workspace_id))).scalars())
            column_by_name = {item.physical_name: item for item in columns}
            for column_payload in table_payload.get("columns", []):
                column_name = column_payload["name"]
                column = column_by_name.get(column_name)
                if not column:
                    column = SemanticColumnModel(workspace_id=run.workspace_id, datasource_id=datasource.id, table_id=table.id, physical_table=name, physical_name=column_name, data_type=column_payload["type"], business_name=column_name, description="", physical_comment=column_payload.get("comment") or "", synonyms=[], status="confirmed", is_queryable=table.status == "confirmed" and table.is_queryable, is_sensitive=_is_sensitive_column_name(column_name), is_primary_key=column_payload.get("primary_key", False), is_indexed=column_payload.get("indexed", False), ordinal_position=column_payload.get("ordinal_position", 0), sync_state="current", origin_source="physical_scan", management_mode="system", confidence=1.0, evidence_json={"scan_id": run.id}, stale_reason_json={}, schema_fingerprint=run.target_fingerprint, last_seen_scan_id=run.id)
                    session.add(column)
                    await session.flush()
                    created_columns += 1
                else:
                    type_changed = str(column.data_type).lower() != str(column_payload["type"]).lower()
                    column.data_type = column_payload["type"]
                    column.physical_comment = column_payload.get("comment") or ""
                    column.is_primary_key = column_payload.get("primary_key", False)
                    column.is_indexed = column_payload.get("indexed", False)
                    column.ordinal_position = column_payload.get("ordinal_position", 0)
                    column.last_seen_scan_id = run.id
                    column.schema_fingerprint = run.target_fingerprint
                    if type_changed:
                        column.sync_state = "stale"
                        column.is_queryable = False
                        column.stale_reason_json = {"reason": "column_type_changed", "scan_id": run.id}
                        changed_column_ids.add(column.id)
                    elif column.sync_state == "orphaned":
                        column.sync_state = "stale" if column.management_mode == "human" else "current"
                        column.stale_reason_json = {"reason": "physical_object_restored", "scan_id": run.id} if column.management_mode == "human" else {}
                seen_columns.add((table.id, column_name))

            for column in columns:
                if (table.id, column.physical_name) not in seen_columns:
                    column.sync_state = "orphaned"
                    column.is_queryable = False
                    column.stale_reason_json = {"reason": "physical_column_removed", "scan_id": run.id}
                    changed_column_ids.add(column.id)

        for table in existing_tables:
            if table.physical_name not in seen_tables:
                table.sync_state = "orphaned"
                table.is_queryable = False
                table.stale_reason_json = {"reason": "physical_table_removed", "scan_id": run.id}
                orphaned_table_ids.add(table.id)

        metrics = list((await session.execute(select(SemanticMetricModel).where(SemanticMetricModel.workspace_id == run.workspace_id, SemanticMetricModel.datasource_id == datasource.id))).scalars())
        relationships = list((await session.execute(select(SemanticRelationshipModel).where(SemanticRelationshipModel.workspace_id == run.workspace_id, SemanticRelationshipModel.datasource_id == datasource.id))).scalars())
        stale_metrics = stale_relationships = 0
        for metric in metrics:
            if metric.table_id in orphaned_table_ids or metric.column_id in changed_column_ids or metric.time_column_id in changed_column_ids:
                metric.sync_state = "stale"
                metric.is_queryable = False
                metric.stale_reason_json = {"reason": "schema_dependency_changed", "scan_id": run.id}
                stale_metrics += 1
        for relationship in relationships:
            if relationship.left_table_id in orphaned_table_ids or relationship.right_table_id in orphaned_table_ids or relationship.left_column_id in changed_column_ids or relationship.right_column_id in changed_column_ids:
                relationship.sync_state = "stale"
                relationship.is_queryable = False
                relationship.stale_reason_json = {"reason": "schema_dependency_changed", "scan_id": run.id}
                stale_relationships += 1
        from app.services.semantic_query_service import get_semantic_query_service

        suggestion_counts = await get_semantic_query_service()._ensure_scan_suggestions(
            session,
            run.workspace_id,
            datasource.id,
        )
        return {"created_tables": created_tables, "created_columns": created_columns, "stale_metrics": stale_metrics, "stale_relationships": stale_relationships, **suggestion_counts}

    async def get_readiness(self, workspace_id: str, user_id: Optional[str] = None) -> dict[str, Any]:
        config = await self._get_config(workspace_id, user_id)
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = (await session.execute(select(SemanticDatasourceModel).where(SemanticDatasourceModel.workspace_id == workspace_id, SemanticDatasourceModel.is_active == True).order_by(SemanticDatasourceModel.updated_at.desc()))).scalars().first()  # noqa: E712
            if not datasource or not datasource.last_applied_scan_id:
                return {"status": "not_scanned", "runtime_mode": datasource.runtime_mode if datasource else "disabled", "structure": {"connected": True, "has_applied_scan": False, "pending_drift": False}, "semantics": {"queryable_tables": 0, "queryable_columns": 0, "confirmed_metrics": 0, "confirmed_relationships": 0, "stale_assets": 0}, "security": {"readonly_configured": config.has_readonly_config(), "blocking": not config.has_readonly_config()}, "governance": {"last_run": None, "evidence_freshness": "missing", "profile_coverage": 0, "pending_candidates": 0, "high_risk_candidates": 0, "recent_auto_applied": 0}, "blockers": ["尚未应用 Schema 扫描"]}
            models = [SemanticTableModel, SemanticColumnModel, SemanticMetricModel, SemanticRelationshipModel]
            rows = [list((await session.execute(select(model).where(model.workspace_id == workspace_id, model.datasource_id == datasource.id))).scalars()) for model in models]
            tables, columns, metrics, relationships = rows
            stale_count = sum(1 for group in rows for item in group if item.sync_state != "current")
            pending_row = (await session.execute(
                select(
                    SemanticScanRunModel.target_fingerprint,
                    SemanticScanRunModel.summary_json,
                )
                .where(
                    SemanticScanRunModel.workspace_id == workspace_id,
                    SemanticScanRunModel.datasource_id == datasource.id,
                    SemanticScanRunModel.status == "previewed",
                )
                .order_by(SemanticScanRunModel.created_at.desc())
                .limit(1)
            )).mappings().first()
            pending_drift = bool(
                pending_row
                and pending_row["target_fingerprint"] != datasource.schema_fingerprint
                and (pending_row["summary_json"] or {}).get("blocking")
            )
            readonly = config.has_readonly_config()
            queryable_tables = sum(1 for item in tables if item.status == "confirmed" and item.is_queryable and item.sync_state == "current")
            queryable_columns = sum(1 for item in columns if item.status == "confirmed" and item.is_queryable and item.sync_state == "current")
            # Temporary rollout policy: readonly credentials are advisory only.
            # The profiler still uses a SELECT-only executor, and a dedicated
            # readonly account can be re-enabled as a blocking requirement later.
            status = "degraded" if pending_drift else "ready" if queryable_tables and queryable_columns and stale_count == 0 else "partially_ready"
            blockers = []
            if pending_drift:
                blockers.append("存在尚未应用的关键 Schema 漂移")
            if False and not readonly:
                blockers.append("未配置只读数据库账号")
            if stale_count:
                blockers.append(f"存在 {stale_count} 个过期或失联语义资产")
        from app.services.semantic_auto_governance_service import get_semantic_auto_governance_service

        governance = await get_semantic_auto_governance_service().readiness_summary(workspace_id, datasource.id)
        return {"status": status, "runtime_mode": datasource.runtime_mode, "structure": {"connected": True, "has_applied_scan": True, "pending_drift": pending_drift, "schema_fingerprint": datasource.schema_fingerprint, "last_scan_at": datasource.last_scan_at.isoformat() if datasource.last_scan_at else None}, "semantics": {"queryable_tables": queryable_tables, "queryable_columns": queryable_columns, "confirmed_metrics": sum(1 for item in metrics if item.status == "confirmed" and item.is_queryable and item.sync_state == "current"), "confirmed_relationships": sum(1 for item in relationships if item.status == "confirmed" and item.is_queryable and item.sync_state == "current"), "stale_assets": stale_count}, "security": {"readonly_configured": readonly, "blocking": False, "advisory": not readonly}, "governance": governance, "blockers": blockers}

    async def check_question(self, workspace_id: str, user_id: str, question: str) -> dict[str, Any]:
        """Dry-run semantic resolution; deliberately does not compile or execute SQL."""
        from app.services.semantic_query_service import get_semantic_query_service

        query_service = get_semantic_query_service()
        catalog = await query_service.get_catalog(workspace_id)
        if not catalog:
            return {"answerable": False, "status": "blocked", "error_type": "datasource_missing", "blockers": ["尚未建立语义目录"], "required_actions": ["预览并应用 Schema 扫描"], "referenced_objects": {}}
        try:
            intent = await query_service.extract_intent_hybrid(question, catalog)
            access = await query_service._load_user_access(user_id, workspace_id)
            plan = query_service.resolve_plan(intent, catalog, access)
            referenced_objects = {
                "table_ids": plan.table_ids,
                "column_ids": plan.permission_column_ids,
                "metric_ids": plan.metric_ids,
                "relationship_ids": plan.relationship_ids,
            }
            from app.services.semantic_auto_governance_service import get_semantic_auto_governance_service

            high_risk = await get_semantic_auto_governance_service().high_risk_candidates_for_objects(workspace_id, referenced_objects)
            if high_risk:
                return {
                    "answerable": False,
                    "status": "blocked",
                    "error_type": "governance_review_required",
                    "blockers": ["问题引用了尚未处理的高风险治理候选"],
                    "required_actions": ["复核相关语义资产后重试"],
                    "referenced_objects": referenced_objects,
                    "governance_candidates": high_risk,
                }
            return {
                "answerable": True,
                "status": "ready",
                "error_type": None,
                "blockers": [],
                "required_actions": [],
                "referenced_objects": referenced_objects,
                "intent": intent.model_dump(mode="json"),
            }
        except SemanticQueryError as exc:
            status = "needs_clarification" if exc.error_type in {"clarification_required", "join_path_ambiguous", "semantic_object_not_found", "time_field_missing"} else "unsafe" if exc.error_type in {"permission_denied", "sensitive_object", "permission_check_failed", "unsafe_sql"} else "blocked"
            actions = {
                "join_path_ambiguous": ["确认缺失的 Join 关系"],
                "semantic_object_not_found": ["补充或确认相关语义对象"],
                "clarification_required": ["补充问题中的歧义信息"],
                "time_field_missing": ["为指标绑定时间字段"],
            }.get(exc.error_type, ["检查语义模型和权限配置"])
            return {"answerable": False, "status": status, "error_type": exc.error_type, "blockers": [exc.message], "required_actions": actions, "referenced_objects": exc.details or {}}

    async def set_runtime_mode(self, workspace_id: str, actor_id: str, mode: str) -> dict[str, Any]:
        if mode not in RUNTIME_MODES:
            raise SemanticQueryError("invalid_runtime_mode", "运行模式必须是 disabled、shadow 或 trusted", safe_to_fallback=False)
        readiness = await self.get_readiness(workspace_id, actor_id)
        if mode == "trusted" and (not readiness["structure"].get("has_applied_scan") or readiness["security"].get("blocking")):
            raise SemanticQueryError("readiness_blocked", "结构或安全就绪度未通过，不能切换 Trusted", safe_to_fallback=False, details=readiness)
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            datasource = (await session.execute(select(SemanticDatasourceModel).where(SemanticDatasourceModel.workspace_id == workspace_id, SemanticDatasourceModel.is_active == True).order_by(SemanticDatasourceModel.updated_at.desc()))).scalars().first()  # noqa: E712
            if not datasource:
                raise SemanticQueryError("datasource_missing", "请先扫描数据库", safe_to_fallback=False)
            previous = datasource.runtime_mode
            datasource.runtime_mode = mode
            datasource.semantic_sql_enabled = mode != "disabled"
            datasource.semantic_sql_fallback_enabled = mode == "shadow"
            session.add(SemanticGovernanceEventModel(workspace_id=workspace_id, datasource_id=datasource.id, actor_id=actor_id, action="runtime_mode_changed", payload_json={"before": previous, "after": mode}))
            return {"runtime_mode": mode, "warning": "Shadow 模式会并行执行新旧链路，数据库查询负载可能接近翻倍。" if mode == "shadow" else None}


_service: Optional[SemanticGovernanceService] = None


def get_semantic_governance_service() -> SemanticGovernanceService:
    global _service
    if _service is None:
        _service = SemanticGovernanceService()
    return _service
