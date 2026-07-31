"""
Worker 分类定义

逻辑门控执行（Gated Execution）的核心配置

分类说明：
- EXTRACTION: 提取类，用于读取/查询数据（sql_worker, doc_worker）
- TERMINAL: 终结类，用于生成文件/图表（chart_worker, office_worker）
- UTILITY: 工具类，辅助功能（finish）
"""
from enum import Enum
from typing import Dict


class WorkerCategory(str, Enum):
    """Worker 分类枚举"""
    EXTRACTION = "extraction"   # 提取类：用于读取/查询数据
    TERMINAL = "terminal"       # 终结类：用于生成文件/图表
    UTILITY = "utility"         # 工具类：辅助功能


# Worker → Category 映射配置
WORKER_CATEGORY_MAP: Dict[str, WorkerCategory] = {
    # 提取类 (Extraction) - 用于数据获取
    "sql_worker": WorkerCategory.EXTRACTION,
    "doc_worker": WorkerCategory.EXTRACTION,
    "inspect_file": WorkerCategory.EXTRACTION,
    
    # 终结类 (Terminal) - 用于产出生成
    "chart_worker": WorkerCategory.TERMINAL,
    "office_worker": WorkerCategory.TERMINAL,
    
    # 工具类 (Utility) - 辅助功能
    "finish": WorkerCategory.UTILITY,
}


def get_worker_category(worker: str) -> WorkerCategory:
    """
    获取 Worker 的分类
    
    Args:
        worker: Worker 类型名称
        
    Returns:
        WorkerCategory: 该 Worker 的分类，未知类型默认为 EXTRACTION
    """
    return WORKER_CATEGORY_MAP.get(worker, WorkerCategory.EXTRACTION)


def is_extraction_worker(worker: str) -> bool:
    """判断是否为提取类 Worker"""
    return get_worker_category(worker) == WorkerCategory.EXTRACTION


def is_terminal_worker(worker: str) -> bool:
    """判断是否为终结类 Worker"""
    return get_worker_category(worker) == WorkerCategory.TERMINAL


def is_utility_worker(worker: str) -> bool:
    """判断是否为工具类 Worker"""
    return get_worker_category(worker) == WorkerCategory.UTILITY
