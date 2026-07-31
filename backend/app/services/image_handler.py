"""
图片处理服务 (Image Handler)

职责：处理聊天图片的上传和保存
"""
import uuid
import logging
from pathlib import Path
from typing import Tuple

import aiofiles
from fastapi import UploadFile, HTTPException

from app.config import get_settings

logger = logging.getLogger(__name__)

# 允许的图片类型
ALLOWED_IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/webp'}
# 最大文件大小 (10MB)
MAX_IMAGE_SIZE = 10 * 1024 * 1024


class ImageHandler:
    """
    图片处理服务
    
    负责聊天图片的验证、保存和路径管理
    """
    
    async def save_chat_image(
        self,
        file: UploadFile,
        session_id: str,
    ) -> Tuple[str, str]:
        """
        保存聊天图片到会话沙盒目录
        
        Args:
            file: 上传的文件对象
            session_id: 会话 ID
            
        Returns:
            (相对URL, 文件名) 元组
            
        Raises:
            HTTPException: 文件类型或大小不符合要求
        """
        # 验证文件类型
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的图片类型: {file.content_type}"
            )
        
        # 读取并验证文件大小
        content = await file.read()
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(
                status_code=400,
                detail="图片大小超过 10MB 限制"
            )
        
        # 创建沙盒目录
        settings = get_settings()
        sandbox_path = Path(settings.sandbox.base_dir) / f"session_{session_id}" / "images"
        sandbox_path.mkdir(parents=True, exist_ok=True)
        
        # 生成安全文件名
        safe_filename = f"{uuid.uuid4().hex[:8]}_{file.filename}"
        image_path = sandbox_path / safe_filename
        
        # 异步写入文件
        async with aiofiles.open(image_path, 'wb') as f:
            await f.write(content)
        
        logger.info(f"图片上传成功: {image_path}")
        
        # [修复] 返回绝对路径，确保后续请求无论使用什么 session_id 都能定位图片
        # 因为图片上传和消息发送可能使用不同的 session_id
        absolute_url = str(image_path.absolute())
        original_name = file.filename or "unknown"
        
        return absolute_url, original_name

