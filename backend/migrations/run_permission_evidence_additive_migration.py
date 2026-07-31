"""Create the auditable permission-evidence chain without changing live access.

Run with::

    python -m migrations.run_permission_evidence_additive_migration

This migration is idempotent. The destructive/default-deny cut-over is deliberately
kept in ``run_permission_evidence_cutover.py`` and is never invoked here.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db.database import Base, get_async_db_manager  # noqa: E402
from app.models.auth.organization_semantic import (  # noqa: E402,F401
    OrganizationSemanticProfileModel,
    OrganizationSemanticProfileVersionModel,
    WorkspaceBusinessContextModel,
)
from app.models.auth.organization import DepartmentModel  # noqa: E402
from app.models.config.permission_evidence import (  # noqa: E402,F401
    SemanticAccessEvidenceAssetModel,
    SemanticAccessEvidenceRelationModel,
    SemanticAccessEvidenceSetModel,
    SemanticPermissionEvidenceRunModel,
)


async def _column_exists(session, table_name: str, column_name: str) -> bool:
    return bool((await session.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :table_name "
        "AND column_name = :column_name"
    ), {"table_name": table_name, "column_name": column_name})).scalar())


async def _add_column(session, table: str, column: str, ddl: str) -> None:
    if not await _column_exists(session, table, column):
        await session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


async def _add_index(session, table: str, index_name: str, columns: str) -> None:
    exists = bool((await session.execute(text(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema=DATABASE() AND table_name=:table_name "
        "AND index_name=:index_name"
    ), {"table_name": table, "index_name": index_name})).scalar())
    if not exists:
        await session.execute(text(
            f"CREATE INDEX {index_name} ON {table} ({columns})"
        ))


async def run_migration() -> None:
    db = get_async_db_manager()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with db.session_scope() as session:
        await _add_column(
            session, "workspace_business_contexts", "industry",
            "VARCHAR(200) NULL",
        )
        for column in (
            "core_offerings_json", "business_objects_json", "business_processes_json",
            "customer_types_json", "operating_regions_json", "special_terms_json",
            "data_governance_constraints_json",
        ):
            await _add_column(
                session, "workspace_business_contexts", column, "JSON NULL",
            )
        await _add_column(
            session, "semantic_datasources", "access_bootstrap_required",
            "TINYINT(1) NOT NULL DEFAULT 0",
        )
        await _add_column(
            session, "semantic_datasources", "active_evidence_set_id",
            "INT NULL",
        )
        await _add_index(
            session, "semantic_datasources",
            "ix_semantic_datasources_active_evidence_set_id", "active_evidence_set_id",
        )
        for table in ("semantic_tables", "semantic_columns"):
            await _add_column(
                session, table, "business_semantics_status",
                "VARCHAR(20) NOT NULL DEFAULT 'pending'",
            )
            await _add_column(
                session, table, "business_semantics_revision",
                "INT NOT NULL DEFAULT 0",
            )
            await _add_column(
                session, table, "business_semantics_reviewed_by",
                "VARCHAR(64) NULL",
            )
            await _add_column(
                session, table, "business_semantics_reviewed_at",
                "DATETIME NULL",
            )
            await _add_index(
                session, table, f"ix_{table}_business_semantics_status",
                "workspace_id, datasource_id, business_semantics_status",
            )
            await session.execute(text(
                f"UPDATE {table} SET "
                "business_semantics_status = CASE "
                "WHEN management_mode = 'human' THEN 'confirmed' ELSE 'pending' END, "
                "business_semantics_revision = CASE "
                "WHEN management_mode = 'human' AND business_semantics_revision = 0 "
                "THEN 1 ELSE business_semantics_revision END, "
                "business_semantics_reviewed_by = CASE "
                "WHEN management_mode = 'human' THEN confirmed_by "
                "ELSE business_semantics_reviewed_by END, "
                "business_semantics_reviewed_at = CASE "
                "WHEN management_mode = 'human' THEN COALESCE(confirmed_at, updated_at) "
                "ELSE business_semantics_reviewed_at END"
            ))

        # Existing workspaces may have configured organizations but no business
        # context or profile rows. Queue only missing top-level profiles; the
        # worker computes the real fingerprint from optional context + org facts.
        existing_profile_orgs = {
            (row.workspace_id, int(row.org_unit_id))
            for row in (await session.execute(
                select(OrganizationSemanticProfileModel)
            )).scalars()
        }
        top_departments = list((await session.execute(select(DepartmentModel).where(
            DepartmentModel.parent_id.is_(None),
            DepartmentModel.status == True,  # noqa: E712
        ))).scalars())
        if top_departments:
            from app.services.organization_semantic_service import OrganizationSemanticService
            profile_service = OrganizationSemanticService()
            for department in top_departments:
                key = (department.workspace_id, int(department.id))
                if key in existing_profile_orgs:
                    continue
                await profile_service._enqueue_profile_in_session(
                    session,
                    department.workspace_id,
                    int(department.id),
                    "system:migration",
                    force=False,
                )

    print("Permission evidence additive migration complete; live access unchanged.")


if __name__ == "__main__":
    asyncio.run(run_migration())
