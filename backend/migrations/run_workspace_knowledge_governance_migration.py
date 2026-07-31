"""Migration runner for workspace_knowledge_governance table."""

import asyncio
import sys

from sqlalchemy import text

sys.path.insert(0, ".")

from app.core.db.database import get_async_db_manager  # noqa: E402


SQL = """
CREATE TABLE IF NOT EXISTS workspace_knowledge_governance (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    workspace_id VARCHAR(64) NOT NULL,
    storage_quota_bytes BIGINT NULL,
    upload_enabled TINYINT(1) NOT NULL DEFAULT 1,
    delete_enabled TINYINT(1) NOT NULL DEFAULT 1,
    rename_enabled TINYINT(1) NOT NULL DEFAULT 1,
    move_enabled TINYINT(1) NOT NULL DEFAULT 1,
    create_folder_enabled TINYINT(1) NOT NULL DEFAULT 1,
    note TEXT NULL,
    updated_by VARCHAR(64) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_workspace_knowledge_governance_workspace_id (workspace_id),
    KEY ix_workspace_knowledge_governance_workspace_id (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def migrate() -> None:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        await session.execute(text(SQL))
    print("workspace_knowledge_governance migration completed")


if __name__ == "__main__":
    asyncio.run(migrate())
