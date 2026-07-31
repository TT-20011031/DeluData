"""
LLM 智能检测后台任务执行器

职责：
- 在后台执行 LLM 检测任务
- 实时更新任务进度
- 处理错误和超时

设计原则遵循：
- Async First: 异步执行 LLM 调用
- Design Rigor: 与 service 解耦
"""
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any

from .task_manager import get_task_manager, DETECTION_STAGES
from .detector import get_blank_field_detector
from .converter import get_template_converter
from app.core.storage.service import get_storage_service

logger = logging.getLogger(__name__)


class DetectionTaskExecutor:
    """
    检测任务执行器
    
    后台执行 LLM 检测并更新进度
    """
    
    def __init__(self):
        self._manager = get_task_manager()
        self._detector = get_blank_field_detector()
        self._converter = get_template_converter()
    
    async def execute_detection(
        self, 
        task_id: str, 
        file_path: str
    ) -> None:
        """
        执行检测任务（后台异步执行）
        
        Args:
            task_id: 任务 ID
            file_path: 模板文件路径
        """
        try:
            storage_service = get_storage_service()
            suffix = Path(file_path).suffix or '.docx'
            async with storage_service.materialize(file_path, suffix=suffix) as local_file_path:
                # Stage 1: HTML 转换 (10%)
                self._manager.update_progress(task_id, "html_convert", "running")
                result = await self._converter.docx_to_html_with_mapping(local_file_path)
                raw_html = result["html"]
                raw_mapping = result["mapping"]
                
                # Stage 2: Dense HTML 清洗 (25%)
                self._manager.update_progress(task_id, "dense_html", "running")
                dense_html, updated_mapping = self._converter.to_dense_html(
                    raw_html, raw_mapping
                )
                
                # Stage 3: LLM 调用初始化 (40%)
                self._manager.update_progress(task_id, "llm_calling", "running")
                
                # Stage 4: LLM 处理中 (70%) - 这里会有实际的 LLM 调用
                self._manager.update_progress(task_id, "llm_processing", "running")
                candidates = await self._detector.detect_via_llm_async(
                    dense_html, updated_mapping
                )
            
            # Stage 5: 验证结果 (90%)
            self._manager.update_progress(task_id, "validating", "running")
            
            # 转换为可序列化的格式
            result_data = [
                {
                    "key": c.key,
                    "label": c.label,
                    "type": c.type,
                    "location": {
                        "type": c.location.type,
                        "paragraph_index": c.location.paragraph_index,
                        "table_index": c.location.table_index,
                        "row_index": c.location.row_index,
                        "cell_index": c.location.cell_index,
                        "selected_text": c.location.selected_text,
                        "mapping_id": c.location.mapping_id
                    },
                    "confidence": c.confidence,
                    "source": c.source,
                    "context": c.context
                }
                for c in candidates
            ]
            
            # 完成任务
            self._manager.complete_task(task_id, result_data)
            
        except Exception as e:
            logger.error(f"检测任务执行失败: {task_id}, 错误: {e}")
            self._manager.fail_task(task_id, str(e))
    
    def start_background_detection(
        self, 
        task_id: str, 
        file_path: str
    ) -> None:
        """
        在后台启动检测任务（不等待完成）
        
        Args:
            task_id: 任务 ID
            file_path: 模板文件路径
        """
        # 创建后台任务
        asyncio.create_task(self.execute_detection(task_id, file_path))
        logger.info(f"后台检测任务已启动: {task_id}")


# ========== 单例工厂 ==========

_executor = None


def get_task_executor() -> DetectionTaskExecutor:
    """获取任务执行器单例"""
    global _executor
    if _executor is None:
        _executor = DetectionTaskExecutor()
    return _executor
