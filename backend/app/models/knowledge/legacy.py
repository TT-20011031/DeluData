"""
知识库模型 (DTO)

⚠️ 已弃用 - 推荐使用 app.api.knowledge.schemas

此文件仅保留用于向后兼容，新代码请使用：
- app.api.knowledge.schemas.DocumentStatus
- app.api.knowledge.schemas.FileResponse
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

# 从底层 enums 直接导入，避免触发 app.api.knowledge 包 __init__ 的循环 import
# （app.api.knowledge.schemas.DocumentStatus 本身就是从 app.models.common.enums 重新导出）
from app.models.common.enums import DocumentStatus


class KnowledgeBase(BaseModel):
    """
    知识库文档 DTO
    
    ⚠️ 已弃用 - 推荐使用 app.api.knowledge.schemas.FileResponse
    """
    id: str
    name: str
    description: str = ""
    file_path: str
    file_type: str
    file_size: int
    
    status: DocumentStatus = DocumentStatus.PROCESSING
    error_message: Optional[str] = None
    
    chunk_count: int = 0
    token_count: int = 0
    
    user_id: str
    workspace_id: str
    
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    processed_at: Optional[datetime] = None
