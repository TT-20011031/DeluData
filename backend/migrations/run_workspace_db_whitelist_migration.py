"""Migration runner for workspace_db_whitelists table."""

import asyncio
import sys

from sqlalchemy import text

sys.path.insert(0, ".")

from app.core.db.database import get_async_db_manager  # noqa: E402


SQL = """
CREATE TABLE IF NOT EXISTS workspace_db_whitelists (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    is_enabled TINYINT(1) NOT NULL DEFAULT 0,
    allowed_endpoints JSON NOT NULL,
    note TEXT NULL,
    updated_by VARCHAR(64) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_workspace_db_whitelists_workspace_id (workspace_id),
    KEY ix_workspace_db_whitelists_workspace_id (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def migrate() -> None:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        await session.execute(text(SQL))
    print("workspace_db_whitelists migration completed")


if __name__ == "__main__":
    asyncio.run(migrate())
