"""Wiki API 路由（租户内可用）。

权限策略：
- 列表 / 详情：登录用户即可（与知识库 view 权限一致）
- 编译 / 编辑：管理员 或 拥有 config:manage / knowledge:manage 权限

注意：所有写入由 WikiService 内部管理事务，路由层不介入 session。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user
from app.core.security.rbac_deps import CheckPerm
from app.api.wiki.schemas import (
    WikiBatchArchiveRequest,
    WikiBatchArchiveResponse,
    WikiCompileRequest,
    WikiCompileTaskAccepted,
    WikiCompileTaskCancelResponse,
    WikiCompileTaskStatus,
    WikiDomainBucket,
    WikiDomainListResponse,
    WikiLintReportOut,
    WikiPageDetail,
    WikiPageListResponse,
    WikiPageUpdateRequest,
    WikiPurgeOrphansResponse,
    WikiQuotaStatus,
    ConversationDiagnosticsResponse,
    RetrievalDiagnosticsRequest,
    RetrievalDiagnosticsResponse,
    WikiCandidateGovernanceResponse,
    WikiGraphResponse,
    WikiRouteSummary,
    WikiGovernanceActionRequest,
)
from app.core.security.auth import User
from app.models.common.context import UserContext
from app.services.retrieval_diagnostics_service import get_retrieval_diagnostics_service
from app.services.wiki_compile_queue_service import (
    ACTIVE_STATUSES,
    get_wiki_compile_queue,
)
from app.services.wiki_metrics_service import get_wiki_metrics_service
from app.services.wiki_service import WikiService, get_wiki_service
from app.core.db.database import get_async_db_manager
from app.models.wiki.wiki_compile_task import WikiCompileTask
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wiki", tags=["Wiki"])
get_current_admin = CheckPerm("knowledge:manage")


def _service() -> WikiService:
    return get_wiki_service()


# =============================================================================
# 列表 / 详情
# =============================================================================


@router.get("/pages", response_model=WikiPageListResponse)
async def list_wiki_pages(
    domain: Optional[str] = Query(default=None),
    status_: Optional[str] = Query(default=None, alias="status"),
    keyword: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=None),
    department_id: Optional[int] = Query(default=None),
    business_domain: Optional[str] = Query(default=None),
    document_type: Optional[str] = Query(default=None),
    confidentiality_level: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
):
    data = await _service().list_pages(
        workspace_id=current_user.workspace_id,
        user_id=current_user.id,
        user_role="knowledge_manager" if any(code in current_user.permissions for code in ("*", "knowledge:manage")) else "user",
        user_department_id=current_user.department_id,
        domain=domain,
        status=status_,
        keyword=keyword,
        scope=scope,
        department_id=department_id,
        business_domain=business_domain,
        document_type=document_type,
        confidentiality_level=confidentiality_level,
        limit=limit,
        offset=offset,
    )
    return data


@router.get("/graph", response_model=WikiGraphResponse)
async def get_wiki_graph(
    domain: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=None),
    department_id: Optional[int] = Query(default=None),
    business_domain: Optional[str] = Query(default=None),
    document_type: Optional[str] = Query(default=None),
    confidentiality_level: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    """返回当前用户可见的已发布 Wiki 实体关系图。"""
    return await _service().get_graph(
        workspace_id=current_user.workspace_id,
        user_id=current_user.id,
        user_role="knowledge_manager" if any(code in current_user.permissions for code in ("*", "knowledge:manage")) else "user",
        user_department_id=current_user.department_id,
        domain=domain,
        scope=scope,
        department_id=department_id,
        business_domain=business_domain,
        document_type=document_type,
        confidentiality_level=confidentiality_level,
    )


@router.get("/pages/{slug}", response_model=WikiPageDetail)
async def get_wiki_page(
    slug: str,
    current_user: User = Depends(get_current_user),
):
    detail = await _service().get_page(
        workspace_id=current_user.workspace_id,
        slug=slug,
    )
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page_not_found")
    return detail


# =============================================================================
# 编译 / Lint（管理员）
# =============================================================================


@router.post("/compile")
async def compile_wiki(
    payload: WikiCompileRequest,
    async_: bool = Query(
        default=False,
        alias="async",
        description="true 时入队并立即返回 task_id；false 同步执行（默认，向后兼容）",
    ),
    admin: User = Depends(get_current_admin),
):
    """编译接口。

    - 同步模式（默认）：等所有 LLM 完成后返回完整 result，适合脚本 / 小文档。
    - 异步模式（?async=true）：入队 wiki_compile_tasks 后立即返回 task_id，
      前端调 GET /api/wiki/tasks/{task_id} 轮询进度，避免 HTTP 长连接超时 / 按钮死转。
    """
    if payload.scope != "workspace" and not payload.file_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="file_ids 与 scope 必须二选一",
        )

    if async_:
        # 入队路径：worker 消费 wiki_compile_tasks
        task_payload = {}
        if payload.scope == "workspace":
            task_payload["scope"] = "workspace"
            trigger_type = "manual_workspace"
        else:
            task_payload["scope"] = "files"
            task_payload["file_ids"] = list(payload.file_ids or [])
            trigger_type = "manual_files"

        queue = get_wiki_compile_queue()
        # 判断是否复用了已有 active 任务（deduplicate）
        async with get_async_db_manager().session_scope() as session:
            existing = (
                await session.execute(
                    select(WikiCompileTask)
                    .where(
                        WikiCompileTask.workspace_id == admin.workspace_id,
                        WikiCompileTask.trigger_type == trigger_type,
                        WikiCompileTask.status.in_(list(ACTIVE_STATUSES)),
                    )
                    .limit(1)
                )
            ).scalars().first()
            existed_before = existing is not None

        task = await queue.enqueue_task(
            workspace_id=admin.workspace_id,
            trigger_type=trigger_type,
            user_id=admin.id,
            payload=task_payload,
        )
        return WikiCompileTaskAccepted(
            task_id=str(task.id),
            status=str(task.status),
            trigger_type=str(task.trigger_type),
            workspace_id=str(task.workspace_id),
            deduplicated=existed_before,
        )

    # 同步路径（原行为）
    if payload.scope == "workspace":
        result = await _service().compile_workspace(
            workspace_id=admin.workspace_id,
            triggered_by=f"user:{admin.id}",
            user_id=admin.id,
        )
    else:
        result = await _service().compile_files(
            workspace_id=admin.workspace_id,
            file_ids=payload.file_ids,
            triggered_by=f"user:{admin.id}",
            user_id=admin.id,
        )

    if not result.get("success", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error") or "compile_failed",
        )
    return result


@router.get("/tasks/{task_id}", response_model=WikiCompileTaskStatus)
async def get_wiki_compile_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """查询 wiki 编译任务状态（轮询）。

    权限：仅可查询本 workspace 的任务（以免跨租户窃听）。
    """
    async with get_async_db_manager().session_scope() as session:
        task = (
            await session.execute(
                select(WikiCompileTask).where(WikiCompileTask.id == task_id)
            )
        ).scalars().first()

    if task is None or str(task.workspace_id) != str(current_user.workspace_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task_not_found")

    # 注意：task.started_at / finished_at 是 datetime.utcnow() 产出的 naive UTC datetime，
    # 直接 isoformat() 前端会按本地时区解析产生 8 小时误差；追加 "Z" 明确标注 UTC。
    return WikiCompileTaskStatus(
        task_id=str(task.id),
        workspace_id=str(task.workspace_id),
        trigger_type=str(task.trigger_type),
        status=str(task.status),
        stage=str(task.stage) if task.stage else None,
        progress=int(task.progress or 0),
        attempt=int(task.attempt or 0),
        max_attempts=int(task.max_attempts or 1),
        started_at=(task.started_at.isoformat() + "Z") if task.started_at else None,
        finished_at=(task.finished_at.isoformat() + "Z") if task.finished_at else None,
        error_message=task.error_message,
        result=dict(task.result_json) if task.result_json else None,
    )


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=WikiCompileTaskCancelResponse,
)
async def cancel_wiki_compile_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """请求取消编译任务（软取消语义）。

    语义：
    - 已 succeeded / failed / cancelled 的任务返回 already_terminal，不再变动
    - pending 任务直接 cancelled_immediately
    - running 任务转 cancel_requested，worker 会在下一个候选/批次开始前停止启动新 LLM 调用，
      已成功落库的页保留；当前正在跑的 LLM 调用让其完成

    权限：与 GET /tasks/{task_id} 一致，仅本 workspace 用户可取消。
    """
    queue = get_wiki_compile_queue()
    outcome, prev_status = await queue.request_cancel(
        task_id=task_id,
        workspace_id=str(current_user.workspace_id),
    )
    if outcome == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="task_not_found",
        )
    return WikiCompileTaskCancelResponse(
        task_id=task_id,
        previous_status=prev_status,
        outcome=outcome,
    )


@router.post("/lint", response_model=WikiLintReportOut)
async def run_wiki_lint(admin: User = Depends(get_current_admin)):
    report = await _service().lint_workspace(workspace_id=admin.workspace_id)
    return report


@router.post("/purge-orphans", response_model=WikiPurgeOrphansResponse)
async def purge_orphan_wiki_pages(admin: User = Depends(get_current_admin)):
    """[治理] 清理「原文件均已删除」的孤儿实体页（软删 = status='archived'）。

    判定准则（保守，避免误删手工建页）：
    - page 至少绑定一条 wiki_page_sources（即由编译产生）
    - 所有绑定的 source file 都已 is_deleted=True 或不存在
    - 当前 status 不是 archived

    动作：把符合条件的 page 改为 archived，并写一条 wiki_revisions 便于追溯。
    """
    return await _service().purge_orphans_for_deleted_files(
        workspace_id=admin.workspace_id,
        triggered_by=f"user:{admin.id}",
    )


@router.post("/pages/batch-archive", response_model=WikiBatchArchiveResponse)
async def batch_archive_wiki_pages(
    payload: WikiBatchArchiveRequest,
    admin: User = Depends(get_current_admin),
):
    """[治理] 批量归档实体页（status='archived'）。

    支持两种模式（二选一）：
    - slugs: 按 slug 列表精确归档
    - domain: 归档整个域下所有非 archived 页面

    动作：把符合条件的 page 改为 archived，并写一条 wiki_revisions 便于追溯。
    """
    if not payload.slugs and not payload.domain:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 slugs 或 domain 其中之一",
        )

    return await _service().batch_archive_pages(
        workspace_id=admin.workspace_id,
        slugs=payload.slugs,
        domain=payload.domain,
        triggered_by=f"user:{admin.id}",
    )


@router.put("/pages/{slug}")
async def update_wiki_page(
    slug: str,
    payload: WikiPageUpdateRequest,
    admin: User = Depends(get_current_admin),
):
    result = await _service().update_page(
        workspace_id=admin.workspace_id,
        slug=slug,
        markdown_body=payload.markdown_body,
        summary=payload.summary,
        title=payload.title,
        domain=payload.domain,
        status=payload.status,
        committed_by=f"user:{admin.id}",
        commit_message=payload.commit_message,
    )
    if not result.get("success"):
        http_status = (
            status.HTTP_404_NOT_FOUND
            if result.get("error") == "page_not_found"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=http_status,
            detail=result.get("error") or "update_failed",
        )
    return result


@router.get(
    "/governance/candidates",
    response_model=WikiCandidateGovernanceResponse,
)
async def list_wiki_candidate_governance(
    keyword: Optional[str] = Query(default=None),
    status_: Optional[str] = Query(default=None, alias="status"),
    scope: Optional[str] = Query(default=None),
    department_id: Optional[int] = Query(default=None),
    business_domain: Optional[str] = Query(default=None),
    document_type: Optional[str] = Query(default=None),
    confidentiality_level: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(get_current_admin),
):
    statuses = None
    if status_:
        statuses = [s.strip() for s in status_.split(",") if s.strip()]
    return await _service().list_candidate_governance(
        workspace_id=admin.workspace_id,
        user_id=admin.id,
        user_role="knowledge_manager",
        user_department_id=admin.department_id,
        keyword=keyword,
        statuses=statuses,
        scope=scope,
        department_id=department_id,
        business_domain=business_domain,
        document_type=document_type,
        confidentiality_level=confidentiality_level,
        limit=limit,
        offset=offset,
    )


@router.post("/pages/{slug}/governance-action")
async def apply_wiki_governance_action(
    slug: str,
    payload: WikiGovernanceActionRequest,
    admin: User = Depends(get_current_admin),
):
    result = await _service().apply_governance_action(
        workspace_id=admin.workspace_id,
        slug=slug,
        action=payload.action,
        target_slug=payload.target_slug,
        note=payload.note,
        committed_by=f"user:{admin.id}",
        commit_message=payload.commit_message,
    )
    if not result.get("success"):
        http_status = (
            status.HTTP_404_NOT_FOUND
            if result.get("error") == "page_not_found"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=http_status,
            detail=result.get("error") or "governance_action_failed",
        )
    return result


# =============================================================================
# [M3.5] 路由评估 + 范围选择支持
# =============================================================================


@router.get("/metrics/route", response_model=WikiRouteSummary)
async def get_wiki_route_metrics(
    days: int = Query(default=7, ge=1, le=90, description="统计窗口（1-90 天）"),
    current_user: User = Depends(get_current_user),
):
    """近 N 天 Wiki 检索路由评估汇总。

    - by_path: rag/wiki/both 各自 count + avg_latency_ms + wiki_gap_count
    - wiki_hit_rate: (wiki+both) / total
    - wiki_gap_rate: has_wiki_gap=true / total
    """
    summary = await get_wiki_metrics_service().get_route_summary(
        workspace_id=current_user.workspace_id,
        days=days,
    )
    return summary


@router.get("/diagnostics/conversation", response_model=ConversationDiagnosticsResponse)
async def list_conversation_diagnostics(
    session_id: Optional[str] = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
):
    """Return recent real doc_worker diagnostics for the current workspace."""
    items = await get_wiki_metrics_service().list_conversation_diagnostics(
        workspace_id=admin.workspace_id,
        session_id=session_id,
        limit=limit,
    )
    return {"items": items}


@router.get("/domains", response_model=WikiDomainListResponse)
async def list_wiki_domains(
    sample_per_domain: int = Query(default=5, ge=0, le=20),
    current_user: User = Depends(get_current_user),
):
    """[M3.5] 按 domain 聚合实体页（供 KnowledgeScopePicker 一级 Tab）。"""
    items = await _service().list_domains(
        workspace_id=current_user.workspace_id,
        sample_per_domain=sample_per_domain,
    )
    return {"items": items}


@router.get("/quota", response_model=WikiQuotaStatus)
async def get_wiki_quota_status(
    current_user: User = Depends(get_current_user),
):
    """[M4.2] 当前工作区 Wiki 配额使用情况。

    - pages.current vs pages.limit: 已落库实体页 vs `max_pages_per_workspace`
    - links.max_outgoing_observed vs links.max_per_page: 单页最大出链 vs `max_links_per_page`
    - near_limit: 任一指标 >= 90% 时为 true，前端可高亮告警
    """
    return await _service().get_quota_status(
        workspace_id=current_user.workspace_id,
    )


@router.post(
    "/diagnostics/retrieval",
    response_model=RetrievalDiagnosticsResponse,
)
async def diagnose_retrieval(
    payload: RetrievalDiagnosticsRequest,
    admin: User = Depends(get_current_admin),
):
    """管理员召回诊断台入口。

    该接口只读执行检索诊断，不生成最终答案，也不写入 Wiki / RAG 数据。
    """
    user_context = UserContext(
        user_id=admin.id,
        workspace_id=admin.workspace_id,
        username=admin.username,
        role="user",
        capabilities=list(admin.permissions),
        is_workspace_admin=admin.is_workspace_admin,
        dept_id=admin.department_id,
        data_scope=admin.data_scope,
        roles=[],
    )
    return await get_retrieval_diagnostics_service().diagnose(
        query=payload.query,
        user_context=user_context,
        knowledge_path=payload.knowledge_path,
        doc_scope=payload.doc_scope,
        top_k=payload.top_k,
        deep_search=payload.deep_search,
    )
