"""
知识库入库任务 API。

职责：
- 查询 ingestion task 状态与进度
- 发送取消请求
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.rbac_deps import CheckPerm
from app.core.db.database import get_async_db
get_current_admin = CheckPerm("knowledge:manage")
from app.core.security.auth import User
from app.services.task_queue_service import TaskQueueService

from .schemas import IngestionTaskResponse

router = APIRouter(tags=["tasks"])


def _to_task_response(task: Any) -> IngestionTaskResponse:
    return IngestionTaskResponse(
        task_id=task.id,
        file_id=task.file_id,
        status=task.status,
        stage=task.stage,
        progress=int(task.progress or 0),
        detail=task.detail_json or {},
        error_message=task.error_message,
        created_at=task.created_at,
        started_at=task.started_at,
        updated_at=task.updated_at,
        finished_at=task.finished_at,
    )


@router.get("/tasks/{task_id}", response_model=IngestionTaskResponse)
async def get_ingestion_task(
    task_id: str,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """查询上传入库任务状态与进度。"""
    task_queue = TaskQueueService(db)
    task = await task_queue.get_task(task_id, workspace_id=current_user.workspace_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _to_task_response(task)


@router.post("/tasks/{task_id}/cancel", response_model=IngestionTaskResponse)
async def cancel_ingestion_task(
    task_id: str,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """请求取消上传入库任务。"""
    task_queue = TaskQueueService(db)
    task = await task_queue.get_task(task_id, workspace_id=current_user.workspace_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    ok = await task_queue.request_cancel(
        task_id=task_id,
        workspace_id=current_user.workspace_id,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="任务当前状态不可取消")

    latest = await task_queue.get_task(task_id, workspace_id=current_user.workspace_id)
    if latest is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _to_task_response(latest)
