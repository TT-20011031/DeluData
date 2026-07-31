"""
创建 ingestion_tasks 表。

用法：
    cd backend
    python -m migrations.run_ingestion_tasks_migration
"""
from __future__ import annotations

import asyncio
import os
import platform
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db.database import get_async_db_manager


async def run_migration() -> None:
    print("=" * 80)
    print("Ingestion Tasks Migration")
    print("=" * 80)

    db = get_async_db_manager()
    async with db.session_scope() as session:
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS ingestion_tasks (
                    id VARCHAR(36) PRIMARY KEY,
                    file_id VARCHAR(36) NOT NULL,
                    workspace_id VARCHAR(36) NOT NULL COMMENT '租户ID（工作空间）',
                    user_id VARCHAR(36) NOT NULL,

                    status VARCHAR(32) NOT NULL DEFAULT 'pending',
                    stage VARCHAR(32) NOT NULL DEFAULT 'queued',
                    progress INT NOT NULL DEFAULT 0,
                    detail_json JSON NULL,
                    error_message TEXT NULL,

                    attempt INT NOT NULL DEFAULT 0,
                    max_attempts INT NOT NULL DEFAULT 3,

                    worker_id VARCHAR(128) NULL,
                    run_token VARCHAR(64) NULL,
                    lease_expires_at DATETIME NULL,
                    heartbeat_at DATETIME NULL,

                    payload_json JSON NULL,

                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    started_at DATETIME NULL,
                    finished_at DATETIME NULL,

                    INDEX idx_tasks_status_lease(status, lease_expires_at),
                    INDEX idx_tasks_ws_status_created(workspace_id, status, created_at),
                    INDEX idx_tasks_file(file_id),
                    CONSTRAINT fk_ingestion_tasks_file_id
                        FOREIGN KEY (file_id) REFERENCES files(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                COMMENT='知识库入库任务队列'
                """
            )
        )

    print("[OK] ingestion_tasks 表已创建/已存在")
    print("=" * 80)
    print("迁移完成")
    print("=" * 80)


if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_migration())
