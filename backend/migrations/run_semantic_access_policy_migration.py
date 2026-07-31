"""Apply the unified semantic access policy schema and legacy data migration."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db.database import get_async_db_manager  # noqa: E402


def _statements(sql: str) -> list[str]:
    without_comments = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )
    return [item.strip() for item in without_comments.split(";") if item.strip()]


async def migrate() -> None:
    migration_path = Path(__file__).with_name("add_semantic_access_policies.sql")
    statements = _statements(migration_path.read_text(encoding="utf-8"))
    db = get_async_db_manager()
    async with db.session_scope() as session:
        for index, statement in enumerate(statements, start=1):
            await session.execute(text(statement))
            print(f"[{index}/{len(statements)}] applied")
    print("Semantic access policy migration complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
