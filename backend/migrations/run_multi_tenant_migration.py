"""
多租户架构 - Python 迁移脚本

执行数据库迁移并验证
"""
import asyncio
import sys
import os

# 添加项目根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.db.database import get_async_db_manager


async def run_migration():
    """执行多租户迁移"""
    print("=" * 60)
    print("多租户架构 - 数据库迁移")
    print("=" * 60)
    
    db = get_async_db_manager()
    
    migrations = [
        # 1. 创建工作空间表
        ("创建 sys_workspaces 表", """
            CREATE TABLE IF NOT EXISTS sys_workspaces (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(128) NOT NULL,
                code VARCHAR(64) UNIQUE NOT NULL,
                owner_id VARCHAR(36),
                plan VARCHAR(32) DEFAULT 'free',
                max_users INT DEFAULT 10,
                is_active BOOLEAN DEFAULT TRUE,
                description TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """),
        
        # 2. 插入默认工作空间
        ("插入默认工作空间", """
            INSERT IGNORE INTO sys_workspaces (id, name, code, plan, max_users, description)
            VALUES ('default', '默认工作空间', 'default', 'enterprise', 9999, '系统默认工作空间')
        """),
        
        # 3. 添加平台权限
        ("添加 platform:admin 权限", """
            INSERT IGNORE INTO sys_permissions (code, module, description)
            VALUES ('platform:admin', 'PLATFORM', '平台超管权限')
        """),
        
        # 4. 更新用户绑定
        ("绑定用户到默认工作空间", """
            UPDATE sys_users SET workspace_id = 'default' 
            WHERE workspace_id IS NULL OR workspace_id = ''
        """),
    ]
    
    async with db.session_scope() as session:
        for i, (desc, sql) in enumerate(migrations, 1):
            try:
                await session.execute(text(sql))
                print(f"[{i}/{len(migrations)}] ✅ {desc}")
            except Exception as e:
                err = str(e)
                if "Duplicate" in err or "already exists" in err:
                    print(f"[{i}/{len(migrations)}] ⏭️ 跳过: {desc} (已存在)")
                else:
                    print(f"[{i}/{len(migrations)}] ❌ 失败: {desc}")
                    print(f"    错误: {err[:100]}")
        
        await session.commit()
    
    print("\n" + "=" * 60)
    print("验证迁移结果")
    print("=" * 60)
    
    async with db.session_scope() as session:
        # 验证工作空间
        result = await session.execute(text("SELECT id, name, code FROM sys_workspaces LIMIT 5"))
        print("\n工作空间:")
        for row in result.fetchall():
            print(f"  - {row[0]}: {row[2]} ({row[1]})")
        
        # 验证权限
        result = await session.execute(text("SELECT code FROM sys_permissions WHERE code = 'platform:admin'"))
        perm = result.scalar_one_or_none()
        print(f"\n平台权限: {'✅ 已添加' if perm else '❌ 未找到'}")
    
    print("\n迁移完成！")


if __name__ == "__main__":
    asyncio.run(run_migration())
