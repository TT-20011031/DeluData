"""
迁移脚本：science_reward_policies 新增 max_claims_per_period 列

执行方式：python migrations/add_max_claims_migration.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from app.core.db.database import get_async_db_manager


async def migrate():
    engine = get_async_db_manager().engine
    async with engine.begin() as conn:
        # 检查列是否已存在
        result = await conn.execute(
            text("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_name = 'science_reward_policies'
                AND column_name = 'max_claims_per_period'
            """)
        )
        exists = result.scalar()
        if exists:
            print("列 max_claims_per_period 已存在，跳过迁移。")
            return

        await conn.execute(
            text("""
                ALTER TABLE science_reward_policies
                ADD COLUMN max_claims_per_period INTEGER NOT NULL DEFAULT 1
            """)
        )
        print("✅ 迁移完成：science_reward_policies 新增 max_claims_per_period 列")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
