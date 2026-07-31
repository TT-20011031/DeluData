"""
图片存储服务 (v2.1 - PyMuPDF 轻量版)

使用 PyMuPDF (fitz) 直接从 PDF 流中提取图片对象，
速度极快（毫秒级/页），无需 GPU。

职责：
1. 从 PDF/Word 提取嵌入图片
2. 存储到本地文件系统
3. pHash 去重和小图过滤
"""
import os
import uuid
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
from io import BytesIO

import base64

import fitz  # PyMuPDF
import imagehash
from PIL import Image, ImageOps, ImageStat

from app.config import get_settings
from app.core.storage.service import get_storage_service
from app.core.utils.storage_path import build_oss_path, normalize_storage_path

logger = logging.getLogger(__name__)


@dataclass
class ExtractedImage:
    """提取的图片数据结构"""
    id: str                    # 图片唯一ID
    local_path: str            # 本地存储路径
    page_number: int           # 所在页码
    bbox: Dict[str, float]     # 边界框 {x1, y1, x2, y2}
    phash: str                 # 感知哈希（用于去重）
    width: int                 # 宽度
    height: int                # 高度
    storage_path: str = ""     # 持久化后的存储路径


def _fix_inverted_image(img: Image.Image) -> Image.Image:
    """
    检测并修复反色图片（黑底白字→白底黑字）。

    原理：正常文档页面以白色为主，平均亮度应 > 180。
    若平均亮度 < 100，说明 PDF 内部存储的色彩空间或 Decode 数组
    导致 PyMuPDF 提取/渲染时产生反色，需用 ImageOps.invert 修正。
    """
    try:
        grayscale = img.convert("L")
        mean_brightness = ImageStat.Stat(grayscale).mean[0]
        if mean_brightness < 100:
            logger.info(
                "[ImageService] 检测到反色图片 (mean_brightness=%.1f)，自动修正",
                mean_brightness,
            )
            if img.mode == "RGBA":
                r, g, b, a = img.split()
                rgb = Image.merge("RGB", (r, g, b))
                rgb = ImageOps.invert(rgb)
                rgb.putalpha(a)
                return rgb
            if img.mode != "RGB":
                img = img.convert("RGB")
            return ImageOps.invert(img)
    except Exception as e:
        logger.warning("[ImageService] 反色检测失败，跳过: %s", e)
    return img


class ImageService:
    """
    图片存储服务 (PyMuPDF 极速版)
    
    特点：
    - 极速：毫秒级/页（vs unstructured hi_res 的 5-30秒/页）
    - 轻量：纯 CPU，无需 GPU
    - 无损：直接提取 PDF 内嵌图片对象
    
    局限：
    - 无法处理扫描件（图片中的图片）
    """
    
    # 过滤小图标的阈值
    MIN_IMAGE_SIZE = 50    # 最小边长（像素）
    MIN_IMAGE_AREA = 5000  # 最小面积（平方像素）
    
    def __init__(self):
        settings = get_settings()
        self.storage_root = Path(settings.app.data_dir) / "knowledge" / "images"
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._storage_service = get_storage_service()

    def _persist_image(self, workspace_id: str, file_id: str, image_name: str, local_path: str) -> str:
        object_key = self._storage_service.build_document_image_object_key(
            workspace_id=workspace_id,
            file_id=file_id,
            image_name=image_name,
        )
        return self._storage_service.upload_file_sync(
            local_path,
            object_key,
            content_type="image/png",
        )
    
    def extract_images_from_pdf(
        self,
        pdf_path: str,
        file_id: str,
        workspace_id: str,
    ) -> List[ExtractedImage]:
        """
        从 PDF 提取图片 (PyMuPDF 极速版)
        
        使用 fitz 直接从 PDF 二进制流中提取图片对象，
        速度比 unstructured hi_res 快 100-1000 倍。
        
        Args:
            pdf_path: PDF 文件路径
            file_id: 文件唯一 ID
        
        Returns:
            提取的图片列表
        """
        # 创建文件专属目录

        file_image_dir = self.storage_root / file_id
        file_image_dir.mkdir(parents=True, exist_ok=True)
        
        extracted = []
        seen_hashes = set()
        
        try:
            doc = fitz.open(pdf_path)
            
            # [v2.7] 使用 get_image_info 获取每页实际渲染的图片
            # 这样可以正确处理 PDF 共享图片资源的情况
            xref_to_page: dict = {}
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_infos = page.get_image_info(xrefs=True)
                for info in image_infos:
                    xref = info.get('xref', 0)
                    bbox = info.get('bbox', [])
                    # 只记录有实际渲染位置的图片
                    if xref and bbox and xref not in xref_to_page:
                        xref_to_page[xref] = page_num + 1
            
            # 遍历提取图片，使用实际渲染页码
            processed_xrefs = set()
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)
                
                for img_info in image_list:
                    xref = img_info[0]
                    
                    if xref in processed_xrefs:
                        continue
                    processed_xrefs.add(xref)
                    
                    try:
                        base_image = doc.extract_image(xref)
                        if not base_image:
                            continue
                            
                        image_bytes = base_image["image"]
                        img = Image.open(BytesIO(image_bytes))
                        
                        if img.width < self.MIN_IMAGE_SIZE or img.height < self.MIN_IMAGE_SIZE:
                            continue
                        if img.width * img.height < self.MIN_IMAGE_AREA:
                            continue
                        
                        phash = str(imagehash.phash(img))
                        if phash in seen_hashes:
                            continue
                        seen_hashes.add(phash)
                        
                        # 使用实际渲染的页码
                        actual_page = xref_to_page.get(xref, page_num + 1)
                        
                        img_id = f"{len(extracted) + 1:03d}"
                        
                        if img.mode in ("CMYK", "P", "LA", "RGBA"):
                            img = img.convert("RGB")
                        
                        img = _fix_inverted_image(img)
                        
                        save_path = file_image_dir / f"{img_id}.png"
                        img.save(save_path, "PNG")
                        
                        extracted_image = ExtractedImage(
                            id=img_id,
                            local_path=str(save_path),
                            page_number=actual_page,
                            bbox={"x1": 0, "y1": 0, "x2": img.width, "y2": img.height},
                            phash=phash,
                            width=img.width,
                            height=img.height
                        )
                        extracted_image.storage_path = self._persist_image(
                            workspace_id=workspace_id,
                            file_id=file_id,
                            image_name=f"{img_id}.png",
                            local_path=str(save_path),
                        )
                        extracted.append(extracted_image)
                        
                    except Exception as e:
                        logger.warning(f"[ImageService] 提取图片失败 (page {page_num + 1}, xref {xref}): {e}")
                        continue
            
            doc.close()
            
        except Exception as e:
            logger.error(f"[ImageService] 打开 PDF 失败: {e}")
            return []
        
        logger.info(f"[ImageService] 从 PDF 提取 {len(extracted)} 张有效图片 (极速模式)")
        return extracted
    
    def extract_images_from_docx(
        self,
        docx_path: str,
        file_id: str,
        workspace_id: str,
    ) -> List[ExtractedImage]:
        """
        从 Word 文档提取图片
        
        使用 python-docx 提取嵌入图片。
        
        Args:
            docx_path: Word 文件路径
            file_id: 文件唯一 ID
        
        Returns:
            提取的图片列表
        """
        from docx import Document
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        
        file_image_dir = self.storage_root / file_id
        file_image_dir.mkdir(parents=True, exist_ok=True)
        
        extracted = []
        seen_hashes = set()
        
        try:
            doc = Document(docx_path)
            
            for rel in doc.part.rels.values():
                if rel.reltype == RT.IMAGE:
                    try:
                        image_data = rel.target_part.blob
                        img = Image.open(BytesIO(image_data))
                        
                        # 过滤小图
                        if img.width < self.MIN_IMAGE_SIZE or img.height < self.MIN_IMAGE_SIZE:
                            continue
                        if img.width * img.height < self.MIN_IMAGE_AREA:
                            continue
                        
                        # 去重
                        phash = str(imagehash.phash(img))
                        if phash in seen_hashes:
                            continue
                        seen_hashes.add(phash)
                        
                        # [v2.4] 简化图片 ID：使用纯数字序号 (001, 002...)
                        img_id = f"{len(extracted) + 1:03d}"
                        
                        # 转换模式
                        if img.mode in ("CMYK", "P", "LA", "RGBA"):
                            img = img.convert("RGB")
                        
                        save_path = file_image_dir / f"{img_id}.png"
                        img.save(save_path, "PNG")
                        
                        extracted_image = ExtractedImage(
                            id=img_id,
                            local_path=str(save_path),
                            page_number=1,  # Word 没有页码概念
                            bbox={"x1": 0, "y1": 0, "x2": img.width, "y2": img.height},
                            phash=phash,
                            width=img.width,
                            height=img.height
                        )
                        extracted_image.storage_path = self._persist_image(
                            workspace_id=workspace_id,
                            file_id=file_id,
                            image_name=f"{img_id}.png",
                            local_path=str(save_path),
                        )
                        extracted.append(extracted_image)
                        
                    except Exception as e:
                        logger.warning(f"[ImageService] 提取 Word 图片失败: {e}")
                        continue
                        
        except Exception as e:
            logger.error(f"[ImageService] 打开 Word 失败: {e}")
            return []
        
        logger.info(f"[ImageService] 从 Word 提取 {len(extracted)} 张图片")
        return extracted
    
    def extract_images(self, file_path: str, file_id: str, workspace_id: str) -> List[ExtractedImage]:
        """
        自动识别文件类型并提取图片
        
        Args:
            file_path: 文件路径
            file_id: 文件唯一 ID
        
        Returns:
            提取的图片列表
        """
        suffix = Path(file_path).suffix.lower()
        
        if suffix == ".pdf":
            return self.extract_images_from_pdf(file_path, file_id, workspace_id)
        elif suffix == ".docx":
            return self.extract_images_from_docx(file_path, file_id, workspace_id)
        else:
            # MD/TXT 等纯文本格式无嵌入图片
            return []
    
    async def extract_images_async(self, file_path: str, file_id: str, workspace_id: str) -> List[ExtractedImage]:
        """
        异步提取图片（推荐用于大文件）
        
        将同步的图片提取操作放入线程池执行，
        避免超大 PDF（500页+）阻塞 FastAPI 事件循环。
        """
        import asyncio
        return await asyncio.to_thread(self.extract_images, file_path, file_id, workspace_id)
    
    def get_image_url(self, image_id: str, file_id: str) -> str:
        """
        获取图片访问 URL
        
        Args:
            image_id: 图片 ID
            file_id: 所属文件 ID
        
        Returns:
            图片访问 URL
        """
        return f"/api/knowledge/images/{file_id}/{image_id}.png"
    
    def get_image_path(self, image_id: str, file_id: str) -> Optional[Path]:
        """
        获取图片本地路径 ([v2.4] 增强兼容性)
        
        支持新旧两种 ID 格式：
        - 新格式: 001, 002, 003 (纯数字序号)
        - 旧格式: img_abc12345 (uuid 前缀)
        
        Args:
            image_id: 图片 ID (新格式或旧格式)
            file_id: 所属文件 ID
        
        Returns:
            图片本地路径，不存在则返回 None
        """
        base_dir = self.storage_root / file_id
        
        # 1. 优先尝试直接匹配（新格式 001.png 或传入的就是完整 ID）
        path = base_dir / f"{image_id}.png"
        if path.exists():
            return path
        
        # 2. 兼容旧格式：如果传入的是数字但文件系统里是 img_xxx
        if not image_id.startswith("img_"):
            legacy_path = base_dir / f"img_{image_id}.png"
            if legacy_path.exists():
                return legacy_path
        
        return None
    
    def generate_signed_url(
        self, 
        file_id: str, 
        image_id: str, 
        ttl: int = 3600
    ) -> str:
        """
        生成带签名的图片访问 URL
        
        安全特性：
        1. 过期时间控制
        2. HMAC 签名防篡改
        3. URL 安全 Base64 编码
        
        Args:
            file_id: 文件 ID
            image_id: 图片 ID
            ttl: 过期时间（秒），默认 1 小时
        
        Returns:
            签名后的 URL
        """
        import time
        import hmac
        import hashlib
        import base64
        
        settings = get_settings()
        expires = int(time.time()) + ttl
        
        # 生成签名（使用 SECRET_KEY）
        message = f"{file_id}:{image_id}:{expires}"
        signature = hmac.new(
            settings.app.secret_key.encode(),
            message.encode(),
            hashlib.sha256
        ).digest()
        
        # URL 安全 Base64 编码：
        # 这里截断到 16 字节（128-bit）用于缩短 URL，内部短期签名场景安全性可接受。
        sig_b64 = base64.urlsafe_b64encode(signature[:16]).decode().rstrip('=')
        
        path = f"/api/knowledge/images/{file_id}/{image_id}.png?expires={expires}&sig={sig_b64}"
        return self._build_absolute_url(path)
    
    def verify_signature(
        self, 
        file_id: str, 
        image_id: str, 
        expires: int, 
        signature: str
    ) -> bool:
        """
        验证签名 URL
        
        使用 hmac.compare_digest 防止时间攻击
        
        Args:
            file_id: 文件 ID
            image_id: 图片 ID
            expires: 过期时间戳
            signature: URL 中的签名
        
        Returns:
            是否验证通过
        """
        import time
        import hmac
        import hashlib
        import base64
        
        # 检查是否过期
        if int(time.time()) > expires:
            logger.warning(f"[ImageService] 签名已过期: {file_id}/{image_id}")
            return False
        
        settings = get_settings()
        
        # 重新计算签名
        message = f"{file_id}:{image_id}:{expires}"
        expected_sig = hmac.new(
            settings.app.secret_key.encode(),
            message.encode(),
            hashlib.sha256
        ).digest()
        
        expected_b64 = base64.urlsafe_b64encode(expected_sig[:16]).decode().rstrip('=')
        
        # 使用 compare_digest 防止时间攻击
        return hmac.compare_digest(expected_b64, signature)

    def render_pdf_page_image(
        self,
        pdf_path: str,
        file_id: str,
        page_number: int,
        dpi: Optional[int] = None,
    ) -> Optional[str]:
        """
        按需渲染 PDF 页图并缓存，返回 image_id（page_XXXX）。
        """
        settings = get_settings()
        render_dpi = int(dpi or settings.rag.page_image_dpi)
        if page_number <= 0:
            return None

        file_image_dir = self.storage_root / file_id
        file_image_dir.mkdir(parents=True, exist_ok=True)

        image_id = f"page_{page_number:04d}"
        image_path = file_image_dir / f"{image_id}.png"
        if image_path.exists():
            return image_id

        try:
            with fitz.open(str(pdf_path)) as doc:
                if page_number > len(doc):
                    return None
                page = doc[page_number - 1]
                pixmap = page.get_pixmap(dpi=render_dpi, alpha=False)
                img = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                img = _fix_inverted_image(img)
                img.save(str(image_path), "PNG")
            return image_id
        except Exception as e:
            logger.warning(
                "[ImageService] 渲染 PDF 页图失败: file_id=%s page=%s err=%s",
                file_id,
                page_number,
                e,
            )
            return None

    async def render_pdf_page_image_async(
        self,
        pdf_path: str,
        file_id: str,
        page_number: int,
        dpi: Optional[int] = None,
    ) -> Optional[str]:
        import asyncio

        return await asyncio.to_thread(
            self.render_pdf_page_image,
            pdf_path,
            file_id,
            page_number,
            dpi,
        )

    def build_signed_page_image_url(
        self,
        file_id: str,
        page_number: int,
        ttl: Optional[int] = None,
    ) -> str:
        settings = get_settings()
        image_id = f"page_{page_number:04d}"
        return self.generate_signed_url(
            file_id=file_id,
            image_id=image_id,
            ttl=int(ttl or settings.rag.mm_image_url_ttl_sec),
        )

    def _build_absolute_url(self, path: str) -> str:
        settings = get_settings()
        base = (settings.app.public_api_base_url or "").strip()
        if not base:
            return path
        return f"{base.rstrip('/')}/{path.lstrip('/')}"

    async def ensure_image_storage_path_async(
        self,
        *,
        workspace_id: str,
        file_id: str,
        image_id: str,
    ) -> str | None:
        """
        Ensure a rendered or extracted image has a persistent storage path.

        When the storage backend is OSS, on-demand rendered page images are uploaded
        once and then reused through OSS signed URLs.
        """
        local_path = self.get_image_path(image_id, file_id)

        storage_service = self._storage_service
        if storage_service.backend != "oss":
            if local_path is None or not local_path.exists():
                return None
            return normalize_storage_path(str(local_path))

        clean_workspace_id = str(workspace_id or "").strip()
        if not clean_workspace_id:
            return None

        settings = get_settings()
        object_key = storage_service.build_document_image_object_key(
            workspace_id=clean_workspace_id,
            file_id=file_id,
            image_name=f"{image_id}.png",
        )
        storage_path = build_oss_path(settings.oss.bucket, object_key)
        if local_path is not None and local_path.exists():
            if not await storage_service.exists(storage_path):
                storage_path = await storage_service.upload_file(
                    str(local_path),
                    object_key,
                    content_type="image/png",
                )
            return storage_path

        if await storage_service.exists(storage_path):
            return storage_path

        return None

    async def get_image_base64_from_storage_path_async(self, storage_path: str) -> str | None:
        """
        从持久化存储路径读取图片并转为 data URI。

        允许在本地缓存缺失时直接复用 OSS 中已存在的页图，避免回源下载整份 PDF。
        """
        normalized_storage_path = normalize_storage_path(storage_path)
        if not normalized_storage_path:
            return None

        suffix = Path(normalized_storage_path).suffix or ".png"
        try:
            async with self._storage_service.materialize(
                normalized_storage_path,
                suffix=suffix,
                filename=Path(normalized_storage_path).name,
            ) as local_path:
                raw = await asyncio.to_thread(Path(local_path).read_bytes)
        except Exception as e:
            logger.warning("[ImageService] 读取持久化图片失败: %s", e)
            return None

        try:
            b64 = base64.b64encode(raw).decode()
            return f"data:image/png;base64,{b64}"
        except Exception as e:
            logger.warning("[ImageService] 持久化图片 base64 编码失败: %s", e)
            return None

    async def get_multimodal_image_url_async(
        self,
        *,
        workspace_id: str,
        file_id: str,
        image_id: str,
        ttl: Optional[int] = None,
    ) -> str | None:
        """
        Build the URL used by multimodal model inputs.

        OSS backend uses direct OSS signed URLs so the model can fetch the image
        without routing back through the application.
        """
        settings = get_settings()
        effective_ttl = int(ttl or settings.rag.mm_image_url_ttl_sec)
        storage_path = await self.ensure_image_storage_path_async(
            workspace_id=workspace_id,
            file_id=file_id,
            image_id=image_id,
        )
        if not storage_path:
            return None

        if self._storage_service.backend == "oss":
            return await self._storage_service.generate_signed_url(
                storage_path,
                filename=f"{image_id}.png",
                inline=True,
                expires=effective_ttl,
            )

        return self.generate_signed_url(
            file_id=file_id,
            image_id=image_id,
            ttl=effective_ttl,
        )
    
    def get_image_base64(self, file_id: str, image_id: str) -> str | None:
        """
        读取图片文件并返回 data URI 格式的 base64 字符串。

        用于开发环境直接将图片内联传给 VLM，无需公网 URL。

        Returns:
            "data:image/png;base64,xxxx" 或 None
        """
        path = self.get_image_path(image_id, file_id)
        if path is None or not path.exists():
            return None
        try:
            raw = path.read_bytes()
            b64 = base64.b64encode(raw).decode()
            return f"data:image/png;base64,{b64}"
        except Exception as e:
            logger.warning("[ImageService] base64 编码失败: %s", e)
            return None

    def delete_file_images(self, file_id: str, workspace_id: Optional[str] = None) -> bool:
        """
        删除文件关联的所有图片
        
        Args:
            file_id: 文件 ID
        
        Returns:
            是否删除成功
        """
        deleted_any = False
        if workspace_id and get_settings().storage.backend.lower() == "oss":
            try:
                prefix = self._storage_service.build_document_image_prefix(workspace_id, file_id)
                self._storage_service.delete_prefix_sync(prefix)
                deleted_any = True
            except Exception as e:
                logger.warning(f"[ImageService] 鍒犻櫎 OSS 鍥剧墖鍓嶇紑澶辫触: {file_id}, {e}")

        file_image_dir = self.storage_root / file_id
        if file_image_dir.exists():
            import shutil
            shutil.rmtree(file_image_dir)
            logger.info(f"[ImageService] 已删除图片目录: {file_id}")
            return True
        return deleted_any


# 单例
_image_service: Optional[ImageService] = None


def get_image_service() -> ImageService:
    """获取图片服务单例"""
    global _image_service
    if _image_service is None:
        _image_service = ImageService()
    return _image_service
