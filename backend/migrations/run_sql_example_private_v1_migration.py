"""Migrate SQL examples to account-private, permission-governed records.

Run from ``backend`` with::

    python -m migrations.run_sql_example_private_v1_migration

The migration is idempotent. Existing examples keep ``created_by`` as owner,
are disabled, and must be revalidated before they can participate in chat.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text

from app.core.db.database import get_async_db_manager
from app.models.config.sql_example_embeddings import sync_all_sql_examples_to_vector

MIGRATION_ID = "2026_08_sql_example_private_v1"

COLUMNS = {
    "normalized_question": "VARCHAR(500) NOT NULL DEFAULT '' COMMENT '参数化后的问题'",
    "validation_status": "VARCHAR(20) NOT NULL DEFAULT 'draft' COMMENT 'draft/valid/invalid/stale'",
    "validation_errors": "JSON NULL COMMENT '校验错误'",
    "parameters_json": "JSON NULL COMMENT '动态参数定义'",
    "intent_json": "JSON NULL COMMENT '安全的结构化语义意图'",
    "schema_fingerprint": "VARCHAR(128) NULL",
    "authorization_revision": "INT NOT NULL DEFAULT 0",
    "validation_model_version": "INT NOT NULL DEFAULT 1",
    "validated_at": "DATETIME NULL",
    "last_matched_at": "DATETIME NULL",
    "match_count": "INT NOT NULL DEFAULT 0",
}


async def _column_exists(session, table: str, column: str) -> bool:
    return bool(
        (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_schema=DATABASE() AND table_name=:table AND column_name=:column
                    """
                ),
                {"table": table, "column": column},
            )
        ).scalar_one()
    )


async def _table_exists(session, table: str) -> bool:
    return bool(
        (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema=DATABASE() AND table_name=:table
                    """
                ),
                {"table": table},
            )
        ).scalar_one()
    )


async def _index_exists(session, table: str, index_name: str) -> bool:
    return bool(
        (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM information_schema.statistics
                    WHERE table_schema=DATABASE() AND table_name=:table AND index_name=:index_name
                    """
                ),
                {"table": table, "index_name": index_name},
            )
        ).scalar_one()
    )


async def migrate() -> dict[str, object]:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        for name, ddl in COLUMNS.items():
            if not await _column_exists(session, "sql_examples", name):
                await session.execute(text(f"ALTER TABLE sql_examples ADD COLUMN `{name}` {ddl}"))

        await session.execute(
            text(
                """
                UPDATE sql_examples
                SET validation_status='stale', validation_errors=JSON_ARRAY(
                      JSON_OBJECT('code','migration_revalidation_required',
                                  'message','账号私有示例升级后需要重新校验')
                    ),
                    parameters_json=COALESCE(parameters_json, JSON_ARRAY()),
                    intent_json=COALESCE(intent_json, JSON_OBJECT()),
                    normalized_question=CASE
                      WHEN normalized_question IS NULL OR normalized_question='' THEN question
                      ELSE normalized_question END,
                    is_active=0, validated_at=NULL, authorization_revision=0,
                    vector_sync_status='pending_update'
                WHERE normalized_question IS NULL OR normalized_question=''
                """
            )
        )
        orphan_count = int(
            (
                await session.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM sql_examples example
                        LEFT JOIN sys_users user
                          ON BINARY user.id=BINARY example.created_by
                         AND BINARY user.workspace_id=BINARY example.workspace_id
                        WHERE user.id IS NULL
                        """
                    )
                )
            ).scalar_one()
            or 0
        )
        await session.execute(
            text(
                """
                UPDATE sql_examples example
                LEFT JOIN sys_users user
                  ON BINARY user.id=BINARY example.created_by
                 AND BINARY user.workspace_id=BINARY example.workspace_id
                SET example.validation_status='stale', example.is_active=0,
                    example.validation_errors=JSON_ARRAY(
                      JSON_OBJECT('code','owner_missing',
                                  'message','原创建账号不存在，等待管理员分配归属')
                    )
                WHERE user.id IS NULL
                """
            )
        )
        await session.execute(
            text(
                """
                UPDATE sql_examples example
                LEFT JOIN sql_example_groups owned_group
                  ON owned_group.id=example.group_id
                 AND BINARY owned_group.workspace_id=BINARY example.workspace_id
                 AND BINARY owned_group.created_by=BINARY example.created_by
                SET example.group_id=NULL
                WHERE example.group_id IS NOT NULL AND owned_group.id IS NULL
                """
            )
        )
        if not await _index_exists(session, "sql_examples", "ix_sql_examples_owner_state"):
            await session.execute(
                text(
                    "CREATE INDEX ix_sql_examples_owner_state "
                    "ON sql_examples (workspace_id, created_by, validation_status, is_active)"
                )
            )
        if not await _index_exists(session, "sql_example_groups", "ix_sql_example_groups_owner"):
            await session.execute(
                text(
                    "CREATE INDEX ix_sql_example_groups_owner "
                    "ON sql_example_groups (workspace_id, created_by)"
                )
            )
        report = {
            "migration_id": MIGRATION_ID,
            "orphan_examples": orphan_count,
            "existing_examples_disabled_for_revalidation": True,
        }
        if await _table_exists(session, "sys_schema_migrations"):
            await session.execute(
                text(
                    """
                    INSERT INTO sys_schema_migrations (migration_id, checksum, status, report_json)
                    VALUES (:migration_id, '', 'complete', CAST(:report AS JSON))
                    ON DUPLICATE KEY UPDATE status='complete', report_json=VALUES(report_json), applied_at=NOW()
                    """
                ),
                {"migration_id": MIGRATION_ID, "report": json.dumps(report, ensure_ascii=False)},
            )

    vector_report = await sync_all_sql_examples_to_vector(reset=True)
    return {**report, "vector_sync": vector_report}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(migrate()), ensure_ascii=False, indent=2))
