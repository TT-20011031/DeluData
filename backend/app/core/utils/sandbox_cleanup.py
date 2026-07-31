"""
沙盒清理服务

定时清理过期的会话沙盒目录，防止磁盘被撑爆

使用 APScheduler 实现定时任务
"""
import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

# 全局调度器实例
_scheduler = None


async def cleanup_expired_sandboxes():
    """
    清理过期的沙盒目录
    
    扫描沙盒基目录，删除超过 TTL 的会话目录
    """
    settings = get_settings()
    sandbox_base = Path(settings.sandbox.base_dir)
    ttl_hours = settings.sandbox.session_ttl_hours
    
    if not sandbox_base.exists():
        logger.debug("沙盒基目录不存在，跳过清理")
        return
    
    now = datetime.now()
    cutoff_time = now - timedelta(hours=ttl_hours)
    cleaned_count = 0
    total_size = 0
    
    logger.info(f"开始清理过期沙盒，TTL={ttl_hours}小时，截止时间={cutoff_time}")
    
    try:
        for item in sandbox_base.iterdir():
            if not item.is_dir():
                continue
            
            # 只处理 session_ 开头的目录
            if not item.name.startswith("session_"):
                continue
            
            try:
                # 检查目录的最后修改时间
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                
                if mtime < cutoff_time:
                    # 计算目录大小
                    dir_size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                    total_size += dir_size
                    
                    # 删除目录
                    shutil.rmtree(item)
                    cleaned_count += 1
                    logger.info(f"已清理过期沙盒: {item.name} (最后修改: {mtime})")
                    
            except PermissionError:
                logger.warning(f"无权限删除目录: {item}")
            except Exception as e:
                logger.warning(f"清理目录失败 {item}: {e}")
        
        if cleaned_count > 0:
            size_mb = total_size / (1024 * 1024)
            logger.info(f"沙盒清理完成: 删除 {cleaned_count} 个目录，释放 {size_mb:.1f}MB 空间")
        else:
            logger.debug("没有过期的沙盒需要清理")
            
    except Exception as e:
        logger.error(f"沙盒清理任务失败: {e}", exc_info=True)


def start_sandbox_cleanup_service():
    """
    启动沙盒清理定时服务
    
    使用 APScheduler 每小时执行一次清理任务
    """
    global _scheduler
    
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        
        settings = get_settings()
        
        _scheduler = AsyncIOScheduler()
        
        # 添加定时任务：每小时执行一次
        _scheduler.add_job(
            cleanup_expired_sandboxes,
            trigger=IntervalTrigger(hours=1),
            id="sandbox_cleanup",
            name="沙盒清理",
            replace_existing=True
        )
        
        _scheduler.start()
        logger.info(f"沙盒清理服务已启动，每小时检查一次，TTL={settings.sandbox.session_ttl_hours}小时")
        
        # 启动时立即执行一次清理
        asyncio.create_task(cleanup_expired_sandboxes())
        
    except ImportError:
        logger.warning("APScheduler 未安装，沙盒清理服务未启动。请运行: pip install apscheduler")
    except Exception as e:
        logger.error(f"启动沙盒清理服务失败: {e}")


def stop_sandbox_cleanup_service():
    """停止沙盒清理服务"""
    global _scheduler
    
    if _scheduler:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("沙盒清理服务已停止")
        except Exception as e:
            logger.warning(f"停止沙盒清理服务失败: {e}")
        finally:
            _scheduler = None
