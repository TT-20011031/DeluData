"""
模板管理 API (Router 层)

文档模板 CRUD、分析、测试渲染（仅管理员可用）

设计原则遵循：
- Design Rigor: Router/Service 分层，职责清晰
- RBAC: 管理员权限校验
- Async First: 所有端点异步
"""
import logging
import asyncio
from typing import Optional, TYPE_CHECKING
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query
from fastapi.responses import FileResponse

from app.api.deps import get_current_admin
from app.api.config.schemas import (
    TemplateResponse, TemplateListResponse, TemplateMatchResponse,
    AnalyzeResponse, TemplatePreviewResponse, TemplateCompileRequest,
    TemplateCompileResponse, TemplateGroupResponse,
    BlankFieldDetectResponse, BlankFieldConfirmRequest,
    CandidateFieldResponse, CandidateFieldLocation,
)
from app.models.config.template import Template, TemplateUpdate
from app.models.config.template_group import TemplateGroupCreate, TemplateGroupUpdate
from app.services.template import get_template_service

# 延迟导入避免循环依赖
if TYPE_CHECKING:
    from app.core.security.auth import User

router = APIRouter(prefix="/templates", tags=["模板管理"])
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========

def _template_to_response(template: Template) -> TemplateResponse:
    """转换为响应模型"""
    return TemplateResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        file_path=template.file_path,
        file_type=template.file_type,
        keywords=template.keywords,
        variables_schema=template.variables_schema,
        example_context=template.example_context,
        bindings=getattr(template, "bindings", None),
        is_active=template.is_active,
        group_id=template.group_id,
        created_at=template.created_at.isoformat(),
        updated_at=template.updated_at.isoformat()
    )


# ========== 模板 CRUD API ==========

@router.post("", response_model=TemplateResponse, summary="上传模板")
async def upload_template(
    name: str = Form(...),
    description: str = Form(None),
    keywords: str = Form(None),
    variables_schema: str = Form(None),
    example_context: str = Form(None),
    group_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    admin: "User" = Depends(get_current_admin)
):
    """上传文档模板（仅管理员）"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    try:
        service = get_template_service()
        file_content = await file.read()
        template = await service.upload_template(
            filename=file.filename,
            file_content=file_content,
            name=name,
            workspace_id=admin.workspace_id,
            user_id=admin.id,
            description=description,
            keywords=keywords,
            variables_schema_json=variables_schema,
            example_context_json=example_context,
            group_id=group_id,
        )
        return _template_to_response(template)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=TemplateListResponse, summary="获取模板列表")
async def list_templates(
    admin: "User" = Depends(get_current_admin)
):
    """获取所有模板（仅管理员）"""
    service = get_template_service()
    templates = await service.list_templates(
        workspace_id=admin.workspace_id,
        active_only=False
    )
    return TemplateListResponse(
        templates=[_template_to_response(t) for t in templates],
        total=len(templates)
    )


@router.get("/match", response_model=TemplateMatchResponse, summary="匹配模板")
async def match_templates(
    query: str,
    admin: "User" = Depends(get_current_admin)
):
    """根据任务描述匹配模板"""
    service = get_template_service()
    templates = await service.match_templates(
        workspace_id=admin.workspace_id,
        query=query
    )
    return TemplateMatchResponse(
        matched=len(templates) > 0,
        templates=[_template_to_response(t) for t in templates]
    )


@router.get("/{template_id}", response_model=TemplateResponse, summary="获取模板详情")
async def get_template(
    template_id: int,
    admin: "User" = Depends(get_current_admin)
):
    """获取单个模板详情（仅管理员）"""
    service = get_template_service()
    template = await service.get_template(template_id, admin.workspace_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在或无权访问")
    return _template_to_response(template)


@router.get("/{template_id}/analyze", response_model=AnalyzeResponse, summary="分析模板变量")
async def analyze_template(
    template_id: int,
    admin: "User" = Depends(get_current_admin)
):
    """分析模板中的变量（仅管理员）"""
    service = get_template_service()
    template = await service.get_template(template_id, admin.workspace_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在或无权访问")

    result = await service.analyze_template_variables_async(
        template.file_path,
        template.file_type
    )
    return AnalyzeResponse(
        variables=result['variables'],
        suggested_schema=result['suggested_schema']
    )


@router.post("/{template_id}/test-render", summary="测试渲染模板")
async def test_render_template(
    template_id: int,
    admin: "User" = Depends(get_current_admin)
):
    """使用示例数据测试渲染模板（仅管理员）"""
    service = get_template_service()
    template = await service.get_template(template_id, admin.workspace_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在或无权访问")

    if not template.example_context:
        raise HTTPException(status_code=400, detail="未配置示例数据，无法测试渲染")

    try:
        output_path = await service.render_template_to_temp(
            template,
            template.example_context
        )
        return FileResponse(
            output_path,
            filename=f"test_{template.name}.{template.file_type}",
            media_type="application/octet-stream"
        )
    except Exception as e:
        logger.error(f"测试渲染失败: {e}")
        raise HTTPException(status_code=500, detail=f"渲染失败: {str(e)}")


@router.get("/{template_id}/preview", response_model=TemplatePreviewResponse, summary="模板预览")
async def preview_template(
    template_id: int,
    admin: "User" = Depends(get_current_admin)
):
    """预览模板（docx 转 HTML），返回 HTML + mapping"""
    service = get_template_service()
    template = await service.get_template(template_id, admin.workspace_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在或无权访问")
    if template.file_type != "docx":
        raise HTTPException(status_code=400, detail="仅支持 docx 预览")

    try:
        result = await service.docx_to_html_with_mapping(template.file_path)
        return TemplatePreviewResponse(html=result["html"], mapping=result["mapping"])
    except RuntimeError as e:
        if str(e) in ["HTML_CONVERT_UNAVAILABLE", "HTML_MAPPING_FAILED"]:
            raise HTTPException(status_code=503, detail="拥堵请稍等")
        logger.error(f"模板预览失败: {e}")
        raise HTTPException(status_code=500, detail="预览失败")



# ========== LLM 智能检测（后台任务） ==========

@router.post("/{template_id}/detect-blanks-llm/start", summary="启动 LLM 智能检测")
async def start_llm_detection(
    template_id: int,
    admin: "User" = Depends(get_current_admin)
):
    """
    启动 LLM 智能检测后台任务
    
    返回 task_id 供前端轮询进度
    
    [Async First] 后台异步执行，立即返回
    """
    service = get_template_service()
    
    try:
        result = await service.start_llm_detection_task(
            template_id=template_id,
            workspace_id=admin.workspace_id,
            user_id=str(admin.id)
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"启动 LLM 检测失败: {e}")
        raise HTTPException(status_code=500, detail="启动检测失败")


@router.get("/detect-blanks-llm/status/{task_id}", summary="查询 LLM 检测状态")
async def get_llm_detection_status(
    task_id: str,
    admin: "User" = Depends(get_current_admin)
):
    """
    查询 LLM 智能检测任务状态
    
    返回：
    - status: pending | running | completed | failed
    - progress: 0-100 进度百分比
    - stage: 当前阶段描述
    - result: 检测结果（completed 时）
    - error: 错误信息（failed 时）
    """
    service = get_template_service()
    
    try:
        result = service.get_detection_task_status(task_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"查询检测状态失败: {e}")
        raise HTTPException(status_code=500, detail="查询失败")


@router.post("/{template_id}/detect-blanks-llm/confirm", response_model=TemplateCompileResponse, summary="确认检测并编译模板")
async def confirm_llm_detection(
    template_id: int,
    request: BlankFieldConfirmRequest,
    admin: "User" = Depends(get_current_admin)
):
    """
    确认 LLM 检测结果并编译模板

    流程：
    - 接收用户确认的候选字段
    - 编译生成 compiled_{id}.docx
    - 可选更新 variables_schema
    """
    from app.services.template.detector import CandidateField, FieldLocation
    service = get_template_service()

    try:
        candidates = [
            CandidateField(
                key=c.key,
                label=c.label,
                type=c.type,
                location=FieldLocation(
                    type=c.location.type,
                    paragraph_index=c.location.paragraph_index,
                    table_index=c.location.table_index,
                    row_index=c.location.row_index,
                    cell_index=c.location.cell_index,
                    selected_text=c.location.selected_text,
                    mapping_id=c.location.mapping_id
                ),
                confidence=c.confidence,
                source=c.source,
                context=c.context
            )
            for c in request.candidates
        ]
        compiled_path, compiled_name = await service.confirm_llm_detected_fields(
            template_id=template_id,
            workspace_id=admin.workspace_id,
            candidates=candidates,
            output_name=request.output_name,
            update_schema=request.update_schema
        )
        return TemplateCompileResponse(
            compiled_path=compiled_path,
            compiled_name=compiled_name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"确认检测并编译失败: {e}")
        raise HTTPException(status_code=500, detail="确认编译失败")


@router.post("/{template_id}/optimize-blanks", response_model=BlankFieldDetectResponse, summary="优化空白字段标签")
async def optimize_blank_labels(
    template_id: int,
    request: BlankFieldDetectResponse,
    admin: "User" = Depends(get_current_admin)
):
    """
    使用 LLM 优化空白字段标签（仅管理员）
    
    接收 detect-blanks 的结果，返回优化后的候选字段列表
    """
    from app.services.template.detector import CandidateField, FieldLocation
    
    service = get_template_service()
    template = await service.get_template(template_id, admin.workspace_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在或无权访问")
    
    # 将请求转回 CandidateField 模型
    candidates = [
        CandidateField(
            key=c.key,
            label=c.label,
            type=c.type,
            location=FieldLocation(
                type=c.location.type,
                paragraph_index=c.location.paragraph_index,
                table_index=c.location.table_index,
                row_index=c.location.row_index,
                cell_index=c.location.cell_index,
                selected_text=c.location.selected_text,
                mapping_id=c.location.mapping_id
            ),
            confidence=c.confidence,
            source=c.source,
            context=c.context
        )
        for c in request.candidates
    ]
    
    try:
        optimized = await service.optimize_candidates_with_llm(candidates)
        
        # 转回响应模型
        candidate_responses = [
            CandidateFieldResponse(
                key=c.key,
                label=c.label,
                type=c.type,
                location=CandidateFieldLocation(
                    type=c.location.type,
                    paragraph_index=c.location.paragraph_index,
                    table_index=c.location.table_index,
                    row_index=c.location.row_index,
                    cell_index=c.location.cell_index,
                    selected_text=c.location.selected_text,
                    mapping_id=c.location.mapping_id
                ),
                confidence=c.confidence,
                source=c.source,
                context=c.context
            )
            for c in optimized
        ]
        
        high_confidence_count = sum(1 for c in optimized if c.confidence >= 0.8)
        
        return BlankFieldDetectResponse(
            candidates=candidate_responses,
            total=len(candidate_responses),
            high_confidence_count=high_confidence_count
        )
    except Exception as e:
        logger.error(f"LLM 优化失败: {e}")
        raise HTTPException(status_code=500, detail="优化失败")


@router.post("/{template_id}/compile", response_model=TemplateCompileResponse, summary="编译模板")
async def compile_template(
    template_id: int,
    request: TemplateCompileRequest,
    admin: "User" = Depends(get_current_admin)
):
    """编译模板（docx），插入占位符并输出编译文件"""
    service = get_template_service()
    template = await service.get_template(template_id, admin.workspace_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在或无权访问")
    if template.file_type != "docx":
        raise HTTPException(status_code=400, detail="仅支持 docx 编译")

    try:
        compiled_path, compiled_name = await service.compile_template(
            template,
            request.bindings,
            request.output_name
        )
        return TemplateCompileResponse(
            compiled_path=compiled_path,
            compiled_name=compiled_name
        )
    except Exception as e:
        logger.error(f"模板编译失败: {e}")
        raise HTTPException(status_code=500, detail=f"编译失败: {str(e)}")


@router.put("/{template_id}", response_model=TemplateResponse, summary="更新模板")
async def update_template(
    template_id: int,
    request: TemplateUpdate,
    admin: "User" = Depends(get_current_admin)
):
    """更新模板信息（仅管理员）"""
    try:
        service = get_template_service()
        template = await service.update_template(
            template_id,
            request,
            admin.workspace_id
        )
        if not template:
            raise HTTPException(status_code=403, detail="无权修改此模板")

        should_compile = bool(request.bindings or request.variables_schema)
        schema_empty = not (template.variables_schema or {})
        if template.file_type.lower() == "docx" and (should_compile or schema_empty):
            async def _background_compile():
                try:
                    await service.auto_compile_template(
                        template_id=template_id,
                        workspace_id=admin.workspace_id,
                        bindings=request.bindings
                    )
                    logger.info(f"模板自动编译完成: {template_id}")
                except Exception as e:
                    logger.warning(f"模板自动编译失败: {template_id}, error={e}")
            asyncio.create_task(_background_compile())

        return _template_to_response(template)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{template_id}", summary="删除模板")
async def delete_template(
    template_id: int,
    admin: "User" = Depends(get_current_admin)
):
    """删除模板（仅管理员）"""
    service = get_template_service()
    success, message = await service.delete_template(template_id, admin.workspace_id)
    if not success:
        status_code = 404 if "不存在" in message else 403
        raise HTTPException(status_code=status_code, detail=message)
    return {"success": True, "message": message}


# ========== 模板分组 API ==========

@router.get("/groups/list", summary="获取模板分组列表")
async def list_template_groups(
    admin: "User" = Depends(get_current_admin)
):
    """获取所有模板分组（仅管理员）"""
    service = get_template_service()
    groups = await service.list_template_groups(admin.workspace_id)
    return {
        "groups": [
            TemplateGroupResponse(
                id=g.id,
                name=g.name,
                description=g.description,
                color=g.color,
                example_count=g.example_count,
                created_at=g.created_at.isoformat(),
                updated_at=g.updated_at.isoformat()
            ) for g in groups
        ],
        "total": len(groups)
    }


@router.post("/groups", response_model=TemplateGroupResponse, summary="创建模板分组")
async def create_template_group(
    request: TemplateGroupCreate,
    admin: "User" = Depends(get_current_admin)
):
    """创建模板分组（仅管理员）"""
    service = get_template_service()
    group = await service.create_template_group(
        request,
        admin.workspace_id,
        admin.id
    )
    return TemplateGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        color=group.color,
        example_count=0,
        created_at=group.created_at.isoformat(),
        updated_at=group.updated_at.isoformat()
    )


@router.put("/groups/{group_id}", response_model=TemplateGroupResponse, summary="更新模板分组")
async def update_template_group(
    group_id: int,
    request: TemplateGroupUpdate,
    admin: "User" = Depends(get_current_admin)
):
    """更新模板分组（仅管理员）"""
    try:
        service = get_template_service()
        group = await service.update_template_group(
            group_id,
            request,
            admin.workspace_id
        )
        if not group:
            raise HTTPException(status_code=403, detail="无权修改此分组")
        return TemplateGroupResponse(
            id=group.id,
            name=group.name,
            description=group.description,
            color=group.color,
            example_count=getattr(group, 'example_count', 0),
            created_at=group.created_at.isoformat(),
            updated_at=group.updated_at.isoformat()
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/groups/{group_id}", summary="删除模板分组")
async def delete_template_group(
    group_id: int,
    admin: "User" = Depends(get_current_admin)
):
    """删除模板分组（仅管理员）"""
    service = get_template_service()
    success, message = await service.delete_template_group(group_id, admin.workspace_id)
    if not success:
        status_code = 404 if "不存在" in message else 403
        raise HTTPException(status_code=status_code, detail=message)
    return {"success": True, "message": message}
