"""Link editable first-permission drafts to permission evidence sets."""

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
    path = Path(__file__).with_name("add_semantic_access_evidence_drafts.sql")
    db = get_async_db_manager()
    async with db.session_scope() as session:
        for statement in _statements(path.read_text(encoding="utf-8")):
            await session.execute(text(statement))
    print("Semantic access evidence draft migration complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
