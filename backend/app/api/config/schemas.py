"""
扩展配置 API - Pydantic 模型

SQL 示例和模板管理的请求/响应模型
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel


# ========== SQL 示例相关 ==========

class SqlExampleResponse(BaseModel):
    """SQL 示例响应"""
    id: int
    question: str
    sql: str
    description: str | None
    tables: str | None
    is_active: bool
    group_id: int | None = None
    created_at: str
    updated_at: str


class SqlExampleListResponse(BaseModel):
    """SQL 示例列表响应"""
    examples: List[SqlExampleResponse]
    total: int


class SqlGroupResponse(BaseModel):
    """SQL 示例分组响应"""
    id: int
    name: str
    description: str | None
    color: str
    example_count: int
    created_at: str
    updated_at: str


class SqlGroupListResponse(BaseModel):
    """SQL 示例分组列表响应"""
    groups: List[SqlGroupResponse]
    total: int


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""
    ids: List[int]


class BatchMoveRequest(BaseModel):
    """批量移动分组请求"""
    ids: List[int]
    group_id: Optional[int] = None


class BatchOperationResponse(BaseModel):
    """批量操作响应"""
    success: bool
    affected_count: int
    message: str


# ========== 模板相关 ==========

class TemplateResponse(BaseModel):
    """模板响应"""
    id: int
    name: str
    description: str | None
    file_path: str
    file_type: str
    keywords: str | None
    variables_schema: Dict[str, Any] | None
    example_context: Dict[str, Any] | None
    bindings: List[Dict[str, Any]] | None = None
    is_active: bool
    group_id: int | None = None
    created_at: str
    updated_at: str


class TemplateListResponse(BaseModel):
    """模板列表响应"""
    templates: List[TemplateResponse]
    total: int


class TemplateMatchResponse(BaseModel):
    """模板匹配响应"""
    matched: bool
    templates: List[TemplateResponse]


class AnalyzeResponse(BaseModel):
    """变量分析响应"""
    variables: List[str]
    suggested_schema: Dict[str, Any]


class TemplatePreviewResponse(BaseModel):
    """模板预览响应"""
    html: str
    mapping: List[Dict[str, Any]]


class TemplateCompileRequest(BaseModel):
    """模板编译请求"""
    bindings: List[Dict[str, Any]]
    output_name: Optional[str] = None


class TemplateCompileResponse(BaseModel):
    """模板编译响应"""
    compiled_path: str
    compiled_name: str


class TemplateGroupResponse(BaseModel):
    """模板分组响应"""
    id: int
    name: str
    description: str | None
    color: str
    example_count: int
    created_at: str
    updated_at: str


# ========== 空白字段检测相关 ==========

class CandidateFieldLocation(BaseModel):
    """候选字段位置"""
    type: str  # "paragraph" | "table_cell"
    paragraph_index: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    cell_index: int | None = None
    selected_text: str = ""
    mapping_id: str | None = None  # 与 HTML data-id 一致的标识符，用于前端联动高亮


class CandidateFieldResponse(BaseModel):
    """候选字段响应"""
    key: str
    label: str
    type: str
    location: CandidateFieldLocation
    confidence: float
    source: str
    context: str


class BlankFieldDetectResponse(BaseModel):
    """空白字段检测响应"""
    candidates: List[CandidateFieldResponse]
    total: int
    high_confidence_count: int  # confidence >= 0.8 的数量


class BlankFieldConfirmRequest(BaseModel):
    """空白字段确认并编译请求"""
    candidates: List[CandidateFieldResponse]
    output_name: Optional[str] = None
    update_schema: bool = True

