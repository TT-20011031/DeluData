"""
DeluData - 知识库模块

包含:
- 知识图谱 ORM 模型 (File, Folder, FileRelationship, DocumentImage)
- 已弃用的 DTO (向后兼容)
"""

# 知识图谱 ORM 模型
from app.models.knowledge.graph import (
    Folder,
    File,
    FileRelationship,
    DocumentImage,
)
from app.models.knowledge.ingestion_task import IngestionTask
from app.models.knowledge.tree_node import TreeNode

# 已弃用 (向后兼容) - 推荐使用 app.api.knowledge.schemas
from app.models.knowledge.legacy import KnowledgeBase

__all__ = [
    # 知识图谱
    "Folder",
    "File",
    "FileRelationship",
    "DocumentImage",
    "IngestionTask",
    "TreeNode",
    # 已弃用
    "KnowledgeBase",
]
