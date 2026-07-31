"""
Chat API - Pydantic 模型定义

请求/响应的数据验证（DTO 层）
与 Router 层解耦，便于维护和测试
"""
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


# ========== 对话请求/响应 ==========

class ChatRequest(BaseModel):
    """对话请求（已废弃）"""
    message: str = Field(..., description="用户消息", min_length=1)
    session_id: Optional[str] = Field(None, description="会话ID，不传则新建")


class ChatResponse(BaseModel):
    """对话响应（已废弃）"""
    session_id: str = Field(..., description="会话ID")
    message_id: str = Field(..., description="消息ID")
    status: str = Field(..., description="处理状态")
    created_at: str = Field(..., description="创建时间")


# ========== 开始会话 ==========

class StartSessionRequest(BaseModel):
    """开启新会话请求"""
    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., description="用户消息", min_length=1)
    session_id: Optional[str] = Field(None, description="会话ID")
    user_context: Optional[dict] = Field(None, description="用户上下文，包含 file_context 等")
    reply_model_key: Optional[str] = Field(None, description="正常回答模型档位（plus|flash|max）")
    # [多模态支持] 图片上传字段
    image_url: Optional[str] = Field(None, description="图片URL（由 /upload-image 返回）")
    image_name: Optional[str] = Field(None, description="图片文件名")
    # [文件上传] Excel/Word 等文档
    file_path: Optional[str] = Field(None, description="上传的文件路径（沙盒内）")
    file_name: Optional[str] = Field(None, description="上传的文件名")
    skill_id: Optional[str] = Field(None, description="选中的 DeluSkill ID")
    # [NEW] 直连执行模式
    execution_mode: Optional[str] = Field(None, description="执行模式（优先于全局配置）")
    # [NEW] 深度检索模式
    deep_search: bool = Field(False, description="是否启用深度检索（PageIndex）")


class StartSessionResponse(BaseModel):
    """开启会话响应"""
    session_id: str
    plan_id: str
    summary: str
    steps: list
    status: str  # draft / executing
    message: str
    need_confirm: bool = True  # [Auto-Confirm] Planner 判定是否需要用户确认
    selected_skill_name: Optional[str] = Field(None, description="命中的操作手册名称")


# ========== 确认计划 ==========

class ConfirmPlanRequest(BaseModel):
    """确认计划请求"""
    session_id: str
    plan_id: str
    modified_steps: Optional[list] = None  # 用户修改后的步骤


class ConfirmPlanResponse(BaseModel):
    """确认计划响应"""
    session_id: str
    status: str
    message: str


# ========== 恢复执行 ==========

class ResumeRequest(BaseModel):
    """恢复执行请求 (Inline HITL)"""
    session_id: str = Field(..., description="会话ID")
    plan_id: str = Field(..., description="计划ID")
    input: str = Field(..., description="用户补充的信息")
    target_step_id: Optional[str] = Field(None, description="相关的步骤ID")


class ResumeResponse(BaseModel):
    """恢复执行响应"""
    session_id: str
    status: str
    message: str


# ========== 图片上传 ==========

class ImageUploadResponse(BaseModel):
    """图片上传响应"""
    success: bool
    image_url: str = Field(..., description="图片在sandbox内的相对路径")
    image_name: str = Field(..., description="图片文件名")
    session_id: str = Field(..., description="会话ID")
