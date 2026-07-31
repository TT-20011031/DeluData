"""[M3.5] Wiki 检索路由评估指标服务。

职责：
- 写入：从 doc_worker 执行结果 + 路由 state 落库一行 `wiki_route_metrics`
- 查询：聚合最近 N 天的路径分布、平均时延、wiki_gap 触发率

设计：
- 单表追加写入，fire-and-forget；写失败仅 log，不影响主流程
- 查询返回结构化字典供 API 层透传
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import and_, case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.database import get_async_db_manager
from app.models.wiki.wiki_route_metric import WikiRouteMetric

logger = logging.getLogger(__name__)


VALID_PATHS = {"rag", "wiki", "both"}


class WikiMetricsService:
    """Wiki 路由评估指标服务。"""

    def __init__(self, db: Optional[AsyncSession] = None):
        self.db = db

    @staticmethod
    def _trim(value: Optional[str], max_len: int) -> Optional[str]:
        if value is None:
            return None
        s = str(value)
        return s[:max_len] if len(s) > max_len else s

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    async def record_route_metric(
        self,
        *,
        workspace_id: str,
        knowledge_path: str,
        knowledge_path_source: Optional[str] = None,
        knowledge_path_reason: Optional[str] = None,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        user_id: Optional[str] = None,
        chunks_used: int = 0,
        wiki_chunks_count: int = 0,
        wiki_chunks_used: int = 0,
        has_wiki_gap: bool = False,
        wiki_gap_task_id: Optional[str] = None,
        latency_ms: int = 0,
        stop_reason: Optional[str] = None,
        user_query: Optional[str] = None,
        extra_meta: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """落库一行；返回新行 id（失败时返回 None）。"""
        if not workspace_id:
            return None
        if knowledge_path not in VALID_PATHS:
            knowledge_path = "rag"

        new_id = str(uuid.uuid4())
        record = WikiRouteMetric(
            id=new_id,
            workspace_id=workspace_id,
            session_id=self._trim(session_id, 64),
            message_id=self._trim(message_id, 64),
            user_id=self._trim(user_id, 36),
            knowledge_path=knowledge_path,
            knowledge_path_source=self._trim(knowledge_path_source, 16),
            knowledge_path_reason=self._trim(knowledge_path_reason, 255),
            chunks_used=max(0, int(chunks_used or 0)),
            wiki_chunks_count=max(0, int(wiki_chunks_count or 0)),
            wiki_chunks_used=max(0, int(wiki_chunks_used or 0)),
            has_wiki_gap=bool(has_wiki_gap),
            wiki_gap_task_id=self._trim(wiki_gap_task_id, 36),
            latency_ms=max(0, int(latency_ms or 0)),
            stop_reason=self._trim(stop_reason, 32),
            user_query=self._trim(user_query, 200),
            extra_meta=extra_meta,
        )

        try:
            if self.db is not None:
                self.db.add(record)
                await self.db.flush()
            else:
                db_manager = get_async_db_manager()
                async with db_manager.session_scope() as session:
                    session.add(record)
            return new_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("[wiki_metrics] 写入失败（软降级）: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 查询：聚合
    # ------------------------------------------------------------------
    async def get_route_summary(
        self,
        *,
        workspace_id: str,
        days: int = 7,
    ) -> dict[str, Any]:
        """近 N 天路由汇总。

        Returns:
            {
                "window_days": 7,
                "since": "2026-05-01T00:00:00",
                "total": 123,
                "by_path": {
                    "rag":  {"count": 80, "avg_latency_ms": 450, "wiki_gap_count": 0},
                    "wiki": {"count": 30, "avg_latency_ms": 380, "wiki_gap_count": 5},
                    "both": {"count": 13, "avg_latency_ms": 520, "wiki_gap_count": 2},
                },
                "wiki_hit_rate": 0.349,         # (wiki+both) / total
                "wiki_gap_rate": 0.057,         # has_wiki_gap=True / total
                "avg_latency_ms": 446,
            }
        """
        try:
            days_int = int(days) if days is not None else 7
        except (TypeError, ValueError):
            days_int = 7
        days = max(1, min(days_int, 90))
        since = datetime.utcnow() - timedelta(days=days)

        async def _run(session: AsyncSession) -> dict[str, Any]:
            # 使用 SQL CASE WHEN 实现跨方言（MySQL/SQLite/Postgres）的条件计数
            gap_expr = func.sum(
                case((WikiRouteMetric.has_wiki_gap.is_(True), 1), else_=0)
            ).label("wiki_gap_count")
            stmt = (
                select(
                    WikiRouteMetric.knowledge_path,
                    func.count(WikiRouteMetric.id).label("count"),
                    func.coalesce(func.avg(WikiRouteMetric.latency_ms), 0).label("avg_latency"),
                    gap_expr,
                )
                .where(
                    and_(
                        WikiRouteMetric.workspace_id == workspace_id,
                        WikiRouteMetric.created_at >= since,
                    )
                )
                .group_by(WikiRouteMetric.knowledge_path)
            )
            rows = (await session.execute(stmt)).all()

            by_path: dict[str, dict[str, Any]] = {
                p: {"count": 0, "avg_latency_ms": 0, "wiki_gap_count": 0}
                for p in VALID_PATHS
            }
            total = 0
            wiki_gap_total = 0
            latency_sum = 0
            for path, cnt, avg_lat, gap_cnt in rows:
                p = str(path or "rag")
                if p not in by_path:
                    by_path[p] = {"count": 0, "avg_latency_ms": 0, "wiki_gap_count": 0}
                cnt_int = int(cnt or 0)
                gap_int = int(gap_cnt or 0)
                avg_lat_int = int(round(float(avg_lat or 0)))
                by_path[p]["count"] = cnt_int
                by_path[p]["avg_latency_ms"] = avg_lat_int
                by_path[p]["wiki_gap_count"] = gap_int
                total += cnt_int
                wiki_gap_total += gap_int
                latency_sum += avg_lat_int * cnt_int

            wiki_hit = by_path["wiki"]["count"] + by_path["both"]["count"]
            meta_stmt = (
                select(WikiRouteMetric.extra_meta)
                .where(
                    and_(
                        WikiRouteMetric.workspace_id == workspace_id,
                        WikiRouteMetric.created_at >= since,
                    )
                )
            )
            meta_rows = (await session.execute(meta_stmt)).scalars().all()
            wiki_fallback_to_rag_count = 0
            wrong_tool_repair_count = 0
            wiki_hit_but_not_used_count = 0
            for extra in meta_rows:
                if not isinstance(extra, dict):
                    continue
                if extra.get("wiki_fallback_to_rag"):
                    wiki_fallback_to_rag_count += 1
                if extra.get("wrong_tool_repair"):
                    wrong_tool_repair_count += 1
                if extra.get("wiki_hit_but_not_used"):
                    wiki_hit_but_not_used_count += 1
            return {
                "window_days": days,
                "since": since.isoformat(timespec="seconds"),
                "total": total,
                "by_path": by_path,
                "wiki_hit_rate": (wiki_hit / total) if total else 0.0,
                "wiki_gap_rate": (wiki_gap_total / total) if total else 0.0,
                "avg_latency_ms": int(round(latency_sum / total)) if total else 0,
                "wiki_fallback_to_rag_count": wiki_fallback_to_rag_count,
                "wrong_tool_repair_count": wrong_tool_repair_count,
                "wiki_hit_but_not_used_count": wiki_hit_but_not_used_count,
            }

        if self.db is not None:
            return await _run(self.db)
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            return await _run(session)

    async def list_conversation_diagnostics(
        self,
        *,
        workspace_id: str,
        session_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return recent real conversation doc_worker diagnostics from metrics rows."""
        if not workspace_id:
            return []
        try:
            limit_int = int(limit)
        except (TypeError, ValueError):
            limit_int = 20
        limit_int = max(1, min(limit_int, 100))

        async def _run(session: AsyncSession) -> list[dict[str, Any]]:
            conditions = [WikiRouteMetric.workspace_id == workspace_id]
            if session_id:
                conditions.append(WikiRouteMetric.session_id == session_id)
            stmt = (
                select(WikiRouteMetric)
                .where(and_(*conditions))
                .order_by(desc(WikiRouteMetric.created_at))
                .limit(limit_int)
            )
            records = (await session.execute(stmt)).scalars().all()
            items: list[dict[str, Any]] = []
            for record in records:
                extra = record.extra_meta if isinstance(record.extra_meta, dict) else {}
                items.append(
                    {
                        "id": record.id,
                        "created_at": record.created_at.isoformat() if record.created_at else "",
                        "session_id": record.session_id,
                        "message_id": record.message_id,
                        "knowledge_path": record.knowledge_path,
                        "knowledge_path_source": record.knowledge_path_source,
                        "knowledge_path_reason": record.knowledge_path_reason,
                        "user_query": record.user_query or "",
                        "chunks_used": record.chunks_used,
                        "wiki_chunks_count": record.wiki_chunks_count,
                        "wiki_chunks_used": record.wiki_chunks_used,
                        "rag_chunks_used": int(extra.get("rag_chunks_used") or 0),
                        "wiki_fallback_to_rag": bool(extra.get("wiki_fallback_to_rag")),
                        "answer_context_source": extra.get("answer_context_source") or "empty",
                        "issues": extra.get("issues") or [],
                        "final_context": extra.get("final_context") or [],
                        "tool_repair_trace": extra.get("tool_repair_trace") or [],
                        "wrong_tool_repair": bool(extra.get("wrong_tool_repair")),
                        "route_action": extra.get("route_action"),
                        "repair_reason": extra.get("repair_reason"),
                    }
                )
            return items

        if self.db is not None:
            return await _run(self.db)
        db_manager = get_async_db_manager()
        async with db_manager.session_scope() as session:
            return await _run(session)


_singleton: Optional[WikiMetricsService] = None


def get_wiki_metrics_service() -> WikiMetricsService:
    global _singleton
    if _singleton is None:
        _singleton = WikiMetricsService()
    return _singleton
