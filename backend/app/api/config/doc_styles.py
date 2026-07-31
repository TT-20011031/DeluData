"""
文档样式配置 API

提供文档样式的 CRUD 接口，支持：
- 工作空间默认配置（管理员）
- 用户个人偏好（当前用户）
- 预设主题列表
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.security.auth import User
from app.api.auth import get_current_admin, get_current_user
from app.models.common.context import UserContext
from app.api.knowledge.deps import get_user_context
from app.models.config.doc_style import (
    DocStyleConfigUpdate,
    DocStyleConfigResponse,
    get_doc_style_config,
    get_resolved_doc_style,
    save_doc_style_config,
    delete_doc_style_config,
)
from app.core.utils.docx_style_engine import PRESET_THEMES

router = APIRouter(prefix="/doc-styles")
logger = logging.getLogger(__name__)


@router.get(
    "/presets",
    summary="获取预设主题列表",
)
async def list_presets():
    """获取所有可用的预设主题名称和配置。"""
    return {
        "presets": {
            name: config for name, config in PRESET_THEMES.items()
        }
    }


@router.get(
    "/workspace",
    response_model=DocStyleConfigResponse,
    summary="获取工作空间默认文档样式",
)
async def get_workspace_style(
    user_context: UserContext = Depends(get_user_context),
):
    """获取当前工作空间的默认文档样式配置。"""
    config = await get_doc_style_config(user_context.workspace_id, user_id=None)
    return DocStyleConfigResponse(
        workspace_id=user_context.workspace_id,
        user_id=None,
        config=config or {},
    )


@router.put(
    "/workspace",
    response_model=DocStyleConfigResponse,
    summary="设置工作空间默认文档样式",
)
async def update_workspace_style(
    body: DocStyleConfigUpdate,
    user_context: UserContext = Depends(get_user_context),
    current_user: User = Depends(get_current_admin),
):
    """设置当前工作空间的默认文档样式（仅管理员）。"""
    config_to_save = body.config or {}
    if body.preset_theme:
        config_to_save["preset_theme"] = body.preset_theme

    saved = await save_doc_style_config(
        workspace_id=user_context.workspace_id,
        user_id=None,
        config=config_to_save,
    )
    return DocStyleConfigResponse(
        workspace_id=user_context.workspace_id,
        user_id=None,
        config=saved,
    )


@router.delete(
    "/workspace",
    summary="重置工作空间默认文档样式",
)
async def reset_workspace_style(
    user_context: UserContext = Depends(get_user_context),
    current_user: User = Depends(get_current_admin),
):
    """重置工作空间默认文档样式为系统内置默认（仅管理员）。"""
    deleted = await delete_doc_style_config(user_context.workspace_id, user_id=None)
    return {"success": deleted, "message": "已重置为系统默认"}


@router.get(
    "/user",
    response_model=DocStyleConfigResponse,
    summary="获取当前用户的文档样式偏好",
)
async def get_user_style(
    user_context: UserContext = Depends(get_user_context),
):
    """获取当前用户的文档样式偏好。"""
    config = await get_doc_style_config(user_context.workspace_id, user_id=user_context.user_id)
    return DocStyleConfigResponse(
        workspace_id=user_context.workspace_id,
        user_id=user_context.user_id,
        config=config or {},
    )


@router.put(
    "/user",
    response_model=DocStyleConfigResponse,
    summary="设置当前用户的文档样式偏好",
)
async def update_user_style(
    body: DocStyleConfigUpdate,
    user_context: UserContext = Depends(get_user_context),
):
    """设置当前用户的文档样式偏好。"""
    config_to_save = body.config or {}
    if body.preset_theme:
        config_to_save["preset_theme"] = body.preset_theme

    saved = await save_doc_style_config(
        workspace_id=user_context.workspace_id,
        user_id=user_context.user_id,
        config=config_to_save,
    )
    return DocStyleConfigResponse(
        workspace_id=user_context.workspace_id,
        user_id=user_context.user_id,
        config=saved,
    )


@router.delete(
    "/user",
    summary="重置当前用户的文档样式偏好",
)
async def reset_user_style(
    user_context: UserContext = Depends(get_user_context),
):
    """重置当前用户的文档样式偏好（回退到工作空间默认）。"""
    deleted = await delete_doc_style_config(
        user_context.workspace_id, user_id=user_context.user_id
    )
    return {"success": deleted, "message": "已重置为工作空间默认"}


@router.get(
    "/resolved",
    summary="获取合并后的完整文档样式配置",
)
async def get_resolved_style(
    user_context: UserContext = Depends(get_user_context),
):
    """获取当前用户合并后的完整样式配置（用户偏好 > 工作空间默认 > 系统内置）。"""
    resolved = await get_resolved_doc_style(
        workspace_id=user_context.workspace_id,
        user_id=user_context.user_id,
    )
    return {"config": resolved}
