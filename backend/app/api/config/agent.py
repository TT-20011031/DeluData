"""
智能体配置 API (Router 层)

遵循设计原则：
- Design Rigor: 模块化解耦，Router/Service/Repo 分层
- Security Left: RBAC 鉴权前置（get_current_admin）
- Async First: 全异步 I/O
- Schema Validation: Pydantic 严格校验
"""
import logging
from typing import List, Optional

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_admin, get_current_user
from app.core.security.auth import User
from app.models.config.agent_config import (
    get_or_create_agent_config_async,
    save_agent_config_async,
    AgentConfigUpdate,
    MAX_CUSTOM_PROMPT_LENGTH
)
from app.models.config.user_agent_config import (
    get_user_agent_config_async,
    save_user_agent_config_async,
    reset_user_agent_config_async,
    UserAgentConfigUpdate,
    UserAgentConfigResponse,
)
from app.models.config.user_prompt_templates import (
    list_user_templates_async,
    create_user_template_async,
    update_user_template_async,
    delete_user_template_async,
    UserPromptTemplate,
    UserPromptTemplateCreate,
    UserPromptTemplateUpdate,
    RESERVED_TEMPLATE_IDS,
    MAX_TEMPLATE_PROMPT_LENGTH
)
logger = logging.getLogger(__name__)

router = APIRouter()


# ========== Response Schema ==========

class AgentConfigResponse(BaseModel):
    """智能体配置响应"""
    max_retries: int = Field(description="最大重试次数")
    always_confirm: bool = Field(description="始终确认计划")
    execution_mode: str = Field(description="执行模式 (auto|rag_only|sql_only|chart_only|office_only)")
    synthesizer_template: str = Field(description="当前模板 ID")
    synthesizer_custom_prompt: str = Field(description="用户微调 Prompt")


class TemplateInfo(BaseModel):
    """模板信息"""
    template_id: str
    name: str
    description: str
    prompt: str
    is_system: bool = Field(description="是否为系统预设模板")


class TemplateListResponse(BaseModel):
    """模板列表响应"""
    system_templates: List[TemplateInfo]
    user_templates: List[TemplateInfo]


class UserTemplateResponse(BaseModel):
    """用户模板响应"""
    template_id: str
    name: str
    description: str
    prompt: str


# ========== Agent Config API ==========

# ========== 用户级配置 API (/agent/me) ==========

@router.get("/agent/me", response_model=UserAgentConfigResponse, summary="获取用户个人配置")
async def get_my_agent_config(
    current_user: User = Depends(get_current_user)
):
    """
    获取当前用户的个人智能体配置
    
    返回合并后的完整配置（用户配置 > 工作区配置 > 系统默认值）
    
    [FIX] 使用 get_current_user 而非 get_current_admin，所有登录用户均可获取自己的配置
    """
    config = await get_user_agent_config_async(
        user_id=str(current_user.id),
        workspace_id=current_user.workspace_id
    )
    
    return UserAgentConfigResponse.from_config(config)


@router.put("/agent/me", response_model=UserAgentConfigResponse, summary="更新用户个人配置")
async def update_my_agent_config(
    request: UserAgentConfigUpdate,
    admin: User = Depends(get_current_admin)
):
    """
    更新当前用户的个人智能体配置
    
    支持增量更新，只更新传入的字段
    """
    config = await save_user_agent_config_async(
        user_id=str(admin.id),
        workspace_id=admin.workspace_id,
        max_retries=request.max_retries,
        always_confirm=request.always_confirm,
        execution_mode=request.execution_mode.value if request.execution_mode else None,
        synthesizer_template=request.synthesizer_template,
        synthesizer_custom_prompt=request.synthesizer_custom_prompt
    )
    
    logger.info(f"用户配置已更新, user_id={admin.id}, username={admin.username}")
    
    return UserAgentConfigResponse.from_config(config)


@router.post("/agent/me/reset", response_model=UserAgentConfigResponse, summary="重置个人配置")
async def reset_my_agent_config(
    admin: User = Depends(get_current_admin)
):
    """
    重置当前用户的配置为默认值
    
    重置后，用户将继承工作区配置或系统默认值
    """
    config = await reset_user_agent_config_async(
        user_id=str(admin.id),
        workspace_id=admin.workspace_id
    )
    
    logger.info(f"用户配置已重置, user_id={admin.id}, username={admin.username}")
    
    return UserAgentConfigResponse.from_config(config)


# ========== 工作区级配置 API [DEPRECATED] ==========

@router.get(
    "/agent", 
    response_model=AgentConfigResponse, 
    summary="[已废弃] 获取工作区配置",
    deprecated=True
)
async def get_agent_config(
    admin: User = Depends(get_current_admin)
):
    """
    [DEPRECATED] 获取智能体全局配置
    
    ✅ 建议使用 GET /config/agent/me 获取用户个人配置
    
    返回当前工作区的配置值（从数据库读取）
    """
    config = await get_or_create_agent_config_async(admin.workspace_id)
    
    return AgentConfigResponse(
        max_retries=config.max_retries,
        always_confirm=config.always_confirm,
        execution_mode=config.execution_mode,
        synthesizer_template=config.synthesizer_template,
        synthesizer_custom_prompt=config.synthesizer_custom_prompt
    )


@router.put(
    "/agent", 
    response_model=AgentConfigResponse, 
    summary="[已废弃] 更新工作区配置",
    deprecated=True
)
async def update_agent_config(
    request: AgentConfigUpdate,
    admin: User = Depends(get_current_admin)
):
    """
    [DEPRECATED] 更新智能体全局配置
    
    ✅ 建议使用 PUT /config/agent/me 更新用户个人配置
    
    支持增量更新，只更新传入的字段
    配置会持久化到数据库，重启后保留
    """
    config = await save_agent_config_async(
        workspace_id=admin.workspace_id,
        max_retries=request.max_retries,
        always_confirm=request.always_confirm,
        execution_mode=request.execution_mode,
        synthesizer_template=request.synthesizer_template,
        synthesizer_custom_prompt=request.synthesizer_custom_prompt
    )
    
    logger.info(f"智能体配置已更新, 操作者={admin.username}")
    
    return AgentConfigResponse(
        max_retries=config.max_retries,
        always_confirm=config.always_confirm,
        execution_mode=config.execution_mode,
        synthesizer_template=config.synthesizer_template,
        synthesizer_custom_prompt=config.synthesizer_custom_prompt
    )


# ========== Synthesizer Templates API ==========

@router.get(
    "/agent/synthesizer-templates", 
    response_model=TemplateListResponse, 
    summary="获取所有模板"
)
async def get_synthesizer_templates(
    admin: User = Depends(get_current_admin)
):
    """
    获取所有可用的 Synthesizer 模板（仅管理员）
    
    返回系统模板（从数据库读取）和用户自定义模板两个分组
    """
    # 系统模板（从数据库读取，Database-First）
    from app.models.config.system_template import (
        list_effective_system_templates_async,
    )

    db_system_templates = await list_effective_system_templates_async(admin.workspace_id)
    
    system_templates = [
        TemplateInfo(
            template_id=tpl.template_id,
            name=tpl.name,
            description=tpl.description,
            prompt=tpl.prompt,
            is_system=True
        )
        for tpl in db_system_templates
    ]
    
    # 用户模板（只返回当前用户创建的私有模板）
    user_templates_db = await list_user_templates_async(admin.workspace_id, str(admin.id))
    user_templates = [
        TemplateInfo(
            template_id=ut.template_id,
            name=ut.name,
            description=ut.description,
            prompt=ut.prompt,
            is_system=False
        )
        for ut in user_templates_db
    ]
    
    return TemplateListResponse(
        system_templates=system_templates,
        user_templates=user_templates
    )


@router.post(
    "/agent/synthesizer-templates", 
    response_model=UserTemplateResponse, 
    summary="创建用户模板",
    status_code=status.HTTP_201_CREATED
)
async def create_user_template(
    request: UserPromptTemplateCreate,
    admin: User = Depends(get_current_admin)
):
    """
    创建新的用户自定义模板（仅管理员）
    
    模板 ID 会自动生成（usr_ 前缀 + 名称 + UUID）
    """
    # 创建（带 created_by 实现私有模板隔离）
    template = await create_user_template_async(
        workspace_id=admin.workspace_id, 
        data=request,
        created_by=str(admin.id)
    )
    
    logger.info(f"创建用户模板: {template.template_id}, 操作者={admin.username}")
    
    return UserTemplateResponse(
        template_id=template.template_id,
        name=template.name,
        description=template.description,
        prompt=template.prompt
    )


@router.put(
    "/agent/synthesizer-templates/{template_id}", 
    response_model=UserTemplateResponse, 
    summary="更新用户模板"
)
async def update_user_template(
    template_id: str,
    request: UserPromptTemplateUpdate,
    admin: User = Depends(get_current_admin)
):
    """
    更新用户自定义模板（仅管理员）
    
    不能修改系统预设模板
    """
    # 禁止修改系统模板
    if template_id in RESERVED_TEMPLATE_IDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不能修改系统预设模板"
        )
    
    template = await update_user_template_async(admin.workspace_id, template_id, request)
    
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模板不存在"
        )
    
    logger.info(f"更新用户模板: {template_id}, 操作者={admin.username}")
    
    return UserTemplateResponse(
        template_id=template.template_id,
        name=template.name,
        description=template.description,
        prompt=template.prompt
    )


@router.delete(
    "/agent/synthesizer-templates/{template_id}", 
    summary="删除用户模板",
    status_code=status.HTTP_204_NO_CONTENT
)
async def delete_user_template(
    template_id: str,
    admin: User = Depends(get_current_admin)
):
    """
    删除用户自定义模板（仅管理员）
    
    不能删除系统预设模板
    """
    # 禁止删除系统模板
    if template_id in RESERVED_TEMPLATE_IDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不能删除系统预设模板"
        )
    
    success = await delete_user_template_async(admin.workspace_id, template_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模板不存在"
        )
    
    logger.info(f"删除用户模板: {template_id}, 操作者={admin.username}")
    
    return None


@router.post(
    "/agent/reset-synthesizer-config", 
    response_model=AgentConfigResponse, 
    summary="重置为默认配置"
)
async def reset_synthesizer_config(
    admin: User = Depends(get_current_admin)
):
    """
    一键恢复默认配置（仅管理员）
    
    将 synthesizer_template 重置为 default，清空 synthesizer_custom_prompt
    """
    config = await save_agent_config_async(
        workspace_id=admin.workspace_id,
        synthesizer_template="default",
        synthesizer_custom_prompt=""
    )
    
    logger.info(f"Synthesizer 配置已重置为默认, 操作者={admin.username}")
    
    return AgentConfigResponse(
        max_retries=config.max_retries,
        always_confirm=config.always_confirm,
        execution_mode=config.execution_mode,
        synthesizer_template=config.synthesizer_template,
        synthesizer_custom_prompt=config.synthesizer_custom_prompt
    )
