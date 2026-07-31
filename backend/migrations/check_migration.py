"""验证迁移结果"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.db.database import get_async_db_manager

async def check():
    db = get_async_db_manager()
    async with db.session_scope() as s:
        # 检查 langgraph_checkpoints
        result = await s.execute(text("DESCRIBE langgraph_checkpoints"))
        print("langgraph_checkpoints 表结构:")
        for row in result.fetchall():
            print(f"  {row[0]}: {row[1]}")
        
        print()
        
        # 检查 user_db_configs
        result = await s.execute(text("DESCRIBE user_db_configs"))
        print("user_db_configs 表结构:")
        for row in result.fetchall():
            print(f"  {row[0]}: {row[1]}")

if __name__ == "__main__":
    asyncio.run(check())
