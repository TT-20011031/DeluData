"""
数据库迁移脚本 - 创建系统回复风格模板表
"""
import asyncio
import sys
sys.path.insert(0, '.')

from sqlalchemy import text
from app.core.db.database import get_async_db_manager


async def migrate():
    """创建 system_templates 表"""
    db = get_async_db_manager()
    async with db.session_scope() as session:
        try:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS system_templates (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    template_id VARCHAR(64) NOT NULL COMMENT '模板唯一标识',
                    workspace_id VARCHAR(64) NOT NULL COMMENT '工作区 ID',
                    name VARCHAR(100) NOT NULL COMMENT '模板名称',
                    description VARCHAR(500) DEFAULT NULL COMMENT '模板描述',
                    prompt TEXT NOT NULL COMMENT '模板 Prompt 内容',
                    is_default TINYINT DEFAULT 0 COMMENT '是否为默认模板',
                    sort_order INT DEFAULT 0 COMMENT '排序权重',
                    created_by VARCHAR(64) DEFAULT NULL COMMENT '创建者 ID',
                    
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    
                    UNIQUE KEY uq_system_templates_workspace_template (workspace_id, template_id),
                    INDEX idx_workspace (workspace_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci 
                COMMENT='系统回复风格模板表'
            """))
            print("✅ 创建 system_templates 表成功")
        except Exception as e:
            if 'already exists' in str(e):
                print("ℹ️ system_templates 表已存在")
            else:
                print(f"⚠️ 创建失败: {e}")
        
        print("🎉 迁移完成！")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(migrate())
    finally:
        loop.close()
