from __future__ import annotations

import asyncio
import os
import platform
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db.database import get_async_db_manager


def _log(status: str, msg: str) -> None:
    print(f"[{status}] {msg}")


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


async def run_migration() -> None:
    print("=" * 72)
    print("Workspace Feature Flags Migration")
    print("=" * 72)

    db = get_async_db_manager()
    async with db.session_scope() as session:
        if not await _column_exists(session, "sys_workspaces", "museum_enabled"):
            await session.execute(
                text(
                    """
                    ALTER TABLE sys_workspaces
                    ADD COLUMN museum_enabled BOOLEAN NOT NULL DEFAULT FALSE
                    COMMENT '是否开通 Museum'
                    """
                )
            )
            _log("OK", "sys_workspaces.museum_enabled added")
        else:
            _log("SKIP", "sys_workspaces.museum_enabled already exists")

        if not await _column_exists(session, "sys_workspaces", "kiosk_enabled"):
            await session.execute(
                text(
                    """
                    ALTER TABLE sys_workspaces
                    ADD COLUMN kiosk_enabled BOOLEAN NOT NULL DEFAULT FALSE
                    COMMENT '是否开通 Kiosk'
                    """
                )
            )
            _log("OK", "sys_workspaces.kiosk_enabled added")
        else:
            _log("SKIP", "sys_workspaces.kiosk_enabled already exists")

    print("=" * 72)
    print("Workspace feature flags migration complete")
    print("=" * 72)


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_migration())
