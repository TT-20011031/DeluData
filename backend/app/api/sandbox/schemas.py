"""
临时产物 API — Pydantic Schemas

[Schema Validation] 全部数据通过 Pydantic 层校验，禁止直接传递裸 dict。
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class TempArtifactOut(BaseModel):
    """单个产物输出"""
    id: str = Field(description="产物唯一 ID")
    type: Literal["chart", "doc"] = Field(description="产物类型")
    file_kind: Optional[Literal["word", "excel"]] = Field(default=None, description="文档子类型")
    title: str = Field(description="产物标题")
    session_id: str = Field(description="来源会话 ID")
    workspace_id: str
    user_id: str
    created_at: str = Field(description="创建时间 ISO8601")
    expires_at: str = Field(description="过期时间 ISO8601")
    download_url: Optional[str] = Field(default=None, description="文档下载 URL（doc 类型）")
    file_name: Optional[str] = Field(default=None, description="文件名（doc 类型）")


class ArtifactListResponse(BaseModel):
    """产物列表响应"""
    items: list[TempArtifactOut]
    total: int
