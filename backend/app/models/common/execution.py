"""
执行结果数据模型

定义 Skill 返回的结构化执行结果
从原 app/agents/state.py 迁移
"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import pandas as pd


@dataclass
class ExecutionResult:
    """
    代码执行结果
    
    用于 PythonSkill 返回执行结果
    """
    success: bool = True
    output: Any = None
    error: Optional[str] = None
    dataframes: Dict[str, Any] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)


@dataclass
class QueryResult:
    """
    查询结果
    
    用于 SqlSkill 返回查询结果
    """
    success: bool = True
    data: Optional[pd.DataFrame] = None
    sql: str = ""
    error: Optional[str] = None
    row_count: int = 0
    execution_time_ms: float = 0.0


@dataclass
class DocumentChunk:
    """
    文档切片
    
    用于 DocSkill 返回检索结果，支持：
    - 父子索引结构（上下文扩展）
    - 语义增强（自动摘要、标题路径）
    - 重排序分数
    """
    # ============ 基础字段 ============
    content: str
    source_file: str
    page_number: Optional[int] = None
    chunk_id: str = ""
    score: float = 0.0  # 向量相似度分数
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # ============ 父子索引结构 ============
    parent_id: Optional[str] = None  # 父文档/章节 ID
    prev_id: Optional[str] = None  # 前一个 Chunk ID（链表）
    next_id: Optional[str] = None  # 后一个 Chunk ID（链表）
    
    # ============ 语义增强 ============
    summary: Optional[str] = None  # 自动生成的摘要
    header_path: Optional[str] = None  # 标题路径，如 "第一章 > 1.1 概述"
    
    # ============ 重排序 ============
    rerank_score: Optional[float] = None  # Reranker 打分（0-1）
