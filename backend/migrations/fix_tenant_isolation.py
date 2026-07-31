"""
多租户漏洞修复迁移脚本

修复审计报告中指出的关键问题：
1. langgraph_checkpoints 表添加 workspace_id 列
2. folders 表添加 workspace_id 列
"""
import sys
import os

# 设置 Python 路径
backend_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_path)

import asyncio
from sqlalchemy import text
from app.core.db.database import get_async_db_manager


async def run_migration():
    """执行迁移"""
    async def _column_exists(session, table: str, column: str) -> bool:
        stmt = text(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND COLUMN_NAME = :column
            """
        )
        result = await session.execute(stmt, {"table": table, "column": column})
        return (result.scalar_one() or 0) > 0

    async def _index_exists(session, table: str, index_name: str) -> bool:
        stmt = text(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND INDEX_NAME = :index_name
            """
        )
        result = await session.execute(stmt, {"table": table, "index_name": index_name})
        return (result.scalar_one() or 0) > 0

    print("=" * 60)
    print("多租户漏洞修复迁移")
    print("=" * 60)

    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        # 1. langgraph_checkpoints.workspace_id
        if not await _column_exists(session, "langgraph_checkpoints", "workspace_id"):
            try:
                await session.execute(text(
                    """
                    ALTER TABLE langgraph_checkpoints
                    ADD COLUMN workspace_id VARCHAR(36) DEFAULT 'default'
                    COMMENT '租户ID（工作空间）';
                    """
                ))
                print("[1/6] ✅ 为 langgraph_checkpoints 添加 workspace_id")
            except Exception as e:
                print(f"[1/6] ❌ 为 langgraph_checkpoints 添加 workspace_id: {e}")
        else:
            print("[1/6] ⏭️ 为 langgraph_checkpoints 添加 workspace_id (已存在，跳过)")

        # 2. langgraph_checkpoints.workspace_id index
        if not await _index_exists(session, "langgraph_checkpoints", "idx_checkpoints_workspace"):
            try:
                await session.execute(text(
                    """
                    CREATE INDEX idx_checkpoints_workspace
                    ON langgraph_checkpoints(workspace_id);
                    """
                ))
                print("[2/6] ✅ 为 langgraph_checkpoints.workspace_id 创建索引")
            except Exception as e:
                print(f"[2/6] ❌ 为 langgraph_checkpoints.workspace_id 创建索引: {e}")
        else:
            print("[2/6] ⏭️ 为 langgraph_checkpoints.workspace_id 创建索引 (已存在，跳过)")

        # 3. folders.workspace_id
        if not await _column_exists(session, "folders", "workspace_id"):
            try:
                await session.execute(text(
                    """
                    ALTER TABLE folders
                    ADD COLUMN workspace_id VARCHAR(36) DEFAULT 'default'
                    COMMENT '租户ID（工作空间）';
                    """
                ))
                print("[3/6] ✅ 为 folders 添加 workspace_id")
            except Exception as e:
                print(f"[3/6] ❌ 为 folders 添加 workspace_id: {e}")
        else:
            print("[3/6] ⏭️ 为 folders 添加 workspace_id (已存在，跳过)")

        # 4. folders.workspace_id index
        if not await _index_exists(session, "folders", "idx_folders_workspace"):
            try:
                await session.execute(text(
                    """
                    CREATE INDEX idx_folders_workspace
                    ON folders(workspace_id);
                    """
                ))
                print("[4/6] ✅ 为 folders.workspace_id 创建索引")
            except Exception as e:
                print(f"[4/6] ❌ 为 folders.workspace_id 创建索引: {e}")
        else:
            print("[4/6] ⏭️ 为 folders.workspace_id 创建索引 (已存在，跳过)")

        # 5. 设置默认值（列存在时才执行）
        if await _column_exists(session, "langgraph_checkpoints", "workspace_id"):
            try:
                await session.execute(text(
                    """
                    UPDATE langgraph_checkpoints
                    SET workspace_id = 'default'
                    WHERE workspace_id IS NULL;
                    """
                ))
                print("[5/6] ✅ 设置现有 langgraph_checkpoints 记录的 workspace_id")
            except Exception as e:
                print(f"[5/6] ❌ 设置现有 langgraph_checkpoints 记录的 workspace_id: {e}")
        else:
            print("[5/6] ⏭️ 设置现有 langgraph_checkpoints 记录的 workspace_id (列不存在)")

        if await _column_exists(session, "folders", "workspace_id"):
            try:
                await session.execute(text(
                    """
                    UPDATE folders
                    SET workspace_id = 'default'
                    WHERE workspace_id IS NULL;
                    """
                ))
                print("[6/6] ✅ 设置现有 folders 记录的 workspace_id")
            except Exception as e:
                print(f"[6/6] ❌ 设置现有 folders 记录的 workspace_id: {e}")
        else:
            print("[6/6] ⏭️ 设置现有 folders 记录的 workspace_id (列不存在)")

        await session.commit()

    print("=" * 60)
    print("迁移完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_migration())
