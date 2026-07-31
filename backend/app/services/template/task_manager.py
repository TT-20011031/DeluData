"""
LLM 智能检测任务管理器

职责：
- 管理异步 LLM 检测任务的生命周期
- 使用 Redis 存储任务状态（跨进程共享）
- 支持进度追踪和结果缓存

设计原则遵循：
- Async First: 异步 Redis 操作
- No Hardcoding: Redis 配置从 config 读取
- Schema Validation: Pydantic 模型校验
"""
import uuid
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)


# ========== Pydantic 模型 ==========

class DetectionTaskStage(BaseModel):
    """任务阶段"""
    name: str
    progress: int  # 该阶段完成时的进度百分比
    description: str


class DetectionTask(BaseModel):
    """检测任务"""
    task_id: str
    template_id: int
    workspace_id: str
    user_id: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    progress: int = 0
    stage: str = "初始化"
    result: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ========== 进度阶段定义 ==========

DETECTION_STAGES = [
    DetectionTaskStage(name="html_convert", progress=10, description="正在转换 HTML..."),
    DetectionTaskStage(name="dense_html", progress=25, description="正在清洗 HTML..."),
    DetectionTaskStage(name="llm_calling", progress=40, description="正在调用 LLM 分析..."),
    DetectionTaskStage(name="llm_processing", progress=70, description="LLM 处理中..."),
    DetectionTaskStage(name="validating", progress=90, description="正在验证结果..."),
    DetectionTaskStage(name="completed", progress=100, description="检测完成"),
]


# ========== 任务管理器实现 ==========

class DetectionTaskManager:
    """
    检测任务管理器
    
    使用 Redis 存储任务状态，支持：
    - 任务创建、更新、查询
    - 进度追踪
    - 自动过期（1小时）
    """
    
    TASK_PREFIX = "detection_task:"
    EXPIRATION_SECONDS = 3600  # 1小时
    
    def __init__(self):
        self._redis = None
    
    @property
    def redis(self):
        """懒加载 Redis 客户端"""
        if self._redis is None:
            import redis
            settings = get_settings().redis
            self._redis = redis.Redis(
                host=settings.host,
                port=settings.port,
                db=settings.db,
                password=settings.password or None,
                decode_responses=True
            )
        return self._redis
    
    def create_task(
        self, 
        template_id: int, 
        workspace_id: str, 
        user_id: str
    ) -> DetectionTask:
        """
        创建新的检测任务
        
        Args:
            template_id: 模板 ID
            workspace_id: 工作空间 ID
            user_id: 用户 ID
            
        Returns:
            新创建的任务
        """
        now = datetime.utcnow()
        task = DetectionTask(
            task_id=str(uuid.uuid4()),
            template_id=template_id,
            workspace_id=workspace_id,
            user_id=user_id,
            status="pending",
            progress=0,
            stage="等待处理",
            created_at=now,
            updated_at=now
        )
        
        self._save_task(task)
        logger.info(f"创建检测任务: {task.task_id}")
        return task
    
    def get_task(self, task_id: str) -> Optional[DetectionTask]:
        """
        获取任务状态
        
        Args:
            task_id: 任务 ID
            
        Returns:
            任务对象或 None
        """
        key = f"{self.TASK_PREFIX}{task_id}"
        data = self.redis.get(key)
        
        if not data:
            return None
        
        try:
            return DetectionTask.model_validate_json(data)
        except Exception as e:
            logger.error(f"解析任务数据失败: {e}")
            return None
    
    def update_progress(
        self, 
        task_id: str, 
        stage_name: str, 
        status: str = "running"
    ) -> None:
        """
        更新任务进度
        
        Args:
            task_id: 任务 ID
            stage_name: 阶段名称
            status: 任务状态
        """
        task = self.get_task(task_id)
        if not task:
            logger.warning(f"任务不存在: {task_id}")
            return
        
        # 查找阶段进度
        stage_info = next(
            (s for s in DETECTION_STAGES if s.name == stage_name), 
            None
        )
        
        if stage_info:
            task.progress = stage_info.progress
            task.stage = stage_info.description
        
        task.status = status
        task.updated_at = datetime.utcnow()
        
        self._save_task(task)
        logger.debug(f"任务进度更新: {task_id} -> {stage_name} ({task.progress}%)")
    
    def complete_task(
        self, 
        task_id: str, 
        result: List[Dict[str, Any]]
    ) -> None:
        """
        标记任务完成
        
        Args:
            task_id: 任务 ID
            result: 检测结果（候选字段列表）
        """
        task = self.get_task(task_id)
        if not task:
            return
        
        task.status = "completed"
        task.progress = 100
        task.stage = "检测完成"
        task.result = result
        task.updated_at = datetime.utcnow()
        
        self._save_task(task)
        logger.info(f"任务完成: {task_id}, 识别到 {len(result)} 个字段")
    
    def fail_task(self, task_id: str, error: str) -> None:
        """
        标记任务失败
        
        Args:
            task_id: 任务 ID
            error: 错误信息
        """
        task = self.get_task(task_id)
        if not task:
            return
        
        task.status = "failed"
        task.error = error
        task.updated_at = datetime.utcnow()
        
        self._save_task(task)
        logger.error(f"任务失败: {task_id}, 错误: {error}")
    
    def _save_task(self, task: DetectionTask) -> None:
        """保存任务到 Redis"""
        key = f"{self.TASK_PREFIX}{task.task_id}"
        self.redis.setex(
            key,
            self.EXPIRATION_SECONDS,
            task.model_dump_json()
        )


# ========== 单例工厂 ==========

_manager: Optional[DetectionTaskManager] = None


def get_task_manager() -> DetectionTaskManager:
    """获取任务管理器单例"""
    global _manager
    if _manager is None:
        _manager = DetectionTaskManager()
    return _manager
