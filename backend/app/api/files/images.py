"""
图片 API (v2.1 多模态RAG)

提供图片访问和以图搜文功能
"""
import tempfile
import logging
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_async_db
from app.core.security.rbac_deps import CheckPerm
from app.core.storage.service import get_storage_service
from app.core.security.auth import User
from app.core.llm.vlm_service import get_vlm_service
from app.core.utils.image_service import get_image_service
from app.core.utils.storage_path import is_oss_path, resolve_storage_path
from app.skills.doc_skill import DocSkill
from app.models.common.context import UserContext
from app.models.knowledge.graph import DocumentImage, File as KnowledgeFile

router = APIRouter(prefix="/knowledge/images", tags=["images"])
get_current_admin = CheckPerm("knowledge:manage")
logger = logging.getLogger(__name__)


@router.get("/{file_id}/{image_name}")
async def get_image(
    file_id: str, 
    image_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取图片静态文件
    
    Args:
        file_id: 文件 ID
        image_name: 图片文件名（如 img_xxxxxxxx.png）
    
    Returns:
        图片文件响应
    """
    # 权限校验：文件必须属于当前租户
    result = await db.execute(
        select(KnowledgeFile.id).where(
            KnowledgeFile.id == file_id,
            KnowledgeFile.workspace_id == current_user.workspace_id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Image not found")

    image_id = image_name[:-4] if image_name.lower().endswith(".png") else image_name
    image_result = await db.execute(
        select(DocumentImage.storage_path).where(
            DocumentImage.file_id == file_id,
            DocumentImage.image_id == image_id,
        )
    )
    stored_path = image_result.scalar_one_or_none()
    if stored_path:
        storage_service = get_storage_service()
        if is_oss_path(stored_path):
            signed_url = await storage_service.generate_signed_url(
                stored_path,
                filename=image_name,
                inline=True,
            )
            if signed_url:
                return RedirectResponse(url=signed_url, status_code=307)

        local_path = Path(resolve_storage_path(stored_path))
        if local_path.exists():
            return FileResponse(
                local_path,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400"},
            )

    image_service = get_image_service()
    image_path = image_service.storage_root / file_id / image_name
    
    if image_path.exists():
        return FileResponse(
            image_path, 
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"}  # 缓存 1 天
        )
    
    raise HTTPException(status_code=404, detail="Image not found")


@router.post("/search")
async def search_by_image(
    file: UploadFile = File(...),
    query: str = Form(""),
    top_k: int = Form(5),
    current_user: User = Depends(get_current_admin)
):
    """
    以图搜文 - 上传图片搜索相关文档
    
    流程：
    1. 保存上传图片
    2. VLM 生成描述（理解图片内容）
    3. 使用描述检索知识库
    4. 返回相关文档和关联图片
    
    Args:
        file: 上传的图片文件
        query: 附带的问题（可选）
        top_k: 返回结果数量
    
    Returns:
        {
            "image_understanding": "图片理解结果",
            "results": [
                {
                    "content": "...",
                    "source_file": "...",
                    "score": 0.85,
                    "related_images": [...]
                }
            ]
        }
    """
    vlm_service = get_vlm_service()
    doc_skill = DocSkill()
    image_service = get_image_service()
    
    # 验证文件类型
    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400, 
            detail=f"不支持的图片格式: {file.content_type}"
        )
    
    # 保存临时文件
    suffix = Path(file.filename).suffix if file.filename else ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        # 1. VLM 理解图片
        logger.info(f"[ImageSearch] 开始理解上传图片: {file.filename}")
        image_description = await vlm_service.understand_user_image(tmp_path, query)
        
        if not image_description:
            raise HTTPException(
                status_code=500,
                detail="无法理解图片内容，请稍后重试"
            )
        
        logger.info(f"[ImageSearch] 图片理解完成: {len(image_description)} chars")
        
        # 2. 构建用户上下文
        if not current_user.workspace_id:
            raise HTTPException(status_code=400, detail="无有效工作空间")
        
        user_context = UserContext(
            user_id=current_user.id,
            workspace_id=current_user.workspace_id,
            dept_id=current_user.department_id,
            role=current_user.role,
            is_workspace_admin=current_user.is_workspace_admin,
            data_scope=current_user.data_scope
        )
        
        # 3. 使用描述检索知识库
        # 合并用户问题和图片描述作为查询
        search_query = image_description
        if query:
            search_query = f"{query}\n\n图片内容：{image_description}"
        
        results = await doc_skill.query_knowledge_base(
            query=search_query,
            user_context=user_context,
            top_k=top_k,
            original_query=query or "以图搜文"
        )
        
        logger.info(f"[ImageSearch] 检索完成: {len(results)} 条结果")
        
        # 4. 构建响应（包含关联图片）
        response_results = []
        for r in results:
            # 解析关联图片
            related_images = []
            related_ids_str = r.metadata.get("related_image_ids", "")
            if related_ids_str:
                related_ids = related_ids_str.split(",")
                file_id = r.metadata.get("file_id", "")
                for img_id in related_ids:
                    if img_id:
                        related_images.append({
                            "id": img_id,
                            "url": image_service.get_image_url(img_id, file_id)
                        })
            
            response_results.append({
                "content": r.content,
                "source_file": r.source_file,
                "score": r.score,
                "type": r.metadata.get("type", "text"),
                "related_images": related_images
            })
        
        return {
            "image_understanding": image_description,
            "query": query,
            "results": response_results
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ImageSearch] 搜索失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 清理临时文件
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/describe")
async def describe_image(
    file: UploadFile = File(...),
    prompt_type: str = Form("general"),
    current_user: User = Depends(get_current_admin)
):
    """
    单独使用 VLM 描述图片（调试/测试用）
    
    Args:
        file: 上传的图片
        prompt_type: 提示类型 (general/chart/screenshot/exhibit)
    
    Returns:
        {"description": "..."}
    """
    vlm_service = get_vlm_service()
    
    # 验证文件类型
    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400, 
            detail=f"不支持的图片格式: {file.content_type}"
        )
    
    # 保存临时文件
    suffix = Path(file.filename).suffix if file.filename else ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        description = await vlm_service.describe_image(tmp_path, prompt_type)
        
        if not description:
            raise HTTPException(
                status_code=500,
                detail="无法生成图片描述"
            )
        
        return {"description": description}
        
    finally:
        Path(tmp_path).unlink(missing_ok=True)
