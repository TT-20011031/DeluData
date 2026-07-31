"""Deployment-wide default-deny cut-over for the permission evidence chain.

Run only after the additive migration, compatible backend, worker and frontend are
deployed. The script prints impact statistics before applying the idempotent change.
Existing bindings and policy versions are preserved for audit.

Run once without arguments to review the report, then run again with ``--apply``.
"""

from __future__ import annotations

import asyncio
import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db.database import get_async_db_manager  # noqa: E402


ACTION = "semantic_access.evidence_default_deny_cutover"


async def run_migration(*, apply: bool = False) -> None:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        stats = (await session.execute(text(
            "SELECT d.workspace_id, COUNT(DISTINCT d.id) datasource_count, "
            "COUNT(DISTINCT CASE WHEN b.status = 1 THEN b.id END) active_binding_count, "
            "COUNT(DISTINCT u.id) account_count "
            "FROM semantic_datasources d "
            "LEFT JOIN semantic_policy_bindings b "
            "ON BINARY b.workspace_id=BINARY d.workspace_id "
            "AND BINARY b.datasource_id=BINARY d.id "
            "LEFT JOIN sys_users u "
            "ON BINARY u.workspace_id=BINARY d.workspace_id AND u.disabled=0 "
            "GROUP BY d.workspace_id ORDER BY d.workspace_id"
        ))).mappings().all()
        for row in stats:
            print(dict(row))
        if not apply:
            print("Report only. Re-run with --apply after reviewing the impact statistics.")
            return

        await session.execute(text(
            "UPDATE semantic_datasources SET access_bootstrap_required=1 "
            "WHERE access_bootstrap_required<>1"
        ))
        await session.execute(text(
            "UPDATE semantic_policy_bindings SET status=0 "
            "WHERE status<>0"
        ))
        for row in stats:
            exists = (await session.execute(text(
                "SELECT id FROM sys_authorization_audit_events "
                "WHERE workspace_id=:workspace_id AND action=:action LIMIT 1"
            ), {"workspace_id": row["workspace_id"], "action": ACTION})).scalar_one_or_none()
            if exists is not None:
                continue
            await session.execute(text(
                "INSERT INTO sys_authorization_audit_events "
                "(workspace_id, actor_id, action, target_type, target_id, "
                "before_json, after_json, reason, created_at) VALUES "
                "(:workspace_id, 'system:migration', :action, 'semantic_datasource', "
                "NULL, JSON_OBJECT('datasources', :datasources, "
                "'active_bindings', :bindings, 'accounts', :accounts), "
                "JSON_OBJECT('access_bootstrap_required', TRUE, "
                "'active_bindings', 0, 'workspace_admin_bypass_preserved', TRUE), "
                "'Global auditable permission-evidence cut-over', NOW())"
            ), {
                "workspace_id": row["workspace_id"],
                "action": ACTION,
                "datasources": int(row["datasource_count"] or 0),
                "bindings": int(row["active_binding_count"] or 0),
                "accounts": int(row["account_count"] or 0),
            })
    print("Permission evidence default-deny cut-over complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true",
        help="apply the global default-deny cut-over after printing impact statistics",
    )
    asyncio.run(run_migration(apply=parser.parse_args().apply))
