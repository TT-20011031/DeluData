"""
Skills 操作手册 API (Router 层)

Skill CRUD、LLM 生成、Dry Run 测试

设计原则遵循：
- Design Rigor: Router/Service 分层，职责清晰
- RBAC: 管理员权限校验
- Async First: 所有端点异步
- Schema Validation: Pydantic 模型校验
"""
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Depends, status

from app.api.deps import get_current_admin, get_user_context
from app.core.security.auth import User
from app.models.common.context import UserContext
from app.models.config.skill_schemas import (
    SkillCreateRequest,
    SkillUpdateRequest,
    SkillResponse,
    SkillSummaryResponse,
    SkillSearchResult,
    SkillGenerateRequest,
    SkillGenerateResponse,
    DryRunRequest,
    DryRunResponse,
)
from app.services.skill_service import get_skill_service
from app.services.skill_generator import get_skill_generator
from app.services.dry_run_service import get_dry_run_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ========== CRUD API ==========

@router.get("", response_model=List[SkillSummaryResponse])
async def list_skills(
    user_context: UserContext = Depends(get_user_context)
):
    """
    获取 Skills 列表
    
    返回当前用户可见的所有 Skills（工作区 + 全局）
    按使用次数和创建时间排序
    """
    service = get_skill_service()
    skills = await service.list_skills(user_context)
    
    return [
        SkillSummaryResponse(
            id=s.id,
            title=s.title,
            description=s.description[:200] + "..." if len(s.description) > 200 else s.description,
            tags=s.tags or [],
            usage_count=s.usage_count,
            visibility=s.visibility.value
        )
        for s in skills
    ]


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: str,
    user_context: UserContext = Depends(get_user_context)
):
    """
    获取 Skill 详情
    """
    service = get_skill_service()
    skill = await service.get_skill(skill_id, user_context)
    
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill 不存在或无权访问"
        )
    
    from app.models.config.skill_schemas import SkillStepSchema
    
    return SkillResponse(
        id=skill.id,
        title=skill.title,
        description=skill.description,
        steps=[SkillStepSchema(**s) for s in skill.steps],
        tags=skill.tags or [],
        example_queries=skill.example_queries or [],
        visibility=skill.visibility.value,
        usage_count=skill.usage_count,
        created_by=skill.created_by,
        created_at=skill.created_at,
        updated_at=skill.updated_at
    )


@router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(
    request: SkillCreateRequest,
    admin: User = Depends(get_current_admin),
    user_context: UserContext = Depends(get_user_context)
):
    """
    创建 Skill
    
    仅管理员可创建
    """
    service = get_skill_service()
    
    try:
        skill = await service.create_skill(request, user_context)
        
        from app.models.config.skill_schemas import SkillStepSchema
        
        return SkillResponse(
            id=skill.id,
            title=skill.title,
            description=skill.description,
            steps=[SkillStepSchema(**s) for s in skill.steps],
            tags=skill.tags or [],
            example_queries=skill.example_queries or [],
            visibility=skill.visibility.value,
            usage_count=skill.usage_count,
            created_by=skill.created_by,
            created_at=skill.created_at,
            updated_at=skill.updated_at
        )
    except Exception as e:
        logger.error(f"创建 Skill 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建失败: {str(e)}"
        )


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: str,
    request: SkillUpdateRequest,
    admin: User = Depends(get_current_admin),
    user_context: UserContext = Depends(get_user_context)
):
    """
    更新 Skill
    
    仅创建者或管理员可更新
    """
    service = get_skill_service()
    
    try:
        # 管理员可修改任意 Skill（解决权限死锁问题）
        skill = await service.update_skill(skill_id, request, user_context, is_admin=True)
        
        if not skill:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Skill 不存在"
            )
        
        from app.models.config.skill_schemas import SkillStepSchema
        
        return SkillResponse(
            id=skill.id,
            title=skill.title,
            description=skill.description,
            steps=[SkillStepSchema(**s) for s in skill.steps],
            tags=skill.tags or [],
            example_queries=skill.example_queries or [],
            visibility=skill.visibility.value,
            usage_count=skill.usage_count,
            created_by=skill.created_by,
            created_at=skill.created_at,
            updated_at=skill.updated_at
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"更新 Skill 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新失败: {str(e)}"
        )


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: str,
    admin: User = Depends(get_current_admin),
    user_context: UserContext = Depends(get_user_context)
):
    """
    删除 Skill
    
    仅创建者或管理员可删除
    """
    service = get_skill_service()
    
    try:
        # 管理员可删除任意 Skill（解决权限死锁问题）
        success = await service.delete_skill(skill_id, user_context, is_admin=True)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Skill 不存在"
            )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )


# ========== 检索 API ==========

@router.post("/search", response_model=List[SkillSearchResult])
async def search_skills(
    query: str,
    top_k: int = 3,
    user_context: UserContext = Depends(get_user_context)
):
    """
    语义检索 Skills
    
    用于主会话中自动推荐相关 Skills
    """
    service = get_skill_service()
    results, _ = await service.search_skills(query, user_context, top_k)
    return results


# ========== LLM 生成 API ==========

@router.post("/generate", response_model=SkillGenerateResponse)
async def generate_skill(
    request: SkillGenerateRequest,
    admin: User = Depends(get_current_admin)
):
    """
    LLM 生成 Skill 内容
    
    根据自然语言描述，使用 LLM 生成结构化 Skill
    仅管理员可使用
    """
    generator = get_skill_generator()
    
    try:
        result = await generator.generate(request)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Skill 生成失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"生成失败: {str(e)}"
        )


@router.post("/reindex")
async def reindex_skills(
    admin: User = Depends(get_current_admin)
):
    """
    重新索引所有 Skills 到向量库
    
    用于修复 ChromaDB 与数据库不同步的问题
    仅管理员可使用
    """
    service = get_skill_service()
    
    try:
        result = await service.reindex_all_skills(workspace_id=admin.workspace_id)
        return result
    except Exception as e:
        logger.error(f"重新索引失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"重新索引失败: {str(e)}"
        )

# ========== Dry Run API ==========

@router.post("/dry-run", response_model=DryRunResponse)
async def dry_run_skill(
    request: DryRunRequest,
    user_context: UserContext = Depends(get_user_context)
):
    """
    Dry Run 测试 Skill
    
    模拟 Planner 规划过程，返回预测的执行计划（不实际执行）
    """
    service = get_dry_run_service()
    
    try:
        result = await service.run(request, user_context)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Dry Run 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"测试失败: {str(e)}"
        )
