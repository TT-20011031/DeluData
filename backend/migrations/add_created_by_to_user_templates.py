"""
数据库迁移脚本 - 为 user_prompt_templates 表添加 created_by 字段

实现用户级模板隔离：
- 用户创建的模板只有用户自己能看到
- 管理员创建的系统模板所有人可见
"""
import asyncio
import sys
sys.path.insert(0, '.')

from sqlalchemy import text
from app.core.db.database import get_async_db_manager


async def migrate():
    """添加 created_by 字段到 user_prompt_templates 表"""
    db = get_async_db_manager()
    async with db.session_scope() as session:
        try:
            await session.execute(text("""
                ALTER TABLE user_prompt_templates 
                ADD COLUMN created_by VARCHAR(64) DEFAULT NULL 
                COMMENT '创建者用户 ID（私有模板隔离）'
            """))
            print("✅ 添加 created_by 字段成功")
        except Exception as e:
            if 'Duplicate column' in str(e):
                print("ℹ️ created_by 字段已存在")
            else:
                print(f"⚠️ 添加失败: {e}")
        
        # 添加索引
        try:
            await session.execute(text("""
                CREATE INDEX idx_created_by ON user_prompt_templates (created_by)
            """))
            print("✅ 添加 created_by 索引成功")
        except Exception as e:
            if 'Duplicate' in str(e):
                print("ℹ️ created_by 索引已存在")
            else:
                print(f"⚠️ 索引添加失败: {e}")
        
        print("🎉 迁移完成！")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(migrate())
    finally:
        loop.close()
