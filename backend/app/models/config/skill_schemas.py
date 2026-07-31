"""
Skill Pydantic Schemas

用于 API 请求/响应的数据校验

设计原则遵循：
- Schema Validation: 所有数据交互必须经由 Pydantic 校验
- Design Rigor: 分离请求/响应模型，职责明确
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class SkillToolType(str, Enum):
    """
    允许的工具类型枚举
    
    与 Planner Worker 枚举保持同步（参考 prompts/supervisor.yaml）
    仅包含用户可显式指定的 Worker，不包含内部工具（如 inspect_file, finish）
    """
    SQL_WORKER = "sql_worker"
    DOC_WORKER = "doc_worker"
    CHART_WORKER = "chart_worker"
    OFFICE_WORKER = "office_worker"


class SkillVisibilityType(str, Enum):
    """Skill 可见性类型"""
    GLOBAL = "global"
    WORKSPACE = "workspace"


# ========== 步骤 Schema ==========

class SkillStepSchema(BaseModel):
    """单个步骤定义"""
    step: int = Field(..., ge=1, description="步骤序号，从1开始")
    action: str = Field(..., min_length=1, max_length=500, description="操作描述")
    tool: Optional[SkillToolType] = Field(None, description="使用的工具类型")
    template: Optional[str] = Field(None, max_length=2000, description="SQL/查询模板")
    template_id: Optional[int] = Field(None, description="文档模板ID")
    template_version: Optional[str] = Field(None, description="模板版本")
    template_mode: Optional[str] = Field(None, description="模板模式")
    output_filename: Optional[str] = Field(None, description="输出文件名")
    doc_scope: Optional[Dict[str, Any]] = Field(None, description="文档检索范围")
    keywords: Optional[List[str]] = Field(
        default_factory=list, 
        description="RAG 检索关键词"
    )
    
    class Config:
        use_enum_values = True


# ========== 请求 Schema ==========

class SkillCreateRequest(BaseModel):
    """创建 Skill 请求"""
    title: str = Field(..., min_length=1, max_length=255, description="标题")
    description: str = Field(..., min_length=1, max_length=2000, description="描述")
    steps: List[SkillStepSchema] = Field(..., min_length=1, description="步骤列表")
    tags: Optional[List[str]] = Field(default_factory=list, description="标签")
    example_queries: Optional[List[str]] = Field(
        default_factory=list, 
        description="示例问题，用于提高检索召回率"
    )
    visibility: SkillVisibilityType = Field(
        default=SkillVisibilityType.WORKSPACE,
        description="可见性"
    )
    
    @field_validator('tags', 'example_queries', mode='before')
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v


class SkillUpdateRequest(BaseModel):
    """更新 Skill 请求（部分更新）"""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, min_length=1, max_length=2000)
    steps: Optional[List[SkillStepSchema]] = Field(None, min_length=1)
    tags: Optional[List[str]] = None
    example_queries: Optional[List[str]] = None
    visibility: Optional[SkillVisibilityType] = None


# ========== 响应 Schema ==========

class SkillResponse(BaseModel):
    """Skill 完整响应"""
    id: str
    title: str
    description: str
    steps: List[SkillStepSchema]
    tags: List[str]
    example_queries: List[str]
    visibility: str
    usage_count: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class SkillSummaryResponse(BaseModel):
    """Skill 摘要响应（列表用）"""
    id: str
    title: str
    description: str
    tags: List[str]
    usage_count: int
    visibility: str
    
    class Config:
        from_attributes = True


class SkillSearchResult(BaseModel):
    """检索结果"""
    skill: SkillResponse
    score: float = Field(..., ge=0, le=1, description="相似度分数 (0-1)")


# ========== LLM 生成 Schema ==========

class SkillGenerateRequest(BaseModel):
    """LLM 生成 Skill 请求"""
    user_input: str = Field(
        ..., 
        min_length=1, 
        max_length=1000, 
        description="用户自然语言描述"
    )


class SkillGenerateResponse(BaseModel):
    """LLM 生成 Skill 响应"""
    title: str
    description: str
    steps: List[SkillStepSchema]
    tags: List[str]
    example_queries: List[str] = Field(default_factory=list)


# ========== Dry Run Schema ==========

class DryRunRequest(BaseModel):
    """Dry Run 测试请求"""
    skill_id: str = Field(..., description="要测试的 Skill ID")
    test_query: str = Field(..., min_length=1, max_length=500, description="测试问题")


class DryRunStepPreview(BaseModel):
    """Dry Run 预测步骤预览"""
    step_id: str
    instruction: str
    worker: str
    params: dict = Field(default_factory=dict)


class DryRunResponse(BaseModel):
    """Dry Run 测试响应"""
    skill_used: str = Field(..., description="使用的 Skill 标题")
    test_query: str
    predicted_steps: List[DryRunStepPreview]
    thinking: str = Field(default="", description="Planner 思考过程")
