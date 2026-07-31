"""Unified storage service for local and OSS-backed persistent files."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import quote

try:
    import oss2
except ModuleNotFoundError:  # pragma: no cover - optional until OSS backend is enabled
    oss2 = None

from app.config import get_settings
from app.core.utils.storage_path import (
    build_oss_path,
    is_oss_path,
    normalize_storage_key,
    normalize_storage_path,
    parse_oss_path,
    resolve_storage_path,
)


class StorageService:
    """Persistent storage abstraction for local filesystem and Aliyun OSS."""

    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings.storage
        self._oss_settings = settings.oss
        self._local_root = Path(self._settings.local_root).resolve()
        self._temp_dir = Path(self._settings.temp_dir).resolve()
        self._local_root.mkdir(parents=True, exist_ok=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._bucket = None

    @property
    def backend(self) -> str:
        return str(self._settings.backend or "local").strip().lower()

    @property
    def signed_url_ttl_sec(self) -> int:
        return max(60, int(self._settings.signed_url_ttl_sec or 900))

    def persistent_root(self) -> Path:
        return self._local_root

    def ensure_local_parent(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def build_document_object_key(self, workspace_id: str, document_id: str, filename: str) -> str:
        return self._join_key(
            "workspaces",
            str(workspace_id),
            "documents",
            str(document_id),
            self._sanitize_filename(filename),
        )

    def build_document_image_object_key(self, workspace_id: str, file_id: str, image_name: str) -> str:
        return self._join_key(
            "workspaces",
            str(workspace_id),
            "document-images",
            str(file_id),
            self._sanitize_filename(image_name),
        )

    def build_document_image_prefix(self, workspace_id: str, file_id: str) -> str:
        return self._join_key(
            "workspaces",
            str(workspace_id),
            "document-images",
            str(file_id),
        )

    def build_template_object_key(self, workspace_id: str, template_id: str, filename: str) -> str:
        return self._join_key(
            "workspaces",
            str(workspace_id),
            "templates",
            str(template_id),
            self._sanitize_filename(filename),
        )

    def build_local_path(self, object_key: str) -> str:
        return str((self._local_root / normalize_storage_key(object_key)).resolve())

    async def upload_file(self, local_path: str, object_key: str, content_type: Optional[str] = None) -> str:
        normalized_key = normalize_storage_key(object_key)
        if self.backend == "oss":
            return await asyncio.to_thread(self._upload_file_to_oss, local_path, normalized_key, content_type)
        target_path = Path(self.build_local_path(normalized_key))
        self.ensure_local_parent(target_path)
        await asyncio.to_thread(shutil.copy2, local_path, target_path)
        return normalize_storage_path(str(target_path))

    def upload_file_sync(self, local_path: str, object_key: str, content_type: Optional[str] = None) -> str:
        normalized_key = normalize_storage_key(object_key)
        if self.backend == "oss":
            return self._upload_file_to_oss(local_path, normalized_key, content_type)
        target_path = Path(self.build_local_path(normalized_key))
        self.ensure_local_parent(target_path)
        shutil.copy2(local_path, target_path)
        return normalize_storage_path(str(target_path))

    async def replace_from_local_file(
        self,
        storage_path: str,
        local_path: str,
        *,
        content_type: Optional[str] = None,
    ) -> str:
        normalized = normalize_storage_path(storage_path)
        if is_oss_path(normalized):
            bucket, key = parse_oss_path(normalized)
            if bucket != self._oss_settings.bucket:
                raise ValueError(f"unexpected oss bucket: {bucket}")
            return await asyncio.to_thread(self._upload_file_to_oss, local_path, key, content_type)
        source_path = Path(local_path).resolve()
        target_path = Path(resolve_storage_path(normalized)).resolve()
        self.ensure_local_parent(target_path)
        if source_path == target_path:
            return normalize_storage_path(str(target_path))
        await asyncio.to_thread(shutil.copy2, source_path, target_path)
        return normalize_storage_path(str(target_path))

    async def exists(self, storage_path: str) -> bool:
        normalized = normalize_storage_path(storage_path)
        if not normalized:
            return False
        if is_oss_path(normalized):
            return await asyncio.to_thread(self._exists_oss, normalized)
        return Path(resolve_storage_path(normalized)).exists()

    async def get_size(self, storage_path: str) -> int:
        normalized = normalize_storage_path(storage_path)
        if not normalized:
            return 0
        if is_oss_path(normalized):
            return await asyncio.to_thread(self._get_size_oss, normalized)
        local_path = Path(resolve_storage_path(normalized))
        if not local_path.exists():
            return 0
        return int(local_path.stat().st_size)

    async def delete(self, storage_path: str) -> None:
        normalized = normalize_storage_path(storage_path)
        if not normalized:
            return
        if is_oss_path(normalized):
            await asyncio.to_thread(self._delete_oss, normalized)
            return
        local_path = Path(resolve_storage_path(normalized))
        if local_path.exists():
            await asyncio.to_thread(local_path.unlink)

    async def delete_prefix(self, object_key_prefix: str) -> None:
        normalized_prefix = normalize_storage_key(object_key_prefix)
        if not normalized_prefix:
            return
        if self.backend == "oss":
            await asyncio.to_thread(self._delete_prefix_oss, normalized_prefix)
            return
        prefix_path = (self._local_root / normalized_prefix).resolve()
        if prefix_path.exists():
            await asyncio.to_thread(shutil.rmtree, prefix_path, True)

    def delete_prefix_sync(self, object_key_prefix: str) -> None:
        normalized_prefix = normalize_storage_key(object_key_prefix)
        if not normalized_prefix:
            return
        if self.backend == "oss":
            self._delete_prefix_oss(normalized_prefix)
            return
        prefix_path = (self._local_root / normalized_prefix).resolve()
        if prefix_path.exists():
            shutil.rmtree(prefix_path, ignore_errors=True)

    async def generate_signed_url(
        self,
        storage_path: str,
        *,
        filename: Optional[str] = None,
        inline: bool = True,
        expires: Optional[int] = None,
        cache_control: Optional[str] = None,
    ) -> Optional[str]:
        normalized = normalize_storage_path(storage_path)
        if not is_oss_path(normalized):
            return None
        return await asyncio.to_thread(
            self._generate_signed_url_oss,
            normalized,
            filename,
            inline,
            expires or self.signed_url_ttl_sec,
            cache_control,
        )

    @asynccontextmanager
    async def materialize(self, storage_path: str, *, suffix: str = "", filename: Optional[str] = None) -> AsyncIterator[str]:
        normalized = normalize_storage_path(storage_path)
        if not normalized:
            raise FileNotFoundError("empty storage path")
        if not is_oss_path(normalized):
            yield resolve_storage_path(normalized)
            return

        effective_suffix = suffix or Path(filename or "").suffix
        fd, temp_path = tempfile.mkstemp(
            suffix=effective_suffix,
            prefix="storage_",
            dir=str(self._temp_dir),
        )
        os.close(fd)
        try:
            await asyncio.to_thread(self._download_to_path_oss, normalized, temp_path)
            yield temp_path
        finally:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass

    def _bucket_client(self) -> oss2.Bucket:
        if self.backend != "oss":
            raise RuntimeError("oss bucket requested when storage backend is not oss")
        if oss2 is None:
            raise RuntimeError("oss2 is not installed but STORAGE_BACKEND=oss")
        if not self._oss_settings.is_configured:
            raise RuntimeError("oss backend is enabled but credentials are not fully configured")
        if self._bucket is None:
            endpoint = str(self._oss_settings.endpoint).strip()
            if endpoint and not endpoint.startswith(("http://", "https://")):
                endpoint = f"https://{endpoint}"
            if self._oss_settings.security_token:
                auth = oss2.StsAuth(
                    self._oss_settings.access_key_id,
                    self._oss_settings.access_key_secret,
                    self._oss_settings.security_token,
                )
            else:
                auth = oss2.Auth(
                    self._oss_settings.access_key_id,
                    self._oss_settings.access_key_secret,
                )
            self._bucket = oss2.Bucket(
                auth,
                endpoint,
                self._oss_settings.bucket,
                region=self._oss_settings.region or None,
            )
        return self._bucket

    def _upload_file_to_oss(self, local_path: str, object_key: str, content_type: Optional[str]) -> str:
        headers = None
        if content_type:
            headers = {"Content-Type": content_type}
        else:
            guessed_type, _ = mimetypes.guess_type(local_path)
            if guessed_type:
                headers = {"Content-Type": guessed_type}
        self._run_oss_with_retry(
            "upload",
            lambda: self._bucket_client().put_object_from_file(object_key, local_path, headers=headers),
        )
        return build_oss_path(self._oss_settings.bucket, object_key)

    def _download_to_path_oss(self, storage_path: str, local_path: str) -> None:
        _, key = parse_oss_path(storage_path)
        self._run_oss_with_retry(
            "download",
            lambda: self._bucket_client().get_object_to_file(key, local_path),
        )

    def _exists_oss(self, storage_path: str) -> bool:
        _, key = parse_oss_path(storage_path)
        try:
            self._run_oss_with_retry("head", lambda: self._bucket_client().head_object(key))
            return True
        except oss2.exceptions.NoSuchKey:
            return False
        except oss2.exceptions.NotFound:
            return False

    def _get_size_oss(self, storage_path: str) -> int:
        _, key = parse_oss_path(storage_path)
        result = self._run_oss_with_retry("head", lambda: self._bucket_client().head_object(key))
        return int(getattr(result, "content_length", 0) or 0)

    def _delete_oss(self, storage_path: str) -> None:
        _, key = parse_oss_path(storage_path)
        self._run_oss_with_retry("delete", lambda: self._bucket_client().delete_object(key))

    def _delete_prefix_oss(self, object_key_prefix: str) -> None:
        bucket = self._bucket_client()
        for obj in oss2.ObjectIterator(bucket, prefix=object_key_prefix):
            self._run_oss_with_retry("delete", lambda key=obj.key: bucket.delete_object(key))

    def _run_oss_with_retry(self, operation: str, func):
        max_retries = max(1, int(getattr(self._oss_settings, "max_retries", 3) or 3))
        backoff = max(0.1, float(getattr(self._oss_settings, "retry_backoff_sec", 0.6) or 0.6))
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                return func()
            except Exception as exc:
                last_exc = exc
                if attempt >= max_retries or not self._is_retryable_oss_error(exc):
                    raise
                time.sleep(backoff * attempt)
        if last_exc is not None:
            raise last_exc
        return None

    @staticmethod
    def _is_retryable_oss_error(exc: Exception) -> bool:
        name = exc.__class__.__name__.lower()
        text = str(exc).lower()
        retryable_markers = (
            "requesterror",
            "connection",
            "connect timeout",
            "read timeout",
            "timed out",
            "temporarily unavailable",
            "temporary failure",
            "nameresolutionerror",
            "failed to resolve",
            "max retries exceeded",
            "502",
            "503",
            "504",
        )
        return any(marker in name or marker in text for marker in retryable_markers)

    def _generate_signed_url_oss(
        self,
        storage_path: str,
        filename: Optional[str],
        inline: bool,
        expires: int,
        cache_control: Optional[str],
    ) -> str:
        _, key = parse_oss_path(storage_path)
        params = {}
        safe_filename = self._sanitize_filename(filename) if filename else ""
        if safe_filename:
            disposition_type = "inline" if inline else "attachment"
            encoded_name = quote(safe_filename)
            params["response-content-disposition"] = (
                f"{disposition_type}; filename*=UTF-8''{encoded_name}"
            )
        if cache_control:
            params["response-cache-control"] = str(cache_control).strip()
        try:
            return self._bucket_client().sign_url(
                "GET",
                key,
                expires,
                params=params,
                slash_safe=True,
            )
        except oss2.exceptions.NoSuchKey:
            # 文件不存在，返回空字符串让上层返回 404
            return ""

    def _join_key(self, *parts: str) -> str:
        prefix = normalize_storage_key(self._oss_settings.key_prefix)
        segments = [normalize_storage_key(part) for part in parts if normalize_storage_key(part)]
        if prefix:
            segments.insert(0, prefix)
        return "/".join(segments)

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        raw = Path(filename or "file").name
        # 过滤 URL 特殊字符：# 在 URL 中表示锚点，会导致路径被截断
        sanitized = raw.replace("..", "").replace("/", "_").replace("\\", "_").replace("#", "_").strip()
        return sanitized or "file"


_storage_service: Optional[StorageService] = None


def get_storage_service() -> StorageService:
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService()
    return _storage_service
