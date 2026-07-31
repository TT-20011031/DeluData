"""
数据库迁移脚本 - 会话隔离与数据库连接共享
"""
import asyncio
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.db.database import get_async_db_manager

async def run_migration():
    """执行数据库迁移"""
    print("开始执行数据库迁移...")
    
    db_manager = get_async_db_manager()
    
    migrations = [
        ("langgraph_checkpoints: 添加 user_id", 
         "ALTER TABLE langgraph_checkpoints ADD COLUMN user_id VARCHAR(36) NULL"),
        
        ("langgraph_checkpoints: 添加 user_id 索引", 
         "CREATE INDEX idx_langgraph_user_id ON langgraph_checkpoints(user_id)"),
        
        ("user_db_configs: 添加 workspace_id", 
         "ALTER TABLE user_db_configs ADD COLUMN workspace_id VARCHAR(128) NULL"),
        
        ("user_db_configs: 添加 configured_by", 
         "ALTER TABLE user_db_configs ADD COLUMN configured_by VARCHAR(64) NULL"),
        
        ("user_db_configs: 迁移数据", 
         "UPDATE user_db_configs SET workspace_id = user_id WHERE workspace_id IS NULL"),
        
        ("user_db_configs: 添加 workspace_id 索引", 
         "CREATE INDEX idx_workspace_id ON user_db_configs(workspace_id)"),
    ]
    
    async with db_manager.session_scope() as session:
        for i, (desc, sql) in enumerate(migrations, 1):
            try:
                await session.execute(text(sql))
                print(f"[{i}/{len(migrations)}] 成功: {desc}")
            except Exception as e:
                err_str = str(e)
                if "Duplicate column name" in err_str or "Duplicate key name" in err_str:
                    print(f"[{i}/{len(migrations)}] 跳过 (已存在): {desc}")
                else:
                    print(f"[{i}/{len(migrations)}] 失败: {desc}")
                    print(f"    错误: {err_str[:100]}")
        
        await session.commit()
    
    print("\n数据库迁移完成!")

if __name__ == "__main__":
    asyncio.run(run_migration())
