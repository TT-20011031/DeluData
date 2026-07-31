"""Workspace readiness aggregation for planner/executor/frontend."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.database import get_async_db_manager
from app.core.security.data_scope import build_visibility_where_clause, resolve_scope_dept_ids
from app.models.config.db_config import (
    get_user_db_config_async,
    get_workspace_admin_db_config_async,
    get_workspace_db_config_async,
)
from app.models.knowledge.graph import File

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DBReadiness:
    connected: bool
    source: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": bool(self.connected),
            "source": self.source,
            "host": self.host,
            "port": self.port,
            "database": self.database,
        }


@dataclass(frozen=True)
class KnowledgeReadiness:
    global_count: int = 0
    private_count: int = 0
    dept_count: int = 0
    total_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "global_count": int(self.global_count),
            "private_count": int(self.private_count),
            "dept_count": int(self.dept_count),
            "total_count": int(self.total_count),
        }


@dataclass(frozen=True)
class WorkspaceReadiness:
    has_db: bool
    has_knowledge: bool
    db: DBReadiness
    knowledge: KnowledgeReadiness
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_db": bool(self.has_db),
            "has_knowledge": bool(self.has_knowledge),
            "db": self.db.to_dict(),
            "knowledge": self.knowledge.to_dict(),
            "reasons": list(self.reasons or []),
        }


@dataclass(frozen=True)
class WorkerReadinessCheck:
    allowed: bool
    reason_code: Optional[str] = None
    message: str = ""


class WorkspaceReadinessService:
    REASON_NO_DB = "NO_DB_CONNECTION"
    REASON_NO_KNOWLEDGE = "KNOWLEDGE_BASE_EMPTY"

    REASON_WORKER_DB_REQUIRED = "DB_REQUIRED"
    REASON_WORKER_KNOWLEDGE_REQUIRED = "KNOWLEDGE_REQUIRED"
    REASON_WORKER_SOURCE_REQUIRED = "SOURCE_REQUIRED"

    _DB_REQUIRED_WORKERS = {"sql_worker"}
    _KNOWLEDGE_REQUIRED_WORKERS = {"doc_worker"}
    _SOURCE_REQUIRED_WORKERS = {"chart_worker", "office_worker"}
    _ALWAYS_ALLOWED_WORKERS = {"finish", "parameter_definition"}

    async def get_workspace_readiness(
        self,
        *,
        user_id: Optional[str],
        workspace_id: Optional[str],
    ) -> WorkspaceReadiness:
        db = await self._get_db_readiness(user_id=user_id, workspace_id=workspace_id)
        knowledge = await self._get_knowledge_readiness(user_id=user_id, workspace_id=workspace_id)

        has_db = bool(db.connected)
        has_knowledge = bool(knowledge.total_count > 0)

        reasons: list[str] = []
        if not has_db:
            reasons.append(self.REASON_NO_DB)
        if not has_knowledge:
            reasons.append(self.REASON_NO_KNOWLEDGE)

        return WorkspaceReadiness(
            has_db=has_db,
            has_knowledge=has_knowledge,
            db=db,
            knowledge=knowledge,
            reasons=reasons,
        )

    async def _get_db_readiness(
        self,
        *,
        user_id: Optional[str],
        workspace_id: Optional[str],
    ) -> DBReadiness:
        try:
            if user_id:
                user_config = await get_user_db_config_async(str(user_id))
                if user_config and user_config.is_active:
                    return DBReadiness(
                        connected=True,
                        source="user",
                        host=user_config.host,
                        port=user_config.port,
                        database=user_config.database,
                    )

            if workspace_id:
                ws_config = await get_workspace_db_config_async(workspace_id)
                if ws_config and ws_config.is_active:
                    return DBReadiness(
                        connected=True,
                        source="workspace",
                        host=ws_config.host,
                        port=ws_config.port,
                        database=ws_config.database,
                    )

                admin_config = await get_workspace_admin_db_config_async(workspace_id)
                if admin_config and admin_config.is_active:
                    return DBReadiness(
                        connected=True,
                        source="workspace",
                        host=admin_config.host,
                        port=admin_config.port,
                        database=admin_config.database,
                    )
        except Exception as exc:
            logger.warning(
                "[Readiness] DB readiness check failed: user_id=%s workspace_id=%s error=%s",
                user_id,
                workspace_id,
                exc,
            )

        return DBReadiness(connected=False)

    async def _get_knowledge_readiness(
        self,
        *,
        user_id: Optional[str],
        workspace_id: Optional[str],
    ) -> KnowledgeReadiness:
        db_manager = get_async_db_manager()
        try:
            async with db_manager.session_scope() as session:
                visibility_clause = await self._build_knowledge_visibility_clause(
                    session=session,
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
                if visibility_clause is None:
                    return KnowledgeReadiness()

                stmt = select(
                    func.sum(case((File.visibility == "public", 1), else_=0)).label("global_count"),
                    func.sum(case((File.visibility == "private", 1), else_=0)).label("private_count"),
                    func.sum(case((File.visibility == "dept", 1), else_=0)).label("dept_count"),
                ).where(
                    File.is_deleted.is_(False),
                    File.status == "indexed",
                    visibility_clause,
                )

                if workspace_id:
                    stmt = stmt.where(File.workspace_id == workspace_id)

                row = (await session.execute(stmt)).fetchone()
                if row:
                    global_count = int(row.global_count or 0)
                    private_count = int(row.private_count or 0)
                    dept_count = int(row.dept_count or 0)
                else:
                    global_count = 0
                    private_count = 0
                    dept_count = 0

                total = global_count + private_count + dept_count
                return KnowledgeReadiness(
                    global_count=global_count,
                    private_count=private_count,
                    dept_count=dept_count,
                    total_count=total,
                )
        except Exception as exc:
            logger.warning(
                "[Readiness] Knowledge readiness check failed: user_id=%s workspace_id=%s error=%s",
                user_id,
                workspace_id,
                exc,
            )
            return KnowledgeReadiness()

    async def _build_knowledge_visibility_clause(
        self,
        *,
        session: AsyncSession,
        user_id: Optional[str],
        workspace_id: Optional[str],
    ):
        if not user_id:
            logger.warning("[Readiness] missing user_id, knowledge readiness defaults to unavailable")
            return None

        from app.core.security.auth import get_user_by_id, user_model_to_user

        user_db = await get_user_by_id(session, str(user_id))
        if not user_db:
            logger.warning("[Readiness] user not found for knowledge readiness: user_id=%s", user_id)
            return None

        effective_workspace_id = workspace_id or user_db.workspace_id
        if effective_workspace_id and user_db.workspace_id != effective_workspace_id:
            logger.warning(
                "[Readiness] workspace mismatch for knowledge readiness: user_id=%s token_ws=%s db_ws=%s",
                user_id,
                workspace_id,
                user_db.workspace_id,
            )
            return None

        resolved_user = user_model_to_user(user_db)
        scope_context = SimpleNamespace(
            user_id=str(resolved_user.id),
            dept_id=resolved_user.department_id,
            data_scope=resolved_user.data_scope,
            workspace_id=effective_workspace_id,
        )
        scope_dept_ids = await resolve_scope_dept_ids(
            scope_context,
            descendants_loader=lambda dept_id: self._load_dept_tree_ids(
                session=session,
                dept_id=dept_id,
                workspace_id=effective_workspace_id,
            ),
        )
        return build_visibility_where_clause(
            File,
            user_context=scope_context,
            scope_dept_ids=scope_dept_ids,
            visibilities=["public", "private", "dept"],
        )

    async def _load_dept_tree_ids(
        self,
        *,
        session: AsyncSession,
        dept_id: int,
        workspace_id: Optional[str],
    ) -> list[int]:
        from app.models.auth.organization import DepartmentModel

        ids: list[int] = []

        stmt = select(DepartmentModel.id).where(DepartmentModel.id == dept_id)
        if workspace_id:
            stmt = stmt.where(DepartmentModel.workspace_id == workspace_id)
        result = await session.execute(stmt)
        ids.extend([int(row[0]) for row in result.fetchall() if row[0] is not None])

        descendants_stmt = select(DepartmentModel.id).where(
            DepartmentModel.ancestors.like(f"%/{dept_id}/%")
        )
        if workspace_id:
            descendants_stmt = descendants_stmt.where(DepartmentModel.workspace_id == workspace_id)
        descendants_result = await session.execute(descendants_stmt)
        ids.extend([int(row[0]) for row in descendants_result.fetchall() if row[0] is not None])

        return sorted(set(ids))

    @classmethod
    def readiness_from_state(
        cls,
        state: dict[str, Any],
        *,
        default_available: bool = True,
    ) -> WorkspaceReadiness:
        has_db_raw = state.get("has_db_connection")
        has_knowledge_raw = state.get("has_knowledge_base")

        if has_db_raw is None and has_knowledge_raw is None:
            has_db = default_available
            has_knowledge = default_available
            reasons = [] if default_available else [cls.REASON_NO_DB, cls.REASON_NO_KNOWLEDGE]
        else:
            has_db = bool(has_db_raw)
            has_knowledge = bool(has_knowledge_raw)
            reasons = list(state.get("readiness_reasons") or [])

        return WorkspaceReadiness(
            has_db=has_db,
            has_knowledge=has_knowledge,
            db=DBReadiness(connected=has_db),
            knowledge=KnowledgeReadiness(total_count=1 if has_knowledge else 0),
            reasons=reasons,
        )

    @classmethod
    def check_worker_availability(
        cls,
        worker: str,
        readiness: WorkspaceReadiness,
    ) -> WorkerReadinessCheck:
        if worker in cls._ALWAYS_ALLOWED_WORKERS:
            return WorkerReadinessCheck(True)

        if worker in cls._DB_REQUIRED_WORKERS and not readiness.has_db:
            return WorkerReadinessCheck(
                False,
                cls.REASON_WORKER_DB_REQUIRED,
                "该步骤依赖数据库，但当前未连接数据库。请先连接数据库后重试。",
            )

        if worker in cls._KNOWLEDGE_REQUIRED_WORKERS and not readiness.has_knowledge:
            return WorkerReadinessCheck(
                False,
                cls.REASON_WORKER_KNOWLEDGE_REQUIRED,
                "该步骤依赖知识库，但当前知识库为空。请先上传知识库文档后重试。",
            )

        if worker in cls._SOURCE_REQUIRED_WORKERS and not (readiness.has_db or readiness.has_knowledge):
            return WorkerReadinessCheck(
                False,
                cls.REASON_WORKER_SOURCE_REQUIRED,
                "该步骤至少需要一种数据源（数据库或知识库），请先完成任一数据源配置后重试。",
            )

        return WorkerReadinessCheck(True)

    @classmethod
    def build_sources_section(cls, readiness: WorkspaceReadiness) -> str:
        has_db = readiness.has_db
        has_knowledge = readiness.has_knowledge

        if has_db and has_knowledge:
            allowed = "doc_worker, sql_worker, chart_worker, office_worker"
            guidance = "数据源完整，可按正常流程规划。"
        elif has_db and not has_knowledge:
            allowed = "sql_worker, chart_worker, office_worker"
            guidance = "知识库为空，禁止规划 doc_worker。"
        elif (not has_db) and has_knowledge:
            allowed = "doc_worker, chart_worker, office_worker"
            guidance = "未连接数据库，禁止规划 sql_worker。可基于知识库执行文档/图表任务。"
        else:
            allowed = "finish"
            guidance = "无可用数据源，禁止取数类步骤，需先引导用户完成配置。"

        db_text = "可用" if has_db else "不可用"
        kb_text = "可用" if has_knowledge else "不可用"

        return (
            "\n## 数据源约束（必须遵守）\n"
            f"- 数据库: {db_text}\n"
            f"- 知识库: {kb_text}\n"
            f"- 当前允许的 Worker: {allowed}\n"
            f"- 约束说明: {guidance}\n"
            "- 若数据源不足，请使用 finish 直接给出配置指引，不要生成不可执行步骤。\n"
        )


__all__ = [
    "WorkspaceReadinessService",
    "WorkspaceReadiness",
    "WorkerReadinessCheck",
]
