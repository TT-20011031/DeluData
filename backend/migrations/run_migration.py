"""
数据库迁移脚本 - 添加分组和向量同步状态字段
"""
import asyncio
import sys
sys.path.insert(0, '.')

from sqlalchemy import text
from app.core.db.database import get_async_db_manager


async def migrate():
    db = get_async_db_manager()
    async with db.session_scope() as session:
        # 1. 添加 group_id 列
        try:
            await session.execute(text(
                "ALTER TABLE sql_examples ADD COLUMN group_id INT COMMENT '所属分组ID'"
            ))
            print("✅ 添加 group_id 列成功")
        except Exception as e:
            if 'Duplicate column' in str(e):
                print("ℹ️ group_id 列已存在")
            else:
                print(f"⚠️ group_id: {e}")
        
        # 2. 添加 vector_sync_status 列
        try:
            await session.execute(text(
                "ALTER TABLE sql_examples ADD COLUMN vector_sync_status VARCHAR(20) DEFAULT 'synced' COMMENT '向量库同步状态'"
            ))
            print("✅ 添加 vector_sync_status 列成功")
        except Exception as e:
            if 'Duplicate column' in str(e):
                print("ℹ️ vector_sync_status 列已存在")
            else:
                print(f"⚠️ vector_sync_status: {e}")
        
        # 3. 创建分组表
        try:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS sql_example_groups (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    name VARCHAR(100) NOT NULL COMMENT '分组名称',
                    description VARCHAR(500) COMMENT '分组描述',
                    color VARCHAR(20) DEFAULT '#3B82F6' COMMENT '分组颜色',
                    workspace_id VARCHAR(64) NOT NULL COMMENT '工作空间ID',
                    created_by VARCHAR(64) NOT NULL COMMENT '创建者ID',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_workspace (workspace_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SQL示例分组表'
            """))
            print("✅ 创建 sql_example_groups 表成功")
        except Exception as e:
            if 'already exists' in str(e):
                print("ℹ️ sql_example_groups 表已存在")
            else:
                print(f"⚠️ sql_example_groups: {e}")
        
        print("🎉 迁移完成！")


# ========== 模板分组迁移 ==========

async def migrate_templates():
    """模板分组迁移"""
    from app.core.db.database import get_async_db_manager
    from sqlalchemy import text
    
    db = get_async_db_manager()
    async with db.session_scope() as session:
        # 1. 创建模板分组表
        try:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS template_groups (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    name VARCHAR(100) NOT NULL COMMENT '分组名称',
                    description VARCHAR(500) COMMENT '分组描述',
                    color VARCHAR(20) DEFAULT '#10B981' COMMENT '分组颜色',
                    workspace_id VARCHAR(64) NOT NULL COMMENT '工作空间ID',
                    created_by VARCHAR(64) NOT NULL COMMENT '创建者ID',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_workspace (workspace_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模板分组表'
            """))
            print("✅ 创建 template_groups 表成功")
        except Exception as e:
            if 'already exists' in str(e):
                print("ℹ️ template_groups 表已存在")
            else:
                print(f"⚠️ template_groups: {e}")
        
        # 2. 为 templates 表添加 group_id 列
        try:
            await session.execute(text(
                "ALTER TABLE templates ADD COLUMN group_id INT COMMENT '所属分组ID'"
            ))
            print("✅ 添加 templates.group_id 列成功")
        except Exception as e:
            if 'Duplicate column' in str(e):
                print("ℹ️ templates.group_id 列已存在")
            else:
                print(f"⚠️ templates.group_id: {e}")

        # 3. 为 templates 表添加 bindings 列
        try:
            await session.execute(text(
                "ALTER TABLE templates ADD COLUMN bindings JSON NULL COMMENT '变量绑定信息'"
            ))
            print("✅ 添加 templates.bindings 列成功")
        except Exception as e:
            if 'Duplicate column' in str(e):
                print("ℹ️ templates.bindings 列已存在")
            else:
                print(f"⚠️ templates.bindings: {e}")
        
        print("🎉 模板分组迁移完成！")


if __name__ == "__main__":
    import sys
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "templates":
            loop.run_until_complete(migrate_templates())
        else:
            loop.run_until_complete(migrate())
    finally:
        loop.close()
