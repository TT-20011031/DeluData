"""Enable organization-based semantic access targets and retire legacy targets.

Run with::

    python -m migrations.run_semantic_org_target_migration

The migration is deliberately idempotent. Legacy bindings and immutable versions
remain stored for audit, but no longer participate in runtime authorization.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db.database import get_async_db_manager  # noqa: E402


MIGRATION_ACTION = "semantic_policy.organization_targets.cutover"


async def _column_exists(session, table_name: str, column_name: str) -> bool:
    return bool((await session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :table_name "
            "AND column_name = :column_name"
        ),
        {"table_name": table_name, "column_name": column_name},
    )).scalar())


async def run_migration() -> None:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        if not await _column_exists(
            session, "semantic_policy_bindings", "include_descendants",
        ):
            await session.execute(text(
                "ALTER TABLE semantic_policy_bindings "
                "ADD COLUMN include_descendants TINYINT(1) NOT NULL DEFAULT 0 "
                "AFTER target_id"
            ))

        legacy_counts = (await session.execute(text(
            "SELECT workspace_id, COUNT(*) AS binding_count "
            "FROM semantic_policy_bindings "
            "WHERE target_type IN ('role_binding', 'user_exception') AND status = 1 "
            "GROUP BY workspace_id"
        ))).all()

        await session.execute(text(
            "UPDATE semantic_policy_bindings SET status = 0 "
            "WHERE target_type IN ('role_binding', 'user_exception') AND status <> 0"
        ))

        for workspace_id, binding_count in legacy_counts:
            already_recorded = (await session.execute(text(
                "SELECT id FROM sys_authorization_audit_events "
                "WHERE workspace_id = :workspace_id AND action = :action LIMIT 1"
            ), {"workspace_id": workspace_id, "action": MIGRATION_ACTION})).scalar_one_or_none()
            if already_recorded is not None:
                continue
            await session.execute(text(
                "INSERT INTO sys_authorization_audit_events "
                "(workspace_id, actor_id, action, target_type, target_id, "
                "before_json, after_json, reason, created_at) VALUES "
                "(:workspace_id, 'system:migration', :action, 'semantic_policy_binding', "
                "NULL, JSON_OBJECT('active_legacy_bindings', :binding_count), "
                "JSON_OBJECT('active_legacy_bindings', 0, 'preserved_history', TRUE), "
                "'Organization, position and account semantic targets cut-over', NOW())"
            ), {
                "workspace_id": workspace_id,
                "action": MIGRATION_ACTION,
                "binding_count": int(binding_count or 0),
            })

    print("Semantic organization target migration complete.")


if __name__ == "__main__":
    asyncio.run(run_migration())
