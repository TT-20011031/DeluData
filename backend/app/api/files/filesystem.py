"""
文件上传/下载 API

管理用户上传的文件和处理结果
支持 OfficeWorker 的文件处理流程
"""
import os
import uuid
import shutil
import logging
import aiofiles  # [Async First] 异步文件 I/O
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.api.deps import get_current_user_optional
from app.core.security.auth import User
from app.services.filesystem_service import FilesystemService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["files"])  # 前缀由入口路由聚合层提供


# ========== 响应模型 ==========

class UploadResponse(BaseModel):
    """上传响应"""
    success: bool
    file_path: str = Field(description="文件在沙盒中的路径")
    file_name: str = Field(description="文件名")
    session_id: str = Field(description="会话 ID")
    sandbox_path: str = Field(description="沙盒目录路径")


class FileInfo(BaseModel):
    """文件信息"""
    name: str
    size: int
    size_str: str
    created_at: Optional[str] = None


# ========== 工具函数 ==========

def get_sandbox_path(session_id: str) -> Path:
    """
    获取会话沙盒目录
    
    Args:
        session_id: 会话 ID
        
    Returns:
        沙盒目录 Path 对象
    """
    settings = get_settings()
    sandbox_base = Path(settings.sandbox.base_dir)
    session_path = sandbox_base / f"session_{session_id}"
    session_path.mkdir(parents=True, exist_ok=True)
    return session_path


def format_file_size(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    else:
        return f"{size / 1024 / 1024:.1f}MB"


# ========== API 端点 ==========

@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    session_id: Optional[str] = Query(None, description="会话 ID，不传则新建"),
    current_user: Optional[User] = Depends(get_current_user_optional)
    # [RBAC 注释] 允许匿名上传 - 适用于 Kiosk 模式/公开沙箱
    # 如需强制认证，请改为: get_current_user
):
    """
    上传文件到沙盒
    
    文件将被存储到会话专属的沙盒目录中，
    后续可通过 OfficeWorker 进行处理
    
    [多租户] 如果用户已登录，将使用租户隔离路径
    
    Args:
        file: 上传的文件
        session_id: 会话 ID（可选，不传则自动生成）
        
    Returns:
        UploadResponse: 包含文件路径和会话信息
    """
    # 生成或使用现有 session_id
    if not session_id:
        session_id = uuid.uuid4().hex[:12]
    
    # [多租户隔离] 如果用户已登录，使用租户隔离路径
    if current_user and hasattr(current_user, 'workspace_id') and current_user.workspace_id:
        workspace_id = current_user.workspace_id
        # 使用租户隔离路径: uploads/{workspace_id}/sandbox/{session_id}/
        sandbox_base = Path(FilesystemService.get_workspace_upload_dir(workspace_id)) / "sandbox"
        sandbox_path = sandbox_base / f"session_{session_id}"
        sandbox_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"[多租户] 使用隔离路径: {sandbox_path}")
    else:
        # 未登录用户使用默认沙盒（向后兼容）
        sandbox_path = get_sandbox_path(session_id)
    
    # 安全文件名处理
    original_filename = file.filename or "uploaded_file"
    # 移除路径分隔符，防止目录遍历攻击
    safe_filename = Path(original_filename).name.replace("..", "").replace("/", "_").replace("\\", "_")
    
    # [FIX] 竞态条件：如果同名文件已存在，添加时间戳后缀
    file_path = sandbox_path / safe_filename
    if file_path.exists():
        # 拆分文件名和扩展名
        stem = file_path.stem
        suffix = file_path.suffix
        # 添加时间戳后缀 (如 data_20260110_171530.xlsx)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_filename = f"{stem}_{timestamp}{suffix}"
        file_path = sandbox_path / new_filename
        safe_filename = new_filename
        logger.warning(f"同名文件已存在，重命名为: {new_filename}")
    
    # 检查文件大小
    settings = get_settings()
    max_size = settings.sandbox.max_file_size_mb * 1024 * 1024
    
    try:
        content = await file.read()
        
        if len(content) > max_size:
            raise HTTPException(
                status_code=400, 
                detail=f"文件过大，最大允许 {settings.sandbox.max_file_size_mb}MB"
            )
        
        # [Async First] 使用 aiofiles 进行异步文件写入，避免阻塞事件循环
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)
        
        logger.info(f"文件已上传: {file_path} ({format_file_size(len(content))})")
        
        return UploadResponse(
            success=True,
            file_path=str(file_path),
            file_name=safe_filename,
            session_id=session_id,
            sandbox_path=str(sandbox_path)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@router.get("/download/{session_id}/{filename}")
async def download_file(
    session_id: str,
    filename: str,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    下载沙盒中的文件
    
    Args:
        session_id: 会话 ID
        filename: 文件名
        
    Returns:
        FileResponse: 文件下载响应
    """
    # [多租户隔离] 登录用户读取租户沙盒，否则走默认沙盒
    if current_user and hasattr(current_user, 'workspace_id') and current_user.workspace_id:
        workspace_id = current_user.workspace_id
        sandbox_base = Path(FilesystemService.get_workspace_upload_dir(workspace_id)) / "sandbox"
        sandbox_path = sandbox_base / f"session_{session_id}"
    else:
        sandbox_path = get_sandbox_path(session_id)
    file_path = sandbox_path / filename
    
    # 文件存在性检查
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 安全检查：确保文件在沙盒内（防止路径遍历攻击）
    try:
        file_path.resolve().relative_to(sandbox_path.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="非法访问")
    
    # 确定 MIME 类型
    ext = file_path.suffix.lower()
    mime_types = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".csv": "text/csv",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".txt": "text/plain",
    }
    media_type = mime_types.get(ext, "application/octet-stream")
    
    logger.info(f"文件下载: {file_path}")
    
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type
    )


@router.get("/list/{session_id}")
async def list_session_files(
    session_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    列出会话沙盒中的所有文件
    
    Args:
        session_id: 会话 ID
        
    Returns:
        文件列表
    """
    sandbox_path = get_sandbox_path(session_id)
    
    if not sandbox_path.exists():
        return {"files": [], "session_id": session_id}
    
    files = []
    for item in sandbox_path.iterdir():
        if item.is_file():
            stat = item.stat()
            files.append(FileInfo(
                name=item.name,
                size=stat.st_size,
                size_str=format_file_size(stat.st_size),
                created_at=datetime.fromtimestamp(stat.st_ctime).isoformat()
            ))
    
    return {
        "files": [f.model_dump() for f in files],
        "session_id": session_id,
        "sandbox_path": str(sandbox_path)
    }


@router.delete("/session/{session_id}")
async def cleanup_session(
    session_id: str,
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    清理会话沙盒
    
    删除会话的所有文件和目录
    
    Args:
        session_id: 会话 ID
        
    Returns:
        清理结果
    """
    settings = get_settings()
    sandbox_base = Path(settings.sandbox.base_dir)
    session_path = sandbox_base / f"session_{session_id}"
    
    if session_path.exists():
        try:
            shutil.rmtree(session_path)
            logger.info(f"已清理沙盒: {session_path}")
        except Exception as e:
            logger.error(f"清理沙盒失败: {e}")
            raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")
    
    return {"success": True, "session_id": session_id}
