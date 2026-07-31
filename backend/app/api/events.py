"""
DeluData 智能问数系统 - SSE 事件流接口

实现双轨并行事件流：
1. conversation 轨：对话消息、最终结果
2. telemetry 轨：任务进度、思考日志
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import AsyncGenerator, Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse


router = APIRouter()

# 全局广播频道 ID（用于系统通知，如文档处理完成）
GLOBAL_BROADCAST_CHANNEL = "global_broadcast"


class EventChannel(str, Enum):
    """事件通道"""
    CONVERSATION = "conversation"  # 会话轨：用户消息、AI 回复、Artifact
    TELEMETRY = "telemetry"        # 遥测轨：任务状态、思考日志


class EventType(str, Enum):
    """事件类型"""
    # Conversation 通道事件
    MESSAGE_START = "message_start"
    MESSAGE_CHUNK = "message_chunk"
    MESSAGE_END = "message_end"
    ARTIFACT = "artifact"
    
    # Telemetry 通道事件
    PLAN_UPDATE = "plan_update"
    STEP_UPDATE = "step_update"
    THINKING_LOG = "thinking_log"
    ERROR = "error"
    PAGEINDEX_STATUS = "pageindex_status"
    
    # [异步流式响应] 终结类任务状态事件
    CHART_STATUS = "chart_status"    # 图表生成状态 (generating/completed/error)
    FILE_RESULT = "file_result"      # 文件生成完成，带下载URL
    
    # Inline HITL 事件
    INTERRUPT = "interrupt"  # 挂起请求用户输入
    
    # [双通道反馈] Router/Worker 拟人化思考事件
    AI_THOUGHT = "ai_thought"
    
    # [LLM 推理令牌] Qwen3 thinking 模式推理流
    REASONING_CHUNK = "reasoning_chunk"  # 推理内容片段（流式）
    REASONING_END = "reasoning_end"      # 推理阶段结束（附带 duration_ms）


@dataclass
class EventEnvelope:
    """
    统一事件信封
    
    所有 SSE 事件都使用此结构封装
    """
    channel: str
    type: str
    payload: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    
    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(asdict(self), ensure_ascii=False)


class EventQueue:
    """
    事件队列管理器
    
    管理每个会话的事件队列，支持订阅和发布
    
    [修复] 添加 TTL 清理机制，防止僵尸队列导致内存泄漏
    [修复] 添加事件缓冲机制，防止 SSE 连接建立前的事件丢失
    [修复] 同一会话支持多订阅者，避免多窗口/重连时事件丢失
    """
    
    QUEUE_TTL_SECONDS = 3600  # 队列超时时间 1 小时
    EVENT_BUFFER_TTL_SECONDS = 10  # 事件缓冲 TTL 10 秒
    MAX_BUFFER_EVENTS = 500  # 每个会话最大缓冲事件数（提高容量，降低丢包概率）
    
    def __init__(self):
        self._queues: Dict[str, Dict[str, asyncio.Queue]] = {}
        self._event_buffer: Dict[str, list[tuple[datetime, EventEnvelope]]] = {}
        self._last_activity: Dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    def _prune_buffer(self, session_id: str) -> None:
        buffer = self._event_buffer.get(session_id)
        if not buffer:
            return
        cutoff = datetime.now() - timedelta(seconds=self.EVENT_BUFFER_TTL_SECONDS)
        pruned = [(ts, event) for ts, event in buffer if ts >= cutoff]
        if pruned:
            self._event_buffer[session_id] = pruned
        else:
            del self._event_buffer[session_id]

    def _append_to_buffer(self, session_id: str, events: list[EventEnvelope]) -> None:
        if not events:
            return
        self._prune_buffer(session_id)
        buffer = self._event_buffer.setdefault(session_id, [])
        for event in events:
            if len(buffer) >= self.MAX_BUFFER_EVENTS:
                buffer.pop(0)
            buffer.append((datetime.now(), event))

    @staticmethod
    def _drain_queue(queue: asyncio.Queue) -> list[EventEnvelope]:
        drained: list[EventEnvelope] = []
        while True:
            try:
                drained.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return drained
    
    async def subscribe(self, session_id: str) -> tuple[str, asyncio.Queue]:
        """
        订阅会话事件
        
        [缓冲] 连接时先回放缓冲的事件
        """
        async with self._lock:
            if session_id not in self._queues:
                self._queues[session_id] = {}

            subscriber_id = str(uuid.uuid4())
            queue: asyncio.Queue = asyncio.Queue()
            self._queues[session_id][subscriber_id] = queue

            # [缓冲] 回放缓冲事件
            self._prune_buffer(session_id)
            if session_id in self._event_buffer:
                for _, buffered_event in self._event_buffer[session_id]:
                    await queue.put(buffered_event)
                del self._event_buffer[session_id]

            self._last_activity[session_id] = datetime.now()
            return subscriber_id, queue
    
    async def publish(self, session_id: str, event: EventEnvelope):
        """
        发布事件到指定会话
        
        [缓冲] 如果没有订阅者，暂存到缓冲区
        """
        async with self._lock:
            queues = self._queues.get(session_id) or {}

            if queues:
                for queue in queues.values():
                    await queue.put(event)
            else:
                self._append_to_buffer(session_id, [event])

            self._last_activity[session_id] = datetime.now()
    
    async def broadcast(self, event: EventEnvelope):
        """
        广播事件到所有会话
        
        Args:
            event: 事件信封
        """
        async with self._lock:
            # [修复] _queues 是 Dict[session_id, Dict[subscriber_id, Queue]] 嵌套结构
            for session_id, subscribers in self._queues.items():
                for queue in subscribers.values():
                    await queue.put(event)
                self._last_activity[session_id] = datetime.now()
    
    async def unsubscribe(self, session_id: str, subscriber_id: Optional[str] = None):
        """
        取消订阅
        
        [缓冲] 若所有订阅者断开，将未投递事件缓存以便重连回放
        """
        async with self._lock:
            if session_id not in self._queues:
                return

            pending_events: list[EventEnvelope] = []
            if subscriber_id:
                queue = self._queues[session_id].pop(subscriber_id, None)
                if queue and not self._queues[session_id]:
                    pending_events = self._drain_queue(queue)
            else:
                for queue in self._queues[session_id].values():
                    pending_events.extend(self._drain_queue(queue))
                self._queues[session_id] = {}

            if not self._queues[session_id]:
                del self._queues[session_id]
                if pending_events:
                    self._append_to_buffer(session_id, pending_events)
                if session_id in self._last_activity:
                    del self._last_activity[session_id]
    
    async def cleanup_stale_queues(self) -> int:
        """
        [TTL] 清理超时的僵尸队列
        
        Returns:
            清理的队列数量
        """
        import logging
        logger = logging.getLogger(__name__)
        
        now = datetime.now()
        stale_sessions = []
        
        async with self._lock:
            for session_id, last_time in self._last_activity.items():
                age_seconds = (now - last_time).total_seconds()
                if age_seconds > self.QUEUE_TTL_SECONDS:
                    stale_sessions.append(session_id)
            
            for session_id in stale_sessions:
                del self._queues[session_id]
                del self._last_activity[session_id]
        
        return len(stale_sessions)
    
    @property
    def queue_count(self) -> int:
        """当前队列数量"""
        return len(self._queues)


# 全局事件队列
event_queue = EventQueue()


async def event_generator(
    session_id: str,
    request: Request
) -> AsyncGenerator[Dict[str, str], None]:
    """
    SSE 事件生成器
    
    Args:
        session_id: 会话ID
        request: FastAPI 请求对象
        
    Yields:
        SSE 事件数据
    """
    subscriber_id, queue = await event_queue.subscribe(session_id)
    
    try:
        # 发送连接成功事件
        yield {
            "event": "connected",
            "data": json.dumps({
                "session_id": session_id,
                "message": "SSE 连接已建立"
            })
        }
        
        # [修复] 立即发送第一个心跳，避免浏览器长时间等待
        yield {
            "event": "heartbeat",
            "data": json.dumps({"timestamp": datetime.now().isoformat()})
        }
        
        while True:
            # 检查客户端是否断开
            if await request.is_disconnected():
                break
            
            try:
                # 等待事件，超时后发送心跳
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=30.0
                )
                
                # 确保 event.type 是字符串值（处理枚举）
                event_type_str = event.type.value if hasattr(event.type, 'value') else str(event.type)
                import logging
                # 只有非 chunk 事件才记录 INFO 日志
                
                yield {
                    "event": event_type_str,  # 使用字符串值
                    "id": event.event_id,
                    "data": event.to_json()
                }
                
            except asyncio.TimeoutError:
                # 发送心跳保持连接
                yield {
                    "event": "heartbeat",
                    "data": json.dumps({"timestamp": datetime.now().isoformat()})
                }
                
    finally:
        await event_queue.unsubscribe(session_id, subscriber_id)


@router.get("/stream/{session_id}")
async def event_stream(session_id: str, request: Request):
    """
    SSE 事件流端点
    
    双轨事件流：
    - conversation: 对话消息和结果
    - telemetry: 任务进度和日志
    
    Args:
        session_id: 会话ID
        request: FastAPI 请求
        
    Returns:
        SSE 响应
    """
    return EventSourceResponse(
        event_generator(session_id, request),
        media_type="text/event-stream"
    )


@router.get("/notifications")
async def notification_stream(request: Request):
    """
    全局通知 SSE 端点
    
    用于接收全局广播通知（如文档处理完成）
    
    [修复] 使用全局广播频道而非随机ID，这样才能收到 broadcast 的消息
    """
    return EventSourceResponse(
        event_generator(GLOBAL_BROADCAST_CHANNEL, request),
        media_type="text/event-stream"
    )



# ========== 事件发布辅助函数 ==========

async def emit_message_start(
    session_id: str,
    message_id: str,
    round_index: int = 0  # [Session Round] 关联轮次
):
    """发送消息开始事件"""
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.CONVERSATION,
        type=EventType.MESSAGE_START,
        payload={
            "message_id": message_id,
            "round_index": round_index
        }
    ))


async def emit_message_chunk(
    session_id: str,
    message_id: str,
    chunk: str,
    round_index: int = 0  # [Session Round] 关联轮次
):
    """发送消息片段事件（流式输出）"""
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.CONVERSATION,
        type=EventType.MESSAGE_CHUNK,
        payload={
            "message_id": message_id,
            "chunk": chunk,
            "round_index": round_index
        }
    ))


async def emit_message_end(
    session_id: str,
    message_id: str,
    full_content: str,
    round_index: int = 0  # [Session Round] 关联轮次
):
    """发送消息结束事件"""
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.CONVERSATION,
        type=EventType.MESSAGE_END,
        payload={
            "message_id": message_id,
            "content": full_content,
            "round_index": round_index
        }
    ))


async def emit_artifact(
    session_id: str,
    artifact_type: str,
    artifact_data: Dict,
    round_index: int = 0  # [Session Round] 关联轮次
):
    """发送 Artifact 事件（图表、表格等）"""
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.CONVERSATION,
        type=EventType.ARTIFACT,
        payload={
            "type": artifact_type,
            "data": artifact_data,
            "round_index": round_index
        }
    ))


async def emit_step_update(
    session_id: str,
    step_id: str,
    status: str,
    label: str,
    parent_step_id: Optional[str] = None,
    result: Optional[str] = None,  # 支持传递结果数据
    metadata: Optional[Dict[str, Any]] = None,  # [新增] 结构化元数据
    round_index: int = 0  # [Session Round] 关联轮次
):
    """发送步骤状态更新事件

    说明：
    - step_id: 当前事件对应的步骤ID
    - parent_step_id: 子步骤归属的主步骤ID
    - result: 步骤执行结果（如生成的 SQL、分析结论等）
    - metadata: 结构化元数据（如 source_files 列表），避免前端正则解析
    - round_index: 当前执行轮次
    """
    payload: Dict[str, Any] = {
        "id": step_id,
        "status": status,
        "label": label,
        "round_index": round_index  # [Session Round] 添加轮次
    }
    if parent_step_id:
        payload["parent_step_id"] = parent_step_id
    
    # 新增：将结果放入 payload
    if result:
        payload["result"] = result
    
    # [新增] 结构化元数据
    if metadata:
        payload["metadata"] = metadata

    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.TELEMETRY,
        type=EventType.STEP_UPDATE,
        payload=payload,
    ))


async def emit_thinking_log(session_id: str, log: str):
    """发送思考日志事件"""
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.TELEMETRY,
        type=EventType.THINKING_LOG,
        payload={"log": log}
    ))


async def emit_error(session_id: str, error: str, recoverable: bool = True):
    """发送错误事件"""
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.TELEMETRY,
        type=EventType.ERROR,
        payload={"error": error, "recoverable": recoverable}
    ))


async def emit_plan_complete(session_id: str, has_error: bool = False):
    """发送计划完成事件"""
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.TELEMETRY,
        type="plan_complete",
        payload={"has_error": has_error}
    ))


async def emit_plan_update(
    session_id: str, 
    new_steps: list, 
    status: str = "confirmed",
    parent_step_id: str = None,  # [嵌套化] 父任务 ID，用于子任务挂载
    round_index: int = 0  # [Session Round] 关联轮次
):
    """
    发送计划更新事件（动态追加步骤）
    
    用于迭代式规划时，动态追加新步骤并通知前端更新任务列表
    
    Args:
        session_id: 会话ID
        new_steps: 新追加的步骤列表
        status: 计划状态 (draft/confirmed/executing)
        parent_step_id: 父任务 ID，如果指定则作为子任务挂载
        round_index: 当前执行轮次
    """
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.TELEMETRY,
        type=EventType.PLAN_UPDATE,
        payload={
            "action": "nest" if parent_step_id else "append",
            "steps": new_steps,
            "status": status,
            "parent_step_id": parent_step_id,  # 前端用于嵌套挂载
            "round_index": round_index  # [Session Round] 关联轮次
        }
    ))


async def emit_interrupt(
    session_id: str,
    message: str,
    options: list = None,
    target_step_id: str = "",
    source: str = "",
    payload: Optional[Dict[str, Any]] = None,
    signal_type: str = ""
):
    """
    发送挂起事件 (Inline HITL)
    
    当系统需要用户补充信息时调用此函数，
    前端收到后应渲染输入控件等待用户输入
    
    Args:
        session_id: 会话ID
        message: 提示用户的消息
        options: 可选的选项列表（前端可渲染为按钮）
        target_step_id: 相关的步骤ID
        source: 来源（planner/router/executor）
    """
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.CONVERSATION,
        type=EventType.INTERRUPT,
        payload={
            "message": message,
            "options": options or [],
            "target_step_id": target_step_id,
            "source": source,
            "payload": payload or {},
            "signal_type": signal_type
        }
    ))


async def emit_ai_thought(
    session_id: str,
    thought: str,
    thought_id: str = "",
    verdict: str = "",
    round_index: int = 0,
    target_step_id: str = None,  # [嵌套化] 关联的任务 step_id
    source: str = "",  # 兼容扩展：来源节点
    phase: str = "",  # 兼容扩展：阶段
    stop_reason: str = "",  # 兼容扩展：停止/路由原因
):
    """
    发送 AI 拟人化思考事件 (双通道反馈)
    
    Args:
        session_id: 会话ID
        thought: 拟人化思考内容
        thought_id: 思考节点 UUID
        verdict: 判定结果 (pass/fail/partial)
        round_index: 当前执行轮次
        target_step_id: 关联的任务 ID，前端用于嵌套展示
        source: 事件来源（如 router_agent/sql_worker）
        phase: 当前阶段（如 routing/retrieval）
        stop_reason: 本次决策或停止原因
    """
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.TELEMETRY,
        type=EventType.AI_THOUGHT,
        payload={
            "id": thought_id,
            "thought": thought,
            "verdict": verdict,
            "round_index": round_index,
            "target_step_id": target_step_id,
            "source": source,
            "phase": phase,
            "stop_reason": stop_reason,
        }
    ))


# ========== [异步流式响应] 终结类任务事件 ==========

async def emit_reasoning_chunk(
    session_id: str,
    message_id: str,
    chunk: str,
    round_index: int = 0,
):
    """发送 LLM 推理令牌片段事件（流式）"""
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.CONVERSATION,
        type=EventType.REASONING_CHUNK,
        payload={
            "message_id": message_id,
            "chunk": chunk,
            "round_index": round_index,
        }
    ))


async def emit_reasoning_end(
    session_id: str,
    message_id: str,
    duration_ms: int = 0,
    round_index: int = 0,
):
    """发送 LLM 推理阶段结束事件（附带耗时）"""
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.CONVERSATION,
        type=EventType.REASONING_END,
        payload={
            "message_id": message_id,
            "duration_ms": duration_ms,
            "round_index": round_index,
        }
    ))


async def emit_chart_status(
    session_id: str,
    step_id: str,
    status: str,
    title: str = "",
    round_index: int = 0  # [Session Round] 关联轮次
):
    """
    发送图表生成状态事件
    
    Args:
        session_id: 会话ID
        step_id: 关联的步骤ID
        status: 状态 (generating/completed/error)
        title: 图表标题（可选）
        round_index: 当前执行轮次
    """
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.TELEMETRY,
        type=EventType.CHART_STATUS,
        payload={
            "step_id": step_id,
            "status": status,
            "title": title,
            "round_index": round_index
        }
    ))


async def emit_file_result(
    session_id: str,
    step_id: str,
    file_type: str,
    file_name: str,
    download_url: str,
    round_index: int = 0  # [Session Round] 关联轮次
):
    """
    发送文件生成完成事件
    
    Args:
        session_id: 会话ID
        step_id: 关联的步骤ID
        file_type: 文件类型 (word/excel/pdf/html)
        file_name: 文件名
        download_url: 下载URL
        round_index: 当前执行轮次
    """
    await event_queue.publish(session_id, EventEnvelope(
        channel=EventChannel.TELEMETRY,
        type=EventType.FILE_RESULT,
        payload={
            "step_id": step_id,
            "file_type": file_type,
            "file_name": file_name,
            "download_url": download_url,
            "round_index": round_index  # [Session Round] 关联轮次
        }
    ))


async def emit_pageindex_status(
    *,
    file_id: str,
    workspace_id: Optional[str],
    status: str,
    stage: str,
    attempt: int,
    max_retries: int,
    progress: int,
    message: str,
    source: str,
):
    """
    发送 PageIndex 构建状态事件（全局广播）

    用于文档树索引异步构建过程的可观测上报。
    """
    await event_queue.broadcast(EventEnvelope(
        channel=EventChannel.TELEMETRY,
        type=EventType.PAGEINDEX_STATUS,
        payload={
            "file_id": file_id,
            "workspace_id": workspace_id,
            "status": status,
            "stage": stage,
            "attempt": attempt,
            "max_retries": max_retries,
            "progress": progress,
            "message": message,
            "source": source,
            "ts": datetime.now().isoformat(),
        }
    ))
