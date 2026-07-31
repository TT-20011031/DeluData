"""
知识库 API - 图片访问路由

支持 Signed URL 验证
"""
import logging
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.db.database import get_async_db
from app.core.security.auth import User
from app.models.knowledge.graph import DocumentImage, File as KnowledgeFile

router = APIRouter(tags=["images"])
logger = logging.getLogger(__name__)


class ImageMetaResponse(BaseModel):
    file_id: str
    image_id: str
    page_number: int


def _normalize_image_id(raw_image_id: str) -> str:
    raw = str(raw_image_id or "").strip()
    if not raw:
        return ""
    # 统一处理 .png/.jpg/.jpeg 等后缀，并兼容传入路径片段
    path_obj = Path(raw)
    if path_obj.suffix:
        return path_obj.stem
    return path_obj.name


@router.get("/images/{file_id}/{image_id}")
async def get_image(
    file_id: str,
    image_id: str,
    expires: Optional[int] = None,
    sig: Optional[str] = None
):
    """
    获取知识库图片（支持 Signed URL 验证）
    
    安全特性：
    1. 无需 Authorization Header（img 标签兼容）
    2. 基于 HMAC 签名验证
    3. 过期时间控制
    """
    from app.core.utils.image_service import get_image_service
    
    image_service = get_image_service()
    
    image_id = _normalize_image_id(image_id)
    
    # 验证签名
    if expires and sig:
        if not image_service.verify_signature(file_id, image_id, expires, sig):
            raise HTTPException(status_code=403, detail="签名无效或已过期")
    
    # 获取图片路径
    image_path = image_service.get_image_path(image_id, file_id)
    if not image_path:
        raise HTTPException(status_code=404, detail="图片不存在")
    
    return FileResponse(
        path=str(image_path),
        media_type="image/png",
        headers={"Cache-Control": "max-age=86400"}
    )


@router.get("/images/{file_id}/{image_id}/meta", response_model=ImageMetaResponse)
async def get_image_meta(
    file_id: str,
    image_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    查询图片元数据（页码）。
    """
    normalized_image_id = _normalize_image_id(image_id)
    normalized_image_id = str(normalized_image_id or "").strip()
    if not normalized_image_id:
        raise HTTPException(status_code=404, detail="图片不存在")

    page_stmt = (
        select(DocumentImage.id, DocumentImage.page_number)
        .join(KnowledgeFile, KnowledgeFile.id == DocumentImage.file_id)
        .where(
            DocumentImage.file_id == file_id,
            KnowledgeFile.workspace_id == current_user.workspace_id,
            or_(
                DocumentImage.image_id == normalized_image_id,
                DocumentImage.id == normalized_image_id,  # 兼容旧数据
            ),
        )
        .limit(1)
    )
    page_result = await db.execute(page_stmt)
    page_row = page_result.first()
    if page_row is not None:
        raw_page_number = page_row[1]
        try:
            resolved_page = max(1, int(raw_page_number or 1))
        except (TypeError, ValueError):
            resolved_page = 1
        return ImageMetaResponse(
            file_id=file_id,
            image_id=normalized_image_id,
            page_number=resolved_page,
        )

    # 兼容按页渲染缓存图（image_id=page_0001）未入库到 document_images 的场景
    page_match = re.match(r"^page_(\d+)$", normalized_image_id)
    if page_match:
        file_stmt = select(KnowledgeFile.id).where(
            KnowledgeFile.id == file_id,
            KnowledgeFile.workspace_id == current_user.workspace_id,
        )
        file_result = await db.execute(file_stmt)
        if file_result.scalar_one_or_none():
            return ImageMetaResponse(
                file_id=file_id,
                image_id=normalized_image_id,
                page_number=max(1, int(page_match.group(1))),
            )

    raise HTTPException(status_code=404, detail="图片元数据不存在")
