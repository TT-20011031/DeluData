"""
初始化知识图谱数据库表
"""
import sys
import os
import asyncio
import logging

# 将项目根目录添加到 python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db.database import get_db_manager, Base
# 必须导入模型以注册到 Base
from app.models.sql_graph import Folder, File, FileRelationship

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    logger.info("开始初始化知识图谱数据库表...")
    try:
        db_manager = get_db_manager()
        engine = db_manager.engine
        
        # 创建表
        Base.metadata.create_all(bind=engine)
        logger.info("数据库表创建成功！(folders, files, file_relationships)")
        
        # 验证
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        logger.info(f"当前数据库表: {tables}")
        
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        raise

if __name__ == "__main__":
    init_db()
