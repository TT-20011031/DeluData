"""
DeluData 智能问数系统 - 对话接口 (Router 层)

纯路由层：接收请求 -> 调用 Service -> 返回响应
业务逻辑已下沉至 ChatService，数据库操作下沉至 SessionRepository
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.common.context import UserContext
from app.api.deps import get_current_user, get_current_user_optional, get_user_context_optional
from app.core.security.auth import User
from app.core.db.database import get_async_db_manager

# Schema 层
from app.api.chat.schemas import (
    ChatRequest,
    ChatResponse,
    StartSessionRequest,
    StartSessionResponse,
    ConfirmPlanRequest,
    ConfirmPlanResponse,
    ResumeRequest,
    ResumeResponse,
    ImageUploadResponse,
)

# Service 层
from app.services.chat_service import ChatService
from app.services.session_repository import SessionRepository
from app.services.image_handler import ImageHandler

# Utils
from app.core.utils.dataframe_utils import clean_dataframe_for_json, export_dataframe_to_csv, export_dataframe_to_excel

router = APIRouter()
logger = logging.getLogger(__name__)


# ========== 依赖注入 ==========
# get_user_context 使用 deps.py 的 get_user_context_optional
get_user_context = get_user_context_optional


def get_chat_service() -> ChatService:
    """获取对话服务"""
    return ChatService()


def get_image_handler() -> ImageHandler:
    """获取图片处理器"""
    return ImageHandler()


async def get_db_session():
    """获取数据库会话"""
    db = get_async_db_manager()
    async with db.session_scope() as session:
        yield session


def get_session_repo(db: AsyncSession = Depends(get_db_session)) -> SessionRepository:
    """获取会话仓库"""
    return SessionRepository(db)


# ========== API 端点 ==========

@router.post("/upload-image", response_model=ImageUploadResponse)
async def upload_chat_image(
    file: UploadFile = File(...),
    session_id: Optional[str] = None,
    user_context: UserContext = Depends(get_user_context),
    handler: ImageHandler = Depends(get_image_handler),
):
    """
    上传聊天图片（VLM 输入）
    
    图片保存到 session 沙盒目录，返回相对路径供后续使用
    """
    import uuid
    
    sid = session_id or str(uuid.uuid4())
    image_url, image_name = await handler.save_chat_image(file, sid)
    
    return ImageUploadResponse(
        success=True,
        image_url=image_url,
        image_name=image_name,
        session_id=sid
    )


@router.post("/start", response_model=StartSessionResponse)
async def start_session(
    request: StartSessionRequest,
    background_tasks: BackgroundTasks,
    user_context: UserContext = Depends(get_user_context),
    service: ChatService = Depends(get_chat_service),
):
    """
    开启新会话（Phase 1: 生成计划）
    
    调用 Supervisor 的 Planner 节点生成任务计划。
    根据 need_confirm 判断：
    - True: 返回 draft 状态，等待用户确认
    - False: 自动触发后台执行，返回 executing 状态
    """
    try:
        result = await service.start_new_session(
            message=request.message,
            user_context=user_context,
            session_id=request.session_id,
            extra_context=request.user_context,
            reply_model_key=request.reply_model_key,
            image_url=request.image_url,
            image_name=request.image_name,
            file_path=request.file_path,
            file_name=request.file_name,
            skill_id=request.skill_id,
            execution_mode=request.execution_mode,  # [NEW] 直连模式透传
            deep_search=request.deep_search,
        )

        if result.get("status") == "completed":
            return StartSessionResponse(**result)
        
        # [Auto-Confirm] 根据 Planner 判定自动触发执行
        need_confirm = result.get("need_confirm", True)
        
        if not need_confirm:
            # 单步骤任务：自动触发后台执行（复用 confirm_and_execute 逻辑）
            logger.info(f"[Auto-Confirm] 单步骤任务，自动执行 session={result['session_id']}")
            background_tasks.add_task(
                service.confirm_and_execute,
                result["session_id"],
                result["plan_id"],
                user_context,
                result.get("steps")  # 使用原始步骤
            )
            result["status"] = "executing"  # 覆盖状态
            result["message"] = "正在执行..."
        
        return StartSessionResponse(**result)
        
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"生成计划失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm", response_model=ConfirmPlanResponse)
async def confirm_plan(
    request: ConfirmPlanRequest,
    background_tasks: BackgroundTasks,
    user_context: UserContext = Depends(get_user_context),
    service: ChatService = Depends(get_chat_service),
):
    """
    确认计划并执行（Phase 2: 执行）
    
    用户确认/修改计划后，后台异步执行 Executor 节点
    """
    session_id = request.session_id
    
    # 验证会话存在性
    if not await service.validate_session_exists(session_id, user_context):
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    
    logger.info(f"执行会话 {session_id} 的任务计划")
    
    # 后台执行（通过 BackgroundTasks，保持 Service 纯粹）
    background_tasks.add_task(
        service.confirm_and_execute,
        session_id,
        request.plan_id,
        user_context,
        request.modified_steps
    )
    
    return ConfirmPlanResponse(
        session_id=session_id,
        status="executing",
        message="计划已确认，正在执行..."
    )


@router.post("/send", response_model=ChatResponse, deprecated=True)
async def send_message(
    request: ChatRequest,
    user_context: UserContext = Depends(get_user_context)
):
    """
    发送对话消息（已废弃 - DEPRECATED）
    
    ⚠️ 此接口已废弃，请使用新的工作流：
    1. POST /api/chat/start - 生成计划
    2. POST /api/chat/confirm - 确认并执行计划
    """
    raise HTTPException(
        status_code=410,
        detail="此接口已废弃，请使用 /api/chat/start 和 /api/chat/confirm 工作流"
    )


@router.post("/resume", response_model=ResumeResponse)
async def resume_session(
    request: ResumeRequest,
    background_tasks: BackgroundTasks,
    user_context: UserContext = Depends(get_user_context),
    service: ChatService = Depends(get_chat_service),
):
    """
    恢复挂起的会话 (Inline HITL)
    
    当会话因需要用户补充信息而挂起时，传入补充内容继续执行
    """
    logger.info(f"Resume 请求: session_id={request.session_id}, input={request.input[:50]}...")
    
    # 后台执行
    background_tasks.add_task(
        service.resume_session,
        request.session_id,
        request.plan_id,
        request.input,
        user_context,
    )
    
    return ResumeResponse(
        session_id=request.session_id,
        status="resuming",
        message="正在继续执行..."
    )


@router.get("/sessions")
async def list_sessions(
    current_user: User = Depends(get_current_user),  # [SECURITY] 强制登录
    repo: SessionRepository = Depends(get_session_repo),
):
    """获取当前用户的会话列表（用户隔离）"""
    return await repo.list_sessions(
        limit=20,
        user_id=current_user.id,
        workspace_id=current_user.workspace_id,
    )


@router.get("/sessions/{session_id}/plan")
async def get_session_plan(
    session_id: str,
    user_context: UserContext = Depends(get_user_context),
    service: ChatService = Depends(get_chat_service),
):
    """
    获取会话的任务计划
    
    从 Checkpoint 恢复，支持刷新/切换后恢复任务面板
    """
    return await service.get_session_plan(session_id, user_context)


@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    user_context: UserContext = Depends(get_user_context),
    service: ChatService = Depends(get_chat_service),
):
    """获取会话历史"""
    try:
        return await service.get_session_history(session_id, user_context)
    except Exception as e:
        logger.error(f"获取会话历史失败 {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user_context: UserContext = Depends(get_user_context),
    service: ChatService = Depends(get_chat_service),
    repo: SessionRepository = Depends(get_session_repo),
):
    """删除会话"""
    # [P0 已移除内存缓存] 不再需要清除 _session_states
    
    # 从数据库删除
    try:
        deleted = await repo.delete_session(
            session_id,
            user_id=user_context.user_id,
            workspace_id=user_context.workspace_id,
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="会话不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除会话失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")
    
    return {
        "success": True,
        "session_id": session_id,
        "message": "会话已删除"
    }


# ========== DataFrame 数据预览 API ==========

@router.get("/dataframe/{session_id}/{df_key}")
async def get_dataframe(
    session_id: str,
    df_key: str,
    current_user: User = Depends(get_current_user),
    service: ChatService = Depends(get_chat_service),
):
    """
    获取 session 中的 DataFrame 数据（用于预览）
    
    从 Checkpointer 加载会话状态，提取 memory_dfs 中的 DataFrame
    """
    from app.supervisor import get_supervisor_graph
    
    graph = get_supervisor_graph()
    config = {
        "configurable": {
            "thread_id": session_id,
            "user_id": current_user.id,
            "workspace_id": current_user.workspace_id,
        }
    }
    
    try:
        state = await graph.aget_state(config)
    except Exception as e:
        logger.error(f"获取会话状态失败: {e}")
        raise HTTPException(status_code=404, detail="Session not found")
    
    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Session not found or empty")
    
    memory_dfs = state.values.get("memory_dfs", {})
    if df_key not in memory_dfs:
        raise HTTPException(status_code=404, detail=f"DataFrame '{df_key}' not found")
    
    df = memory_dfs[df_key]
    
    # 清洗 DataFrame
    df_clean = clean_dataframe_for_json(df)
    
    # 限制返回行数
    max_rows = 500
    data_slice = df_clean.head(max_rows)
    
    return {
        "key": df_key,
        "columns": df_clean.columns.tolist(),
        "data": data_slice.to_dict(orient="records"),
        "total_rows": len(df),
        "displayed_rows": len(data_slice)
    }


@router.get("/dataframe/{session_id}/{df_key}/export")
async def export_dataframe(
    session_id: str,
    df_key: str,
    format: str = "csv",
    current_user: User = Depends(get_current_user),
):
    """
    导出 DataFrame 为 CSV 或 Excel 文件
    """
    from app.supervisor import get_supervisor_graph
    
    graph = get_supervisor_graph()
    config = {
        "configurable": {
            "thread_id": session_id,
            "user_id": current_user.id,
            "workspace_id": current_user.workspace_id,
        }
    }
    
    try:
        state = await graph.aget_state(config)
    except Exception as e:
        logger.error(f"获取会话状态失败: {e}")
        raise HTTPException(status_code=404, detail="Session not found")
    
    if not state or not state.values:
        raise HTTPException(status_code=404, detail="Session not found or empty")
    
    memory_dfs = state.values.get("memory_dfs", {})
    if df_key not in memory_dfs:
        raise HTTPException(status_code=404, detail=f"DataFrame '{df_key}' not found")
    
    df = memory_dfs[df_key]
    
    if format == "excel":
        buffer = export_dataframe_to_excel(df)
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={df_key}.xlsx"}
        )
    else:
        buffer = export_dataframe_to_csv(df)
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv; charset=utf-8-sig",
            headers={"Content-Disposition": f"attachment; filename={df_key}.csv"}
        )
