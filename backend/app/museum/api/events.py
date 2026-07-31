"""
博物馆模块 - SSE 事件流 API

遵循设计原则：
- 全异步 I/O (Async First)
- 严谨设计原则 (Design Rigor): 心跳机制防止连接超时
- 零技术债 (Zero Tech Debt): 临时文件必须清理

功能：
- SSE 事件流推送
- 15 秒心跳保活 (使用 asyncio.wait 并发)
- 临时图片文件自动清理
"""
import asyncio
import uuid
import json
import logging
import tempfile
import os
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_user_context
from app.models.common.context import UserContext
from app.museum.config import get_museum_settings
from app.museum.services.guide_service import get_guide_service
from app.museum.services.session_repository import get_session_repository
from app.museum.models import GuideSessionCreate
from app.museum.api.guide import get_image_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["博物馆事件流"])


async def sse_generator(
    session_id: str,
    query: str,
    image_path: Optional[str],
    enable_tts: bool,
    dept_id: Optional[str],
    workspace_id: str,
    skip_person_recognition: bool = False,
):
    """
    SSE 事件生成器
    
    包含心跳机制和异常处理
    心跳设计：使用 asyncio.wait 并发，任一完成即处理
    """
    settings = get_museum_settings()
    guide_service = get_guide_service()
    
    heartbeat_interval = settings.guide_sse_heartbeat_interval
    
    try:
        # 创建导览事件迭代器
        guide_iter = guide_service.process_guide(
            session_id=session_id,
            query=query,
            image_path=image_path,
            enable_tts=enable_tts,
            dept_id=dept_id,
            workspace_id=workspace_id,
            skip_person_recognition=skip_person_recognition,
        ).__aiter__()
        
        # 使用 wait 并发心跳和事件处理
        guide_finished = False
        
        while not guide_finished:
            # 创建获取下一个事件的 task
            get_event_task = asyncio.create_task(guide_iter.__anext__())
            
            # 等待事件或超时
            done, pending = await asyncio.wait(
                {get_event_task},
                timeout=heartbeat_interval,
            )
            
            if get_event_task in done:
                try:
                    event = get_event_task.result()
                    event_type = event.get("type", "MESSAGE")
                    payload = event.get("payload", {})
                    
                    # 格式化 SSE 事件
                    yield f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    
                    # 检查是否结束
                    if event_type in ("GUIDE_END", "ERROR"):
                        guide_finished = True
                        
                except StopAsyncIteration:
                    guide_finished = True
            else:
                # 超时，发送心跳
                yield f"event: HEARTBEAT\ndata: {json.dumps({'ping': True})}\n\n"
                # 取消未完成的 task（如果有的话）
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                        
    except asyncio.CancelledError:
        logger.info(f"[SSE] 连接取消: {session_id}")
    except Exception as e:
        logger.error(f"[SSE] 处理失败: {e}", exc_info=True)
        yield f"event: ERROR\ndata: {json.dumps({'message': str(e)})}\n\n"


@router.post("/guide/stream")
async def guide_stream(
    query: str = Form(default="你好"),
    enable_tts: bool = Form(default=True),
    visitor_uuid: Optional[str] = Form(default=None),
    dept_id: Optional[str] = Form(default=None),
    image_id: Optional[str] = Form(default=None),
    image: Optional[UploadFile] = File(default=None),
    skip_person_recognition: bool = Form(default=False),
    user_context: UserContext = Depends(get_user_context),
):
    """
    导览事件流
    
    支持两种图片传递方式：
    1. image_id: 预上传图片的 ID（推荐，降低 TTFT）
    2. image: 直接上传图片文件
    
    返回 SSE 事件流，包含：
    - PERSON_ANALYSIS: 人物识别结果
    - ADJUSTMENT_PROCESS: 调节过程
    - SEARCH_RESULT: 检索结果
    - MESSAGE_CHUNK: 消息片段
    - MESSAGE_END: 消息结束
    - TTS_CHUNK: 音频片段
    - PRODUCT_RECOMMEND: 商品推荐
    - GUIDE_END: 导览结束
    - HEARTBEAT: 心跳保活
    - ERROR: 错误信息
    """
    session_repo = get_session_repository()
    
    # 生成或获取会话
    if not visitor_uuid:
        visitor_uuid = str(uuid.uuid4())
    
    # 创建新会话
    session_id = await session_repo.create(GuideSessionCreate(
        visitor_uuid=visitor_uuid,
    ), workspace_id=user_context.workspace_id)
    
    # 处理图片：优先使用 image_id（预上传），否则使用直接上传
    image_path = None
    temp_file_path = None
    
    if image_id:
        # 方式1: 使用预上传的图片（推荐）
        pre_uploaded_path = get_image_path(image_id)
        if pre_uploaded_path:
            image_path = str(pre_uploaded_path)
            logger.info(f"[SSE] 使用预上传图片: {image_id}")
        else:
            logger.warning(f"[SSE] 预上传图片不存在或已过期: {image_id}")
    elif image:
        # 方式2: 直接上传图片（兼容旧方式）
        suffix = os.path.splitext(image.filename or ".jpg")[1] or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await image.read()
            tmp.write(content)
            temp_file_path = tmp.name
            image_path = temp_file_path
        
        logger.info(f"[SSE] 保存临时图片: {image_path}")
    
    async def generate_with_cleanup():
        """带清理的生成器"""
        try:
            async for event in sse_generator(
                session_id=session_id,
                query=query,
                image_path=image_path,
                enable_tts=enable_tts,
                dept_id=dept_id,
                workspace_id=user_context.workspace_id,
                skip_person_recognition=skip_person_recognition,
            ):
                yield event
        finally:
            # 清理临时文件
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                    logger.debug(f"[SSE] 已删除临时图片: {temp_file_path}")
                except Exception as e:
                    logger.warning(f"[SSE] 删除临时图片失败: {e}")
    
    # 返回 SSE 流
    return StreamingResponse(
        generate_with_cleanup(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Session-Id": session_id,
            "X-Visitor-UUID": visitor_uuid,
        }
    )


@router.post("/guide/chat/stream")
async def guide_chat_stream(
    session_id: str = Form(...),
    message: str = Form(...),
    enable_tts: bool = Form(default=True),
    dept_id: Optional[str] = Form(default=None),
    user_context: UserContext = Depends(get_user_context),
):
    """
    导览对话事件流
    
    继续已有会话的对话
    """
    session_repo = get_session_repository()
    
    # 验证会话存在
    session = await session_repo.get(session_id, workspace_id=user_context.workspace_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    
    # 返回 SSE 流 (复用已有人物类型)
    return StreamingResponse(
        sse_generator(
            session_id=session_id,
            query=message,
            image_path=None,  # 继续对话不需要图片
            enable_tts=enable_tts,
            dept_id=dept_id,
            workspace_id=user_context.workspace_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Session-Id": session_id,
        }
    )
