"""
数据库迁移脚本 - 创建用户级智能体配置表
"""
import asyncio
import sys
sys.path.insert(0, '.')

from sqlalchemy import text
from app.core.db.database import get_async_db_manager


async def migrate():
    """创建 user_agent_configs 表"""
    db = get_async_db_manager()
    async with db.session_scope() as session:
        try:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS user_agent_configs (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    user_id VARCHAR(64) NOT NULL COMMENT '用户 ID',
                    workspace_id VARCHAR(64) NOT NULL COMMENT '工作区 ID',
                    
                    max_retries INT DEFAULT NULL COMMENT '最大重试次数 (NULL=继承)',
                    always_confirm TINYINT DEFAULT NULL COMMENT '始终确认计划 (0/1/NULL)',
                    execution_mode VARCHAR(20) DEFAULT NULL COMMENT '执行模式',
                    synthesizer_template VARCHAR(64) DEFAULT NULL COMMENT '回复风格模板 ID',
                    synthesizer_custom_prompt TEXT DEFAULT NULL COMMENT '用户微调 Prompt',
                    
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    
                    UNIQUE KEY uk_user_workspace (user_id, workspace_id),
                    INDEX idx_user_id (user_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci 
                COMMENT='用户级智能体配置表'
            """))
            print("✅ 创建 user_agent_configs 表成功")
        except Exception as e:
            if 'already exists' in str(e):
                print("ℹ️ user_agent_configs 表已存在")
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
