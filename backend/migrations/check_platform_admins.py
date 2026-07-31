"""
检查 sys_platform_admins 表是否存在
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.db.database import get_async_db_manager


async def check():
    db = get_async_db_manager()
    async with db.session_scope() as session:
        result = await session.execute(text('SHOW TABLES LIKE "sys_platform_admins"'))
        row = result.fetchone()
        if row:
            print("✅ sys_platform_admins 表存在")
            # 检查默认账号
            admin_result = await session.execute(text('SELECT username FROM sys_platform_admins LIMIT 1'))
            admin = admin_result.fetchone()
            if admin:
                print(f"✅ 默认管理员账号: {admin[0]}")
            else:
                print("⚠️  没有管理员账号")
        else:
            print("❌ sys_platform_admins 表不存在")


if __name__ == "__main__":
    asyncio.run(check())
