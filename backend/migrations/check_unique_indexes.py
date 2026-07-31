"""检查数据库唯一索引"""
import asyncio
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from sqlalchemy import text
from app.core.db.database import get_async_db_manager

async def check_unique_indexes():
    db = get_async_db_manager()
    async with db.session_scope() as s:
        result = await s.execute(text('''
            SELECT 
                TABLE_NAME,
                INDEX_NAME,
                GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) as COLUMNS,
                NON_UNIQUE
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
            AND INDEX_NAME != 'PRIMARY'
            GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE
            ORDER BY TABLE_NAME, INDEX_NAME
        '''))
        
        print("=" * 80)
        print("数据库唯一索引检查")
        print("=" * 80)
        print(f"{'表名':<30} {'索引名':<30} {'列':<20} {'唯一'}")
        print("-" * 80)
        
        unique_issues = []
        for row in result.fetchall():
            table, index, columns, non_unique = row
            is_unique = "是" if non_unique == 0 else "否"
            print(f"{table:<30} {index:<30} {columns:<20} {is_unique}")
            
            # 检测潜在问题：唯一索引不包含 workspace_id
            if non_unique == 0 and 'workspace_id' not in columns:
                unique_issues.append((table, index, columns))
        
        if unique_issues:
            print("\n" + "=" * 80)
            print("⚠️ 需要修改为复合唯一索引的表：")
            print("=" * 80)
            for table, index, columns in unique_issues:
                print(f"  - {table}.{index} ({columns}) → 需添加 workspace_id")

if __name__ == "__main__":
    asyncio.run(check_unique_indexes())
