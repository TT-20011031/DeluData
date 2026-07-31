"""
临时产物存储服务

管理 AI 生成的图表（HTML）和文档（Excel/Word）临时产物。
存储路径：{sandbox.base_dir}/temp_artifacts/{workspace_id}/{user_id}/{artifact_id}/
TTL：默认 3600 秒（1 小时），用户活跃访问时滑动延期。

设计原则：
- [Security] 严格校验路径合法性，防止路径穿越攻击
- [Async First] 全异步文件 I/O（aiofiles）
- [Zero Tech Debt] cleanup_expired 不仅删 meta，也递归删除目录
"""
import json
import logging
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiofiles

from app.config import get_settings

logger = logging.getLogger(__name__)

TTL_SECONDS = 3600  # 1 小时
TTL_EXTEND_WRITE_THRESHOLD_SECONDS = 600  # 剩余不足 10 分钟时才续期并写盘


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _expires_at(ttl: int = TTL_SECONDS) -> str:
    return (datetime.utcnow() + timedelta(seconds=ttl)).isoformat() + "Z"


def _is_expired(expires_at_str: str) -> bool:
    try:
        exp = datetime.fromisoformat(expires_at_str.rstrip("Z"))
        return datetime.utcnow() > exp
    except (ValueError, TypeError):
        return True


def _seconds_until_expiry(expires_at_str: str) -> int:
    """返回距离过期的秒数；无法解析时返回 -1。"""
    try:
        exp = datetime.fromisoformat(expires_at_str.rstrip("Z"))
    except (ValueError, TypeError):
        return -1
    return int((exp - datetime.utcnow()).total_seconds())


def _infer_file_kind(file_name: str) -> Optional[str]:
    lower_name = str(file_name or "").strip().lower()
    if lower_name.endswith((".xlsx", ".xls", ".csv")):
        return "excel"
    if lower_name.endswith((".docx", ".doc")):
        return "word"
    return None


class TempArtifactService:
    """
    临时产物存储服务

    提供图表/文档产物的持久化存储、列表查询、内容读取和过期清理。
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._base_dir = Path(settings.sandbox.base_dir) / "temp_artifacts"

    # ========== 内部工具 ==========

    def _user_dir(self, workspace_id: str, user_id: str) -> Path:
        """返回用户专属目录（已保证 workspace/user 两级隔离）。"""
        return self._base_dir / workspace_id / user_id

    def _artifact_dir(self, workspace_id: str, user_id: str, artifact_id: str) -> Path:
        return self._user_dir(workspace_id, user_id) / artifact_id

    def _assert_safe_path(self, path: Path, user_dir: Path) -> None:
        """路径合法性校验：目标路径必须位于用户目录内，防止路径穿越攻击。"""
        try:
            path.resolve().relative_to(user_dir.resolve())
        except ValueError:
            raise PermissionError(f"路径越界: {path}")

    def _resolve_within_base(self, path: Path) -> Path:
        """
        校验并返回位于 temp_artifacts 基础目录下的绝对路径。
        """
        resolved = path.resolve()
        try:
            resolved.relative_to(self._base_dir.resolve())
        except ValueError:
            raise PermissionError(f"路径越界: {resolved}")
        return resolved

    async def _read_meta(self, artifact_dir: Path) -> Optional[dict]:
        meta_path = artifact_dir / "meta.json"
        if not meta_path.exists():
            return None
        try:
            async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
                return json.loads(await f.read())
        except Exception as e:
            logger.warning("[TempArtifact] 读取 meta 失败: %s, %s", meta_path, e)
            return None

    async def _write_meta(self, artifact_dir: Path, meta: dict) -> None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        meta_path = artifact_dir / "meta.json"
        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta, ensure_ascii=False, indent=2))

    # ========== 公开接口 ==========

    async def save_chart(
        self,
        workspace_id: str,
        user_id: str,
        session_id: str,
        title: str,
        html_content: str,
    ) -> dict:
        """
        将图表 HTML 保存到临时存储。

        Returns:
            产物 metadata dict
        """
        artifact_id = str(uuid.uuid4())
        artifact_dir = self._artifact_dir(workspace_id, user_id, artifact_id)
        user_dir = self._user_dir(workspace_id, user_id)
        self._assert_safe_path(artifact_dir, user_dir)

        artifact_dir.mkdir(parents=True, exist_ok=True)
        content_path = artifact_dir / "content.html"
        async with aiofiles.open(content_path, "w", encoding="utf-8") as f:
            await f.write(html_content)

        meta = {
            "id": artifact_id,
            "type": "chart",
            "title": title,
            "session_id": session_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "created_at": _now_iso(),
            "expires_at": _expires_at(),
            "content_file": "content.html",
        }
        await self._write_meta(artifact_dir, meta)
        logger.info("[TempArtifact] 图表已保存: artifact_id=%s, workspace=%s, user=%s", artifact_id, workspace_id, user_id)
        return meta

    async def save_doc(
        self,
        workspace_id: str,
        user_id: str,
        session_id: str,
        title: str,
        download_url: str,
        file_name: str,
        file_kind: Optional[str] = None,
    ) -> dict:
        """
        将文档产物元数据保存到临时存储（文件本身已在 sandbox，此处只存 meta）。

        Returns:
            产物 metadata dict
        """
        artifact_id = str(uuid.uuid4())
        artifact_dir = self._artifact_dir(workspace_id, user_id, artifact_id)
        user_dir = self._user_dir(workspace_id, user_id)
        self._assert_safe_path(artifact_dir, user_dir)

        meta = {
            "id": artifact_id,
            "type": "doc",
            "file_kind": file_kind or _infer_file_kind(file_name),
            "title": title,
            "session_id": session_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "created_at": _now_iso(),
            "expires_at": _expires_at(),
            "download_url": download_url,
            "file_name": file_name,
        }
        await self._write_meta(artifact_dir, meta)
        logger.info("[TempArtifact] 文档已保存: artifact_id=%s, workspace=%s, user=%s", artifact_id, workspace_id, user_id)
        return meta

    async def list_artifacts(
        self, workspace_id: str, user_id: str, extend_ttl: bool = True
    ) -> list[dict]:
        """
        列出用户所有未过期产物（按创建时间倒序）。

        Args:
            extend_ttl: 如果 True，对每个未过期产物滑动延期（活跃访问策略）
        """
        user_dir = self._user_dir(workspace_id, user_id)
        if not user_dir.exists():
            return []

        artifacts = []
        for artifact_dir in user_dir.iterdir():
            if not artifact_dir.is_dir():
                continue
            meta = await self._read_meta(artifact_dir)
            if not meta:
                continue
            if _is_expired(meta.get("expires_at", "")):
                continue
            if extend_ttl:
                # 仅在临近过期时续期，避免每次 list 都写盘
                remaining_seconds = _seconds_until_expiry(meta.get("expires_at", ""))
                if remaining_seconds < TTL_EXTEND_WRITE_THRESHOLD_SECONDS:
                    meta["expires_at"] = _expires_at()
                    await self._write_meta(artifact_dir, meta)
            artifacts.append(meta)

        artifacts.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return artifacts

    async def get_artifact(
        self, workspace_id: str, user_id: str, artifact_id: str
    ) -> Optional[dict]:
        """获取单个产物 metadata，过期则返回 None。"""
        artifact_dir = self._artifact_dir(workspace_id, user_id, artifact_id)
        user_dir = self._user_dir(workspace_id, user_id)
        self._assert_safe_path(artifact_dir, user_dir)

        meta = await self._read_meta(artifact_dir)
        if not meta or _is_expired(meta.get("expires_at", "")):
            return None
        return meta

    async def get_chart_content(
        self, workspace_id: str, user_id: str, artifact_id: str
    ) -> Optional[str]:
        """读取图表 HTML 内容，路径合法性校验后返回。"""
        artifact_dir = self._artifact_dir(workspace_id, user_id, artifact_id)
        user_dir = self._user_dir(workspace_id, user_id)
        self._assert_safe_path(artifact_dir, user_dir)

        content_path = (artifact_dir / "content.html").resolve()
        self._assert_safe_path(content_path, user_dir.resolve())

        if not content_path.exists():
            return None
        async with aiofiles.open(content_path, "r", encoding="utf-8") as f:
            return await f.read()

    async def delete_artifact(
        self, workspace_id: str, user_id: str, artifact_id: str
    ) -> bool:
        """删除产物目录（含 meta.json 和 content.html）。"""
        artifact_dir = self._artifact_dir(workspace_id, user_id, artifact_id)
        user_dir = self._user_dir(workspace_id, user_id)
        self._assert_safe_path(artifact_dir, user_dir)

        if not artifact_dir.exists():
            return False
        # 确保路径在合法范围内再删除
        resolved = self._resolve_within_base(artifact_dir)
        shutil.rmtree(resolved, ignore_errors=True)
        return True

    async def cleanup_expired(self) -> int:
        """
        清理所有已过期的产物目录。

        Returns:
            删除的产物数量
        """
        if not self._base_dir.exists():
            return 0

        count = 0
        for workspace_dir in self._base_dir.iterdir():
            if not workspace_dir.is_dir():
                continue
            for user_dir in workspace_dir.iterdir():
                if not user_dir.is_dir():
                    continue
                for artifact_dir in user_dir.iterdir():
                    if not artifact_dir.is_dir():
                        continue
                    meta = await self._read_meta(artifact_dir)
                    if meta and not _is_expired(meta.get("expires_at", "")):
                        continue
                    try:
                        resolved = self._resolve_within_base(artifact_dir)
                    except PermissionError:
                        logger.warning("[TempArtifact] 跳过越界路径: %s", artifact_dir)
                        continue
                    shutil.rmtree(resolved, ignore_errors=True)
                    count += 1

        if count > 0:
            logger.info("[TempArtifact] 清理过期产物: %d 个", count)
        return count


# 单例
_service: TempArtifactService | None = None


def get_temp_artifact_service() -> TempArtifactService:
    """获取临时产物服务单例。"""
    global _service
    if _service is None:
        _service = TempArtifactService()
    return _service
