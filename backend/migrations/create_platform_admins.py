"""
平台管理员表迁移脚本

创建 sys_platform_admins 表并插入默认超管账号

运行方式:
    cd backend
    python -m migrations.create_platform_admins
"""
import os
import sys
import asyncio
import uuid
import logging
import platform

# [修复] Windows 上 aiomysql 的事件循环问题
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from passlib.context import CryptContext

from app.core.db.database import get_async_db_manager
from app.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 密码哈希
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def create_table(session):
    """创建 sys_platform_admins 表"""
    create_sql = """
    CREATE TABLE IF NOT EXISTS sys_platform_admins (
        id VARCHAR(36) PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        email VARCHAR(100),
        password_hash VARCHAR(255) NOT NULL,
        is_active BOOLEAN DEFAULT TRUE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        last_login DATETIME,
        login_attempts INT DEFAULT 0,
        last_login_ip VARCHAR(45),
        INDEX idx_username (username),
        INDEX idx_is_active (is_active)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    COMMENT='平台管理员表 (独立于租户用户)';
    """
    await session.execute(text(create_sql))
    await session.commit()
    logger.info("✅ 创建 sys_platform_admins 表")


async def insert_default_admin(session):
    """插入默认超管账号"""
    # [No Hardcoding] 从环境变量读取默认密码
    default_password = os.getenv("PLATFORM_ADMIN_PASSWORD", "admin123")
    default_username = os.getenv("PLATFORM_ADMIN_USERNAME", "admin")
    default_email = os.getenv("PLATFORM_ADMIN_EMAIL", "admin@deludata.com")
    
    # 检查是否已存在
    check_sql = text("SELECT id FROM sys_platform_admins WHERE username = :username")
    result = await session.execute(check_sql, {"username": default_username})
    if result.scalar_one_or_none():
        logger.info(f"⏭️  超管账号 '{default_username}' 已存在，跳过")
        return
    
    # 创建账号
    admin_id = str(uuid.uuid4())
    password_hash = pwd_context.hash(default_password)
    
    insert_sql = text("""
        INSERT INTO sys_platform_admins (id, username, email, password_hash, is_active)
        VALUES (:id, :username, :email, :password_hash, TRUE)
    """)
    
    await session.execute(insert_sql, {
        "id": admin_id,
        "username": default_username,
        "email": default_email,
        "password_hash": password_hash
    })
    await session.commit()
    
    logger.info(f"✅ 创建默认超管账号: {default_username}")
    logger.info(f"   邮箱: {default_email}")
    logger.info(f"   密码: {'*' * len(default_password)} (请及时修改)")
    
    if default_password == "admin123":
        logger.warning("⚠️  正在使用默认密码！请设置环境变量 PLATFORM_ADMIN_PASSWORD")


async def main():
    """执行迁移"""
    print("=" * 60)
    print("🔧 平台管理员表迁移")
    print("=" * 60)
    
    db_manager = get_async_db_manager()
    
    try:
        async with db_manager.session_scope() as session:
            await create_table(session)
            await insert_default_admin(session)
    finally:
        # [修复] 显式关闭连接池，避免 Event loop is closed 警告
        await db_manager.dispose()
    
    print("=" * 60)
    print("✅ 迁移完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
