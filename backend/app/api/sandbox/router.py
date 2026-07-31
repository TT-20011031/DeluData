"""
临时产物 API 路由

[Security] 全部接口使用 get_user_context 强制鉴权，按 workspace_id + user_id 隔离。
[Design Rigor] 路径合法性由 TempArtifactService 内部 _assert_safe_path 校验。
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from app.api.deps import get_user_context
from app.models.common.context import UserContext
from app.services.temp_artifact_service import get_temp_artifact_service
from app.api.sandbox.schemas import TempArtifactOut, ArtifactListResponse

router = APIRouter(tags=["sandbox"])


@router.get(
    "/artifacts",
    response_model=ArtifactListResponse,
    summary="列出当前用户的所有暂存产物",
)
async def list_artifacts(
    user_ctx: UserContext = Depends(get_user_context),
):
    """
    返回当前用户（workspace_id + user_id）在过去 1 小时内生成的所有图表和文档产物。
    每次访问自动为未过期产物滑动延期（再延 1 小时）。
    """
    svc = get_temp_artifact_service()
    items = await svc.list_artifacts(
        workspace_id=user_ctx.workspace_id,
        user_id=user_ctx.user_id,
        extend_ttl=True,
    )
    out = [TempArtifactOut(**m) for m in items]
    return ArtifactListResponse(items=out, total=len(out))


@router.get(
    "/artifacts/{artifact_id}/content",
    response_class=HTMLResponse,
    summary="获取图表 HTML 内容",
)
async def get_artifact_content(
    artifact_id: str,
    user_ctx: UserContext = Depends(get_user_context),
):
    """
    返回图表产物的 HTML 内容（供前端直接渲染）。
    路径合法性已在 Service 层严格校验，防止路径穿越。
    """
    svc = get_temp_artifact_service()
    try:
        content = await svc.get_chart_content(
            workspace_id=user_ctx.workspace_id,
            user_id=user_ctx.user_id,
            artifact_id=artifact_id,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if content is None:
        raise HTTPException(status_code=404, detail="产物不存在或已过期")
    return HTMLResponse(content=content)


@router.delete(
    "/artifacts/{artifact_id}",
    status_code=204,
    summary="手动删除暂存产物",
)
async def delete_artifact(
    artifact_id: str,
    user_ctx: UserContext = Depends(get_user_context),
):
    """
    手动删除指定产物（含 HTML 文件和 meta.json）。
    """
    svc = get_temp_artifact_service()
    try:
        deleted = await svc.delete_artifact(
            workspace_id=user_ctx.workspace_id,
            user_id=user_ctx.user_id,
            artifact_id=artifact_id,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=404, detail="产物不存在")
