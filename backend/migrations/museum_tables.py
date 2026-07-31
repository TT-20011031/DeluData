"""
博物馆模块 - 数据库迁移脚本

创建 museum_products 和 museum_guide_sessions 表
"""
import asyncio
import logging
from sqlalchemy import text

from app.core.db.async_engine import async_engine

logger = logging.getLogger(__name__)

# 创建商品表
CREATE_PRODUCTS_TABLE = """
CREATE TABLE IF NOT EXISTS museum_products (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL COMMENT '商品名称',
    description TEXT COMMENT '商品描述',
    price DECIMAL(10, 2) NOT NULL COMMENT '商品价格',
    category ENUM('文创', '纪念品', '仿制品', '书籍', '其他') DEFAULT '其他' COMMENT '商品分类',
    related_exhibit_ids JSON COMMENT '关联展品ID列表',
    image_urls JSON COMMENT '商品图片URL列表',
    stock INT DEFAULT 0 COMMENT '库存数量',
    status ENUM('active', 'inactive') DEFAULT 'active' COMMENT '商品状态',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    
    INDEX idx_category (category),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='博物馆商品表';
"""

# 创建导览会话表
CREATE_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS museum_guide_sessions (
    id VARCHAR(36) PRIMARY KEY,
    visitor_uuid VARCHAR(36) NOT NULL COMMENT '访客 UUID',
    person_type VARCHAR(50) COMMENT '识别出的人物类型',
    person_features JSON COMMENT '人物特征列表',
    style_config JSON COMMENT '缓存的风格配置',
    visitor_image_path VARCHAR(500) COMMENT '访客图片路径',
    conversation_history JSON COMMENT '对话历史',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    
    INDEX idx_visitor (visitor_uuid),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='博物馆导览会话表';
"""


async def run_migration():
    """执行迁移"""
    async with async_engine.begin() as conn:
        # 创建商品表
        logger.info("创建 museum_products 表...")
        await conn.execute(text(CREATE_PRODUCTS_TABLE))
        
        # 创建会话表
        logger.info("创建 museum_guide_sessions 表...")
        await conn.execute(text(CREATE_SESSIONS_TABLE))
        
        logger.info("博物馆模块数据库迁移完成!")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_migration())
