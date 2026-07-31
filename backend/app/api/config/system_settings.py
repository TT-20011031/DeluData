"""
系统设置 API

提供超级管理员管理功能：
1. 系统回复风格模板管理（增删改查）
2. 为指定用户配置智能体参数

遵循设计原则：
- Async First: 全异步 I/O
- Schema Validation: 严格 Pydantic 校验
- RESTful: 规范的 API 设计
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field

from app.api.deps import get_current_admin
from app.core.security.auth import User
from app.models.config.system_template import (
    SystemTemplate,
    SystemTemplateCreate,
    SystemTemplateUpdate,
    list_effective_system_templates_async,
    get_system_template_async,
    create_system_template_async,
    update_system_template_async,
    delete_system_template_async,
)
from app.models.config.user_agent_config import (
    get_user_agent_config_async,
    save_user_agent_config_async,
    reset_user_agent_config_async,
    UserAgentConfigResponse,
    UserAgentConfigUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["系统设置"])


# ========== 响应模型 ==========

class UserConfigListItem(BaseModel):
    """用户配置列表项"""
    user_id: str
    username: str
    department: Optional[str] = None  # 部门名称
    max_retries: int
    always_confirm: bool
    execution_mode: str
    synthesizer_template: str
    has_custom_config: bool  # 是否有自定义配置


class UserListResponse(BaseModel):
    """用户列表响应"""
    users: List[UserConfigListItem]
    total: int


# ========== 系统模板管理 API ==========

@router.get(
    "/system-templates",
    response_model=List[SystemTemplate],
    summary="获取所有系统模板"
)
async def list_system_templates(
    admin: User = Depends(get_current_admin)
):
    """
    获取所有系统回复风格模板

    只读查询，不执行写库初始化，避免 GET 副作用。
    """
    templates = await list_effective_system_templates_async(admin.workspace_id)
    return templates


@router.post(
    "/system-templates",
    response_model=SystemTemplate,
    summary="创建系统模板"
)
async def create_system_template(
    request: SystemTemplateCreate,
    admin: User = Depends(get_current_admin)
):
    """创建新的系统回复风格模板"""
    # 检查是否已存在
    existing = await get_system_template_async(request.template_id, admin.workspace_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"模板 ID '{request.template_id}' 已存在"
        )
    
    template = await create_system_template_async(
        workspace_id=admin.workspace_id,
        template_id=request.template_id,
        name=request.name,
        description=request.description,
        prompt=request.prompt,
        is_default=request.is_default,
        sort_order=request.sort_order,
        created_by=str(admin.id)
    )
    
    logger.info(f"管理员 {admin.username} 创建系统模板: {request.template_id}")
    return template


@router.put(
    "/system-templates/{template_id}",
    response_model=SystemTemplate,
    summary="更新系统模板"
)
async def update_system_template(
    template_id: str,
    request: SystemTemplateUpdate,
    admin: User = Depends(get_current_admin)
):
    """更新系统回复风格模板"""
    if template_id == "default":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="default 模板由系统托管，不允许编辑"
        )

    template = await update_system_template_async(
        template_id=template_id,
        workspace_id=admin.workspace_id,
        name=request.name,
        description=request.description,
        prompt=request.prompt,
        is_default=request.is_default,
        sort_order=request.sort_order
    )
    
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"模板 '{template_id}' 不存在"
        )
    
    logger.info(f"管理员 {admin.username} 更新系统模板: {template_id}")
    return template


@router.delete(
    "/system-templates/{template_id}",
    summary="删除系统模板"
)
async def delete_system_template(
    template_id: str,
    admin: User = Depends(get_current_admin)
):
    """删除系统回复风格模板"""
    # 不允许删除默认模板
    if template_id == "default":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除默认模板"
        )
    
    success = await delete_system_template_async(template_id, admin.workspace_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"模板 '{template_id}' 不存在"
        )
    
    logger.info(f"管理员 {admin.username} 删除系统模板: {template_id}")
    return {"message": f"模板 '{template_id}' 已删除"}


# ========== 系统通用 API ==========

@router.get(
    "/departments",
    summary="获取部门列表"
)
async def list_departments(
    admin: User = Depends(get_current_admin)
):
    """获取当前租户的所有部门"""
    from app.core.db.database import get_async_db_manager
    from sqlalchemy import text
    
    db_manager = get_async_db_manager()
    async with db_manager.session_scope() as session:
        result = await session.execute(text("""
            SELECT id, name 
            FROM sys_departments 
            WHERE workspace_id = :workspace_id 
            ORDER BY order_num ASC, id ASC
        """), {"workspace_id": admin.workspace_id})
        departments = [{"id": row[0], "name": row[1]} for row in result.fetchall()]
    return departments


# ========== 用户配置管理 API ==========

@router.get(
    "/users",
    response_model=UserListResponse,
    summary="获取所有用户及其配置"
)
async def list_users_with_config(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="搜索用户名或部门"),
    department_id: Optional[int] = Query(None, description="筛选部门ID"),
    admin: User = Depends(get_current_admin)
):
    """
    获取所有用户及其智能体配置
    
    支持分页、搜索（用户名）和部门筛选
    """
    """
    获取所有用户及其智能体配置
    
    支持分页、搜索（用户名）和部门筛选
    """
    from app.core.db.database import get_async_db_manager
    from sqlalchemy import text
    from app.models.config.user_agent_config import _get_user_config_from_db
    
    try:
        print(f"DEBUG: 开始获取用户列表, admin={admin.username}, workspace={admin.workspace_id}")
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            # 构建基础 SQL
            base_sql = """
                FROM sys_users u
                LEFT JOIN sys_departments d ON u.department_id = d.id
                WHERE u.workspace_id = :workspace_id
            """
            params = {"workspace_id": admin.workspace_id, "limit": page_size, "offset": (page - 1) * page_size}
            
            # 添加搜索条件
            if search:
                print(f"DEBUG: 添加搜索条件: {search}")
                base_sql += " AND (u.username LIKE :search OR d.name LIKE :search)"
                params["search"] = f"%{search}%"
                
            # 添加部门筛选
            if department_id:
                print(f"DEBUG: 添加部门筛选: {department_id}")
                base_sql += " AND u.department_id = :dept_id"
                params["dept_id"] = department_id
                
            print(f"DEBUG: 执行 SQL 查询... Params: {params}")
            # 查询数据
            result = await session.execute(text(f"""
                SELECT u.id, u.username, d.name as department_name
                {base_sql}
                ORDER BY u.username ASC
                LIMIT :limit OFFSET :offset
            """), params)
            users = result.fetchall()
            
            # 查询总数
            count_result = await session.execute(text(f"""
                SELECT COUNT(*) {base_sql}
            """), params)
            total = count_result.scalar()
        
        # 获取每个用户的配置
        user_configs = []
        for user in users:
            user_id = str(user[0])
            username = user[1]
            dept_name = user[2]
            
            config = await get_user_agent_config_async(user_id, admin.workspace_id)
            
            # 检查是否有自定义配置
            has_custom = await _get_user_config_from_db(user_id, admin.workspace_id) is not None
            
            user_configs.append(UserConfigListItem(
                user_id=user_id,
                username=username,
                department=dept_name,
                max_retries=config.max_retries,
                always_confirm=config.always_confirm,
                execution_mode=config.execution_mode.value,
                synthesizer_template=config.synthesizer_template,
                has_custom_config=has_custom
            ))
        
        return UserListResponse(users=user_configs, total=total)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"ERROR: 获取用户列表失败: {e}")
        logger.error(f"获取用户列表失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取用户列表失败: {str(e)}"
        )


@router.get(
    "/users/{user_id}/config",
    response_model=UserAgentConfigResponse,
    summary="获取指定用户的配置"
)
async def get_user_config(
    user_id: str,
    admin: User = Depends(get_current_admin)
):
    """获取指定用户的智能体配置"""
    config = await get_user_agent_config_async(user_id, admin.workspace_id)
    return UserAgentConfigResponse.from_config(config)


@router.put(
    "/users/{user_id}/config",
    response_model=UserAgentConfigResponse,
    summary="为指定用户设置配置"
)
async def set_user_config(
    user_id: str,
    request: UserAgentConfigUpdate,
    admin: User = Depends(get_current_admin)
):
    """
    为指定用户设置智能体配置
    
    管理员可以为没有权限的用户配置参数
    """
    config = await save_user_agent_config_async(
        user_id=user_id,
        workspace_id=admin.workspace_id,
        max_retries=request.max_retries,
        always_confirm=request.always_confirm,
        execution_mode=request.execution_mode.value if request.execution_mode else None,
        synthesizer_template=request.synthesizer_template,
        synthesizer_custom_prompt=request.synthesizer_custom_prompt
    )
    
    logger.info(f"管理员 {admin.username} 为用户 {user_id} 设置配置")
    return UserAgentConfigResponse.from_config(config)


@router.post(
    "/users/{user_id}/config/reset",
    response_model=UserAgentConfigResponse,
    summary="重置指定用户的配置"
)
async def reset_user_config(
    user_id: str,
    admin: User = Depends(get_current_admin)
):
    """重置指定用户的配置为默认值"""
    config = await reset_user_agent_config_async(user_id, admin.workspace_id)
    
    logger.info(f"管理员 {admin.username} 重置用户 {user_id} 的配置")
    return UserAgentConfigResponse.from_config(config)
