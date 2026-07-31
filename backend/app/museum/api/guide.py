"""
博物馆模块 - 导览会话管理 API

遵循设计原则：
- 全异步 I/O (Async First)
- 严格模式校验 (Schema Validation)
- 零技术债 (Zero Tech Debt)

注意：导览核心功能（SSE 事件流）已在 events.py 实现
本模块仅提供会话管理辅助接口
"""
import uuid
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, status, UploadFile, File, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import get_user_context
from app.models.common.context import UserContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/guide", tags=["博物馆导览"])

# 临时图片目录（5分钟过期）
TEMP_IMAGE_DIR = Path(__file__).parent.parent / "temp_images"
TEMP_IMAGE_DIR.mkdir(exist_ok=True)
IMAGE_EXPIRE_SECONDS = 300  # 5 分钟


class UploadResponse(BaseModel):
    """图片上传响应"""
    image_id: str
    preview_url: str
    expires_at: str


@router.get("/session/{session_id}")
async def get_session(
    session_id: str,
    user_context: UserContext = Depends(get_user_context),
):
    """
    获取会话信息
    
    Args:
        session_id: 会话ID
        
    Returns:
        会话详情
    """
    from app.museum.services import get_session_repository
    
    repo = get_session_repository()
    session = await repo.get(session_id, workspace_id=user_context.workspace_id)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会话不存在: {session_id}"
        )
    
    return session


@router.delete("/session/{session_id}")
async def delete_session(
    session_id: str,
    user_context: UserContext = Depends(get_user_context),
):
    """
    删除会话
    
    Args:
        session_id: 会话ID
        
    Returns:
        删除结果
    """
    from app.museum.services import get_session_repository
    
    repo = get_session_repository()
    success = await repo.delete(session_id, workspace_id=user_context.workspace_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会话不存在: {session_id}"
        )
    
    logger.info(f"[Guide] 会话已删除: {session_id}")
    return {"message": f"会话已删除: {session_id}"}


@router.post("/upload", response_model=UploadResponse)
async def upload_scene_image(
    file: UploadFile = File(...),
    _: UserContext = Depends(get_user_context),
):
    """
    预上传场景图片
    
    将图片保存到临时目录，返回 image_id 供后续对话使用。
    图片 5 分钟后自动过期删除。
    
    Args:
        file: 上传的图片文件
        
    Returns:
        image_id: 图片ID
        preview_url: 预览URL
        expires_at: 过期时间
    """
    # 验证文件类型
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="只支持图片文件"
        )
    
    # 生成唯一 ID
    image_id = str(uuid.uuid4())
    
    # 确定文件扩展名
    ext = ".jpg"
    if file.content_type == "image/png":
        ext = ".png"
    elif file.content_type == "image/gif":
        ext = ".gif"
    elif file.content_type == "image/webp":
        ext = ".webp"
    
    # 保存文件
    save_path = TEMP_IMAGE_DIR / f"{image_id}{ext}"
    
    try:
        content = await file.read()
        save_path.write_bytes(content)
        logger.info(f"[Guide] 图片已上传: {image_id}, 大小: {len(content)} bytes")
    except Exception as e:
        logger.error(f"[Guide] 图片保存失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="图片保存失败"
        )
    
    # 异步清理过期图片
    asyncio.create_task(_cleanup_expired_images())
    
    expires_at = datetime.utcnow() + timedelta(seconds=IMAGE_EXPIRE_SECONDS)
    
    return UploadResponse(
        image_id=image_id,
        preview_url=f"/api/museum/guide/images/{image_id}",
        expires_at=expires_at.isoformat()
    )


@router.get("/images/{image_id}")
async def get_scene_image(
    image_id: str,
    _: UserContext = Depends(get_user_context),
):
    """
    获取预上传的场景图片
    
    Args:
        image_id: 图片ID
        
    Returns:
        图片文件
    """
    # 查找图片文件（支持多种扩展名）
    for ext in [".jpg", ".png", ".gif", ".webp"]:
        image_path = TEMP_IMAGE_DIR / f"{image_id}{ext}"
        if image_path.exists():
            return FileResponse(
                image_path,
                media_type=f"image/{ext[1:]}",
                filename=f"{image_id}{ext}"
            )
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"图片不存在或已过期: {image_id}"
    )


def get_image_path(image_id: str) -> Path | None:
    """
    根据 image_id 获取图片路径（供内部服务使用）
    
    Args:
        image_id: 图片ID
        
    Returns:
        图片路径，不存在则返回 None
    """
    for ext in [".jpg", ".png", ".gif", ".webp"]:
        image_path = TEMP_IMAGE_DIR / f"{image_id}{ext}"
        if image_path.exists():
            return image_path
    return None


async def _cleanup_expired_images():
    """清理过期的临时图片"""
    try:
        now = datetime.utcnow()
        for image_path in TEMP_IMAGE_DIR.glob("*"):
            if image_path.is_file():
                # 检查文件修改时间
                mtime = datetime.fromtimestamp(image_path.stat().st_mtime)
                if (now - mtime).total_seconds() > IMAGE_EXPIRE_SECONDS:
                    image_path.unlink()
                    logger.debug(f"[Guide] 清理过期图片: {image_path.name}")
    except Exception as e:
        logger.warning(f"[Guide] 清理过期图片失败: {e}")
