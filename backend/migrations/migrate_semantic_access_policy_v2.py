"""Switch semantic access control to the structured, default-deny v2 model.

The migration intentionally does not translate v1 policies. Existing policy rows
remain available for audit, but are disabled together with their compiled effects.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db.database import get_async_db_manager  # noqa: E402


async def _column_exists(session, table_name: str, column_name: str) -> bool:
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :table_name AND column_name = :column_name"
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return bool(result.scalar())


async def _index_exists(session, table_name: str, index_name: str) -> bool:
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :table_name AND index_name = :index_name"
        ),
        {"table_name": table_name, "index_name": index_name},
    )
    return bool(result.scalar())


async def _drop_column_if_exists(session, table_name: str, column_name: str) -> None:
    if await _column_exists(session, table_name, column_name):
        await session.execute(text(f"ALTER TABLE `{table_name}` DROP COLUMN `{column_name}`"))


async def migrate() -> None:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        first_switch = not await _column_exists(session, "semantic_access_policies", "model_version")
        if first_switch:
            await session.execute(
                text(
                    "ALTER TABLE semantic_access_policies "
                    "ADD COLUMN model_version INT NOT NULL DEFAULT 1 AFTER validation_json"
                )
            )

        # Always enforce the cut-over rule. This also covers databases where
        # model_version was added earlier but v1 rows were not yet disabled.
        await session.execute(
            text(
                "UPDATE semantic_access_policies "
                "SET status = 'disabled' WHERE model_version = 1 AND status <> 'disabled'"
            )
        )
        await session.execute(
            text(
                "UPDATE semantic_access_policy_effects AS effect "
                "JOIN semantic_access_policies AS policy ON policy.id = effect.policy_id "
                "SET effect.enabled = 0 "
                "WHERE policy.model_version = 1 AND effect.enabled <> 0"
            )
        )

        if not await _index_exists(
            session,
            "semantic_access_policies",
            "ix_semantic_access_policy_model_version",
        ):
            await session.execute(
                text(
                    "CREATE INDEX ix_semantic_access_policy_model_version "
                    "ON semantic_access_policies (model_version)"
                )
            )
        await session.execute(
            text(
                "ALTER TABLE semantic_access_policies "
                "MODIFY COLUMN model_version INT NOT NULL DEFAULT 2"
            )
        )
        for table_name, columns in {
            "semantic_tables": (
                "visible_role_ids",
                "visible_user_ids",
                "row_scope_enabled",
                "row_scope_mode",
                "row_scope_user_column_id",
                "row_scope_dept_column_id",
                "row_permission_mode",
            ),
            "semantic_columns": ("visible_role_ids", "visible_user_ids"),
            "semantic_metrics": ("visible_role_ids", "visible_user_ids"),
        }.items():
            for column_name in columns:
                await _drop_column_if_exists(session, table_name, column_name)
    print("Semantic access policy v2 migration complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
