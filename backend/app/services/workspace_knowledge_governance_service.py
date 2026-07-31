"""Workspace-level knowledge governance service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.tenant_mixin import bypass_tenant_filter
from app.models.auth.workspace import WorkspaceModel
from app.models.config.workspace_knowledge_governance import (
    WorkspaceKnowledgeGovernance,
    WorkspaceKnowledgeGovernanceModel,
)
from app.models.knowledge.graph import File

KnowledgeAction = Literal["upload", "delete", "rename", "move", "create_folder"]


@dataclass(frozen=True)
class GovernanceDecision:
    allowed: bool
    code: Optional[str] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class WorkspaceKnowledgeUsage:
    used_bytes: int = 0
    file_count: int = 0
    last_upload_at: Optional[datetime] = None


class WorkspaceKnowledgeGovernanceService:
    """Read, update, and enforce workspace knowledge governance rules."""

    ACTION_FIELD_MAP: dict[KnowledgeAction, str] = {
        "upload": "upload_enabled",
        "delete": "delete_enabled",
        "rename": "rename_enabled",
        "move": "move_enabled",
        "create_folder": "create_folder_enabled",
    }

    ACTION_ERROR_MAP: dict[KnowledgeAction, tuple[str, str]] = {
        "upload": ("knowledge_upload_disabled", "当前租户未开启知识库上传权限"),
        "delete": ("knowledge_delete_disabled", "当前租户未开启知识库删除权限"),
        "rename": ("knowledge_rename_disabled", "当前租户未开启知识库重命名权限"),
        "move": ("knowledge_move_disabled", "当前租户未开启知识库移动权限"),
        "create_folder": (
            "knowledge_create_folder_disabled",
            "当前租户未开启知识库新建文件夹权限",
        ),
    }

    QUOTA_EXCEEDED_CODE = "knowledge_storage_quota_exceeded"
    FILE_SIZE_EXCEEDED_CODE = "knowledge_upload_file_size_exceeded"

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def normalize_positive_bytes(value: Any) -> Optional[int]:
        if value in (None, "", 0, "0"):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @classmethod
    def normalize_storage_quota_bytes(cls, value: Any) -> Optional[int]:
        return cls.normalize_positive_bytes(value)

    @classmethod
    def normalize_max_upload_file_size_bytes(cls, value: Any) -> Optional[int]:
        return cls.normalize_positive_bytes(value)

    @classmethod
    def evaluate_max_upload_file_size_allowed(
        cls,
        max_upload_file_size_bytes: Optional[int],
        *,
        incoming_bytes: int,
    ) -> GovernanceDecision:
        normalized_limit = cls.normalize_max_upload_file_size_bytes(
            max_upload_file_size_bytes
        )
        next_upload = max(int(incoming_bytes or 0), 0)
        if normalized_limit is None or next_upload <= normalized_limit:
            return GovernanceDecision(True)

        return GovernanceDecision(
            False,
            code=cls.FILE_SIZE_EXCEEDED_CODE,
            message=(
                "上传文件大小超出限制："
                f"单文件上限 {cls.format_bytes(normalized_limit)}，"
                f"当前文件 {cls.format_bytes(next_upload)}。"
            ),
        )

    @staticmethod
    def build_default_config(workspace_id: str) -> WorkspaceKnowledgeGovernance:
        return WorkspaceKnowledgeGovernance(workspace_id=workspace_id)

    @staticmethod
    def format_bytes(num_bytes: int) -> str:
        value = max(int(num_bytes or 0), 0)
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(value)
        unit = units[0]
        for unit in units:
            if size < 1024 or unit == units[-1]:
                break
            size /= 1024
        if unit == "B":
            return f"{int(size)} {unit}"
        return f"{size:.1f} {unit}"

    @classmethod
    def build_feature_flags(
        cls,
        config: WorkspaceKnowledgeGovernance,
    ) -> dict[str, Any]:
        return {
            "knowledge_upload_enabled": bool(config.upload_enabled),
            "knowledge_delete_enabled": bool(config.delete_enabled),
            "knowledge_rename_enabled": bool(config.rename_enabled),
            "knowledge_move_enabled": bool(config.move_enabled),
            "knowledge_create_folder_enabled": bool(config.create_folder_enabled),
            "knowledge_storage_quota_bytes": cls.normalize_storage_quota_bytes(
                config.storage_quota_bytes
            ),
            "knowledge_max_upload_file_size_bytes": cls.normalize_max_upload_file_size_bytes(
                config.max_upload_file_size_bytes
            ),
        }

    @classmethod
    def evaluate_action_allowed(
        cls,
        config: WorkspaceKnowledgeGovernance,
        action: KnowledgeAction,
    ) -> GovernanceDecision:
        field_name = cls.ACTION_FIELD_MAP[action]
        if bool(getattr(config, field_name, True)):
            return GovernanceDecision(True)

        code, message = cls.ACTION_ERROR_MAP[action]
        return GovernanceDecision(False, code=code, message=message)

    @classmethod
    def evaluate_upload_allowed(
        cls,
        config: WorkspaceKnowledgeGovernance,
        *,
        used_bytes: int,
        incoming_bytes: int,
    ) -> GovernanceDecision:
        base_decision = cls.evaluate_action_allowed(config, "upload")
        if not base_decision.allowed:
            return base_decision

        next_upload = max(int(incoming_bytes or 0), 0)
        file_size_decision = cls.evaluate_max_upload_file_size_allowed(
            config.max_upload_file_size_bytes,
            incoming_bytes=next_upload,
        )
        if not file_size_decision.allowed:
            return file_size_decision

        quota_bytes = cls.normalize_storage_quota_bytes(config.storage_quota_bytes)
        if quota_bytes is None:
            return GovernanceDecision(True)

        current_used = max(int(used_bytes or 0), 0)
        projected = current_used + next_upload
        if projected <= quota_bytes:
            return GovernanceDecision(True)

        return GovernanceDecision(
            False,
            code=cls.QUOTA_EXCEEDED_CODE,
            message=(
                "知识库存储额度不足："
                f"已用 {cls.format_bytes(current_used)} / "
                f"总额 {cls.format_bytes(quota_bytes)}，"
                f"本次上传 {cls.format_bytes(next_upload)}。"
            ),
        )

    @classmethod
    def build_usage_payload(
        cls,
        config: WorkspaceKnowledgeGovernance,
        usage: WorkspaceKnowledgeUsage,
    ) -> dict[str, Any]:
        quota_bytes = cls.normalize_storage_quota_bytes(config.storage_quota_bytes)
        used_bytes = max(int(usage.used_bytes or 0), 0)
        remaining_bytes = None if quota_bytes is None else max(quota_bytes - used_bytes, 0)
        usage_ratio = None
        if quota_bytes:
            usage_ratio = min(max(used_bytes / quota_bytes, 0.0), 1.0)

        return {
            "storage_quota_bytes": quota_bytes,
            "storage_used_bytes": used_bytes,
            "storage_remaining_bytes": remaining_bytes,
            "storage_usage_ratio": usage_ratio,
            "file_count": int(usage.file_count or 0),
            "last_upload_at": usage.last_upload_at,
        }

    async def get_workspace_config(
        self,
        workspace_id: str,
    ) -> WorkspaceKnowledgeGovernance:
        result = await self.db.execute(
            select(WorkspaceKnowledgeGovernanceModel).where(
                WorkspaceKnowledgeGovernanceModel.workspace_id == workspace_id
            )
        )
        model = result.scalar_one_or_none()
        if not model:
            return self.build_default_config(workspace_id)
        return WorkspaceKnowledgeGovernance.from_orm(model)

    async def update_workspace_config(
        self,
        workspace_id: str,
        *,
        storage_quota_bytes: Optional[int],
        max_upload_file_size_bytes: Optional[int],
        upload_enabled: bool,
        delete_enabled: bool,
        rename_enabled: bool,
        move_enabled: bool,
        create_folder_enabled: bool,
        note: Optional[str],
        updated_by: Optional[str],
    ) -> WorkspaceKnowledgeGovernance:
        normalized_quota = self.normalize_storage_quota_bytes(storage_quota_bytes)
        normalized_max_upload_file_size = self.normalize_max_upload_file_size_bytes(
            max_upload_file_size_bytes
        )
        result = await self.db.execute(
            select(WorkspaceKnowledgeGovernanceModel).where(
                WorkspaceKnowledgeGovernanceModel.workspace_id == workspace_id
            )
        )
        model = result.scalar_one_or_none()

        if model:
            model.storage_quota_bytes = normalized_quota
            model.max_upload_file_size_bytes = normalized_max_upload_file_size
            model.upload_enabled = bool(upload_enabled)
            model.delete_enabled = bool(delete_enabled)
            model.rename_enabled = bool(rename_enabled)
            model.move_enabled = bool(move_enabled)
            model.create_folder_enabled = bool(create_folder_enabled)
            model.note = (note or "").strip() or None
            model.updated_by = updated_by
            model.updated_at = datetime.now()
        else:
            model = WorkspaceKnowledgeGovernanceModel(
                workspace_id=workspace_id,
                storage_quota_bytes=normalized_quota,
                max_upload_file_size_bytes=normalized_max_upload_file_size,
                upload_enabled=bool(upload_enabled),
                delete_enabled=bool(delete_enabled),
                rename_enabled=bool(rename_enabled),
                move_enabled=bool(move_enabled),
                create_folder_enabled=bool(create_folder_enabled),
                note=(note or "").strip() or None,
                updated_by=updated_by,
            )
            self.db.add(model)
            await self.db.flush()

        await self.db.commit()
        await self.db.refresh(model)
        return WorkspaceKnowledgeGovernance.from_orm(model)

    async def get_workspace_usage(
        self,
        workspace_id: str,
    ) -> WorkspaceKnowledgeUsage:
        stmt = (
            select(
                func.coalesce(func.sum(func.coalesce(File.file_size, 0)), 0),
                func.count(File.id),
                func.max(File.created_at),
            )
            .where(File.workspace_id == workspace_id)
            .where(File.is_deleted.is_(False))
        )
        with bypass_tenant_filter():
            result = await self.db.execute(stmt)
        row = result.one()
        return WorkspaceKnowledgeUsage(
            used_bytes=int(row[0] or 0),
            file_count=int(row[1] or 0),
            last_upload_at=row[2],
        )

    async def get_workspace_feature_flags(
        self,
        workspace_id: str,
    ) -> dict[str, Any]:
        config = await self.get_workspace_config(workspace_id)
        return self.build_feature_flags(config)

    async def ensure_action_allowed(
        self,
        workspace_id: str,
        action: KnowledgeAction,
    ) -> None:
        config = await self.get_workspace_config(workspace_id)
        decision = self.evaluate_action_allowed(config, action)
        if decision.allowed:
            return
        raise HTTPException(status_code=403, detail=decision.message or decision.code or "forbidden")

    async def ensure_upload_allowed(
        self,
        workspace_id: str,
        *,
        incoming_bytes: int,
        lock_workspace: bool = False,
    ) -> None:
        if lock_workspace:
            workspace_row = await self.db.execute(
                select(WorkspaceModel.id)
                .where(WorkspaceModel.id == workspace_id)
                .with_for_update()
            )
            if workspace_row.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="工作空间不存在")

        config = await self.get_workspace_config(workspace_id)
        usage = await self.get_workspace_usage(workspace_id)
        decision = self.evaluate_upload_allowed(
            config,
            used_bytes=usage.used_bytes,
            incoming_bytes=incoming_bytes,
        )
        if decision.allowed:
            return
        raise HTTPException(
            status_code=(
                413
                if decision.code == self.FILE_SIZE_EXCEEDED_CODE
                else 409
                if decision.code == self.QUOTA_EXCEEDED_CODE
                else 403
            ),
            detail=decision.message or decision.code or "forbidden",
        )

    async def list_workspace_summaries(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        usage_subquery = (
            select(
                File.workspace_id.label("workspace_id"),
                func.coalesce(func.sum(func.coalesce(File.file_size, 0)), 0).label(
                    "used_bytes"
                ),
                func.count(File.id).label("file_count"),
                func.max(File.created_at).label("last_upload_at"),
            )
            .where(File.is_deleted.is_(False))
            .group_by(File.workspace_id)
            .subquery()
        )

        stmt = (
            select(
                WorkspaceModel.id,
                WorkspaceModel.code,
                WorkspaceModel.name,
                WorkspaceKnowledgeGovernanceModel.storage_quota_bytes,
                WorkspaceKnowledgeGovernanceModel.max_upload_file_size_bytes,
                WorkspaceKnowledgeGovernanceModel.upload_enabled,
                WorkspaceKnowledgeGovernanceModel.delete_enabled,
                WorkspaceKnowledgeGovernanceModel.rename_enabled,
                WorkspaceKnowledgeGovernanceModel.move_enabled,
                WorkspaceKnowledgeGovernanceModel.create_folder_enabled,
                func.coalesce(usage_subquery.c.used_bytes, 0),
                func.coalesce(usage_subquery.c.file_count, 0),
                usage_subquery.c.last_upload_at,
            )
            .select_from(WorkspaceModel)
            .outerjoin(
                WorkspaceKnowledgeGovernanceModel,
                WorkspaceKnowledgeGovernanceModel.workspace_id == WorkspaceModel.id,
            )
            .outerjoin(
                usage_subquery,
                usage_subquery.c.workspace_id == WorkspaceModel.id,
            )
            .order_by(WorkspaceModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )

        with bypass_tenant_filter():
            result = await self.db.execute(stmt)
        rows = result.all()

        summaries: list[dict[str, Any]] = []
        for row in rows:
            config = WorkspaceKnowledgeGovernance(
                workspace_id=row[0],
                storage_quota_bytes=self.normalize_storage_quota_bytes(row[3]),
                max_upload_file_size_bytes=self.normalize_max_upload_file_size_bytes(
                    row[4]
                ),
                upload_enabled=True if row[5] is None else bool(row[5]),
                delete_enabled=True if row[6] is None else bool(row[6]),
                rename_enabled=True if row[7] is None else bool(row[7]),
                move_enabled=True if row[8] is None else bool(row[8]),
                create_folder_enabled=True if row[9] is None else bool(row[9]),
            )
            usage = WorkspaceKnowledgeUsage(
                used_bytes=int(row[10] or 0),
                file_count=int(row[11] or 0),
                last_upload_at=row[12],
            )
            summaries.append(
                {
                    "workspace_id": row[0],
                    "workspace_code": row[1],
                    "workspace_name": row[2],
                    "max_upload_file_size_bytes": config.max_upload_file_size_bytes,
                    "upload_enabled": config.upload_enabled,
                    "delete_enabled": config.delete_enabled,
                    "rename_enabled": config.rename_enabled,
                    "move_enabled": config.move_enabled,
                    "create_folder_enabled": config.create_folder_enabled,
                    **self.build_usage_payload(config, usage),
                }
            )
        return summaries
