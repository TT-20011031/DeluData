"""
DeluData 智能问数系统 - 健康检查接口

提供系统健康状态监控
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db.database import get_db, get_db_manager
from app.config import get_settings


router = APIRouter()


@router.get("/health")
async def health_check():
    """
    基础健康检查
    
    Returns:
        健康状态信息
    """
    settings = get_settings()
    
    return {
        "status": "healthy",
        "version": "1.0.0",
        "environment": settings.app.env
    }


@router.get("/health/db")
async def database_health():
    """
    数据库连接健康检查
    
    Returns:
        数据库连接状态
    """
    db_manager = get_db_manager()
    is_connected = db_manager.test_connection()
    
    settings = get_settings()
    
    return {
        "status": "healthy" if is_connected else "unhealthy",
        "database": {
            "host": settings.db.host,
            "port": settings.db.port,
            "name": settings.db.name,
            "connected": is_connected
        }
    }


@router.get("/health/full")
async def full_health_check():
    """
    完整健康检查
    
    检查所有依赖服务的状态
    
    Returns:
        完整的健康状态报告
    """
    settings = get_settings()
    db_manager = get_db_manager()
    
    # 检查数据库
    db_healthy = db_manager.test_connection()
    
    # 检查 LLM 配置
    llm_configured = bool(settings.llm.dashscope_api_key)
    
    # 检查向量库目录
    import os
    chroma_dir_exists = os.path.exists(settings.chroma.persist_dir)
    
    all_healthy = db_healthy and llm_configured
    
    return {
        "status": "healthy" if all_healthy else "degraded",
        "checks": {
            "database": {
                "status": "pass" if db_healthy else "fail",
                "host": settings.db.host,
                "database": settings.db.name
            },
            "llm": {
                "status": "pass" if llm_configured else "warn",
                "model": settings.llm.model,
                "configured": llm_configured
            },
            "vector_store": {
                "status": "pass" if chroma_dir_exists else "warn",
                "path": settings.chroma.persist_dir,
                "exists": chroma_dir_exists
            }
        },
        "environment": settings.app.env,
        "debug": settings.app.debug
    }
