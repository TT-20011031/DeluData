"""
知识库 API - Pydantic 模型

请求/响应的数据验证
包含 DocumentStatus 枚举，统一状态字符串定义
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# 从 models 层导入枚举并重新导出
from app.models.common.enums import DocumentStatus


# ========== 文件夹 ==========

class FolderCreate(BaseModel):
    name: str
    parent_id: Optional[str] = None
    visibility: str = 'public'
    dept_id: Optional[int] = None


class FolderResponse(BaseModel):
    """文件夹响应"""
    id: str
    name: str
    parent_id: Optional[str] = None
    visibility: str = 'public'
    
    class Config:
        from_attributes = True


# ========== 关系 ==========

class RelationCreate(BaseModel):
    source_id: str
    target_id: str
    relation_type: str


# ========== 文件请求 ==========

class DescriptionUpdate(BaseModel):
    description: str


class UploadResponse(BaseModel):
    document_id: str
    name: str
    status: str
    message: str
    task_id: Optional[str] = None


class FileEditRequest(BaseModel):
    content: str


class FileMetadataUpdate(BaseModel):
    document_type: Optional[str] = None
    business_domain: Optional[str] = None
    department_id: Optional[int] = None
    confidentiality_level: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    owner_id: Optional[str] = None
    external_ref: Optional[str] = None
    visibility: Optional[str] = None


# ========== 文件响应 ==========

class FileResponse(BaseModel):
    """文件详情响应"""
    id: str
    name: str
    description: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    status: DocumentStatus = DocumentStatus.PROCESSING
    is_deleted: bool = False
    delete_status: str = "active"
    delete_error: Optional[str] = None
    delete_op_id: Optional[str] = None
    chunk_count: int = 0
    visibility: str = "dept"
    dept_id: Optional[int] = None
    owner_id: Optional[str] = None
    document_type: Optional[str] = None
    business_domain: Optional[str] = None
    confidentiality_level: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    external_ref: Optional[str] = None
    pageindex_status: Optional[str] = None
    active_task_id: Optional[str] = None
    processing_stage: Optional[str] = None
    processing_progress: Optional[int] = None
    preview_policy: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class FileAccessUrlResponse(BaseModel):
    """文件访问地址响应"""
    url: str
    requires_auth: bool = True
    kind: str
    expires_at: Optional[datetime] = None


class FileListItem(BaseModel):
    """文件列表项（轻量）"""
    id: str
    name: str
    file_type: Optional[str] = None
    status: DocumentStatus = DocumentStatus.PROCESSING
    is_deleted: bool = False
    delete_status: str = "active"
    delete_error: Optional[str] = None
    delete_op_id: Optional[str] = None
    description: Optional[str] = None
    pageindex_status: Optional[str] = None
    visibility: str = "dept"
    dept_id: Optional[int] = None
    document_type: Optional[str] = None
    business_domain: Optional[str] = None
    confidentiality_level: Optional[str] = None
    
    class Config:
        from_attributes = True


class IngestionTaskResponse(BaseModel):
    task_id: str
    file_id: str
    status: str
    stage: str
    progress: int
    detail: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


# ========== 批量操作 ==========

class BatchDeleteRequest(BaseModel):
    """批量删除请求"""
    ids: List[str]


class BatchDeleteResponse(BaseModel):
    """批量删除响应"""
    success: bool
    deleted_count: int
    failed_count: int = 0
    requested_count: int


class RenameRequest(BaseModel):
    name: str


class MoveRequest(BaseModel):
    parent_id: Optional[str]


class UpdatePositionRequest(BaseModel):
    x: float
    y: float


# ========== 溯源 ==========

class DocumentOrigin(BaseModel):
    """文档溯源信息"""
    document_id: str
    name: str
    owner_id: str
    owner_name: str
    department_name: Optional[str] = None
    visibility: str
    created_at: str
    updated_at: Optional[str] = None
    # [新增] 切片统计
    chunk_count: int = 0
    chunks_with_file_id: int = 0
    file_id_coverage: str = "0/0"
