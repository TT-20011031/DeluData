"""
Add soft-delete columns and index for files table.

Usage:
    cd backend
    python -m migrations.run_file_soft_delete_migration
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
INDEX_NAME = "ix_files_workspace_deleted_status"


async def _column_exists(session, table_name: str, column_name: str) -> bool:
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
        {"table_name": table_name, "column_name": column_name},
    )
    return int(result.scalar() or 0) > 0


async def _index_exists(session, table_name: str, index_name: str) -> bool:
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
        {"table_name": table_name, "index_name": index_name},
    )
    return int(result.scalar() or 0) > 0


async def run_migration() -> None:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        if not await _column_exists(session, TABLE_NAME, "is_deleted"):
            await session.execute(
                text(
                    """
                    ALTER TABLE files
                    ADD COLUMN is_deleted TINYINT(1) NOT NULL DEFAULT 0
                    """
                )
            )
            print("[OK] added files.is_deleted")
        else:
            print("[SKIP] files.is_deleted exists")

        if not await _column_exists(session, TABLE_NAME, "deleted_at"):
            await session.execute(
                text(
                    """
                    ALTER TABLE files
                    ADD COLUMN deleted_at TIMESTAMP NULL
                    """
                )
            )
            print("[OK] added files.deleted_at")
        else:
            print("[SKIP] files.deleted_at exists")

        if not await _column_exists(session, TABLE_NAME, "delete_status"):
            await session.execute(
                text(
                    """
                    ALTER TABLE files
                    ADD COLUMN delete_status VARCHAR(20) NOT NULL DEFAULT 'active'
                    """
                )
            )
            print("[OK] added files.delete_status")
        else:
            print("[SKIP] files.delete_status exists")

        if not await _column_exists(session, TABLE_NAME, "delete_error"):
            await session.execute(
                text(
                    """
                    ALTER TABLE files
                    ADD COLUMN delete_error TEXT NULL
                    """
                )
            )
            print("[OK] added files.delete_error")
        else:
            print("[SKIP] files.delete_error exists")

        if not await _column_exists(session, TABLE_NAME, "delete_op_id"):
            await session.execute(
                text(
                    """
                    ALTER TABLE files
                    ADD COLUMN delete_op_id VARCHAR(36) NULL
                    """
                )
            )
            print("[OK] added files.delete_op_id")
        else:
            print("[SKIP] files.delete_op_id exists")

        await session.execute(
            text(
                """
                UPDATE files
                SET delete_status = 'active'
                WHERE delete_status IS NULL OR delete_status = ''
                """
            )
        )
        print("[OK] normalized delete_status")

        if not await _index_exists(session, TABLE_NAME, INDEX_NAME):
            await session.execute(
                text(
                    """
                    CREATE INDEX ix_files_workspace_deleted_status
                    ON files (workspace_id, is_deleted, status)
                    """
                )
            )
            print("[OK] created index ix_files_workspace_deleted_status")
        else:
            print("[SKIP] index ix_files_workspace_deleted_status exists")


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

