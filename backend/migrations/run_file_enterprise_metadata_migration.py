"""Add enterprise metadata columns to files.

Usage:
    cd backend
    python -m migrations.run_file_enterprise_metadata_migration
"""
from __future__ import annotations

import asyncio
import os
import platform
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db.database import get_async_db_manager


TABLE_NAME = "files"
INDEX_NAME = "ix_files_workspace_metadata"


async def _column_exists(session, column_name: str) -> bool:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {"table_name": TABLE_NAME, "column_name": column_name},
    )
    return int(result.scalar() or 0) > 0


async def _index_exists(session) -> bool:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND index_name = :index_name
            """
        ),
        {"table_name": TABLE_NAME, "index_name": INDEX_NAME},
    )
    return int(result.scalar() or 0) > 0


async def run_migration() -> None:
    db = get_async_db_manager()
    columns = {
        "document_type": "VARCHAR(50) NULL",
        "business_domain": "VARCHAR(100) NULL",
        "confidentiality_level": "VARCHAR(50) NULL",
        "effective_from": "DATETIME NULL",
        "effective_until": "DATETIME NULL",
        "external_ref": "VARCHAR(128) NULL",
    }
    async with db.session_scope() as session:
        for name, ddl in columns.items():
            if await _column_exists(session, name):
                print(f"[SKIP] files.{name} exists")
                continue
            await session.execute(text(f"ALTER TABLE files ADD COLUMN {name} {ddl}"))
            print(f"[OK] added files.{name}")

        if await _index_exists(session):
            print(f"[SKIP] index {INDEX_NAME} exists")
        else:
            await session.execute(
                text(
                    """
                    CREATE INDEX ix_files_workspace_metadata
                    ON files (
                        workspace_id,
                        dept_id,
                        visibility,
                        business_domain,
                        document_type,
                        confidentiality_level
                    )
                    """
                )
            )
            print(f"[OK] created index {INDEX_NAME}")


async def _main() -> None:
    try:
        await run_migration()
    finally:
        try:
            await get_async_db_manager().dispose()
        except Exception:
            pass


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_main())
