"""
LangGraph 的 MySQL 检查点存储实现
"""
import pickle
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Tuple
from contextlib import asynccontextmanager

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, CheckpointMetadata, CheckpointTuple
from sqlalchemy import select, desc, func, delete, and_
from sqlalchemy.dialects.mysql import insert

from .database import get_async_db_manager
from app.models.system.checkpoints import LangGraphCheckpoints, LangGraphWrites

logger = logging.getLogger(__name__)


def _require_checkpoint_identity(config: RunnableConfig) -> tuple[str, str, str]:
    """Return the session identity required for every checkpoint operation."""
    configurable = config.get("configurable") or {}
    thread_id = str(configurable.get("thread_id") or "").strip()
    user_id = str(configurable.get("user_id") or "").strip()
    workspace_id = str(configurable.get("workspace_id") or "").strip()
    if not thread_id:
        raise ValueError("checkpoint config requires thread_id")
    if not user_id or not workspace_id:
        raise ValueError("checkpoint config requires user_id and workspace_id")
    return thread_id, user_id, workspace_id

class MySQLSaver(BaseCheckpointSaver):
    """
    基于 MySQL 的检查点存储器
    
    使用 `pickle` 序列化检查点状态。
    将元数据存储在 JSON 列中以便于查询，避免列表时的 pickle 反序列化问题。
    """
    
    def __init__(self):
        super().__init__()
        self.db_manager = get_async_db_manager()

    @asynccontextmanager
    async def _get_session(self):
        """获取异步数据库会话的辅助方法"""
        async with self.db_manager.session_scope() as session:
            yield session

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Optional[Dict[str, Any]] = None,
    ) -> RunnableConfig:
        """保存检查点到数据库。"""
        thread_id, user_id, workspace_id = _require_checkpoint_identity(config)
        checkpoint_id = checkpoint["id"]
        parent_checkpoint_id = config["configurable"].get("checkpoint_id")
        
        # [修复] 提取标题并存入 metadata (JSON)
        # 这允许在列出会话时无需反序列化 pickle blob 即可获取标题
        channel_values = checkpoint.get("channel_values", {})
        title = None
        if isinstance(channel_values, dict):
            # 优先使用 user_query
            title = channel_values.get("user_query")
            # 如果没有，尝试使用 summary
            if not title:
                title = channel_values.get("summary")
            
            # 截断防止过长
            if title and isinstance(title, str):
                title = title[:100]
        
        # 构造增强版元数据
        save_metadata = metadata.copy() if metadata else {}
        if title:
            save_metadata["thread_title"] = title

        # 序列化检查点
        checkpoint_blob = pickle.dumps(checkpoint)
        
        async with self._get_session() as session:
            # 使用 MySQL ON DUPLICATE KEY UPDATE 的 Upsert 逻辑
            stmt = insert(LangGraphCheckpoints).values(
                {
                    "thread_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                    "user_id": user_id,
                    "workspace_id": workspace_id,
                    "parent_checkpoint_id": parent_checkpoint_id,
                    "checkpoint": checkpoint_blob,
                    LangGraphCheckpoints.metadata_: save_metadata,
                }
            )
            
            # 更新语义：如果冲突，则更新负载（不更新 user_id，保持首次创建的值）
            stmt = stmt.on_duplicate_key_update(
                {
                    "checkpoint": checkpoint_blob,
                    "parent_checkpoint_id": parent_checkpoint_id,
                    LangGraphCheckpoints.metadata_: save_metadata,
                }
            )
            
            await session.execute(stmt)
            await session.commit()
            
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
                "user_id": user_id,
                "workspace_id": workspace_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        """存储中间写入状态。"""
        thread_id, user_id, workspace_id = _require_checkpoint_identity(config)
        checkpoint_id = config["configurable"]["checkpoint_id"]
        
        async with self._get_session() as session:
            owner_stmt = select(
                LangGraphCheckpoints.user_id,
                LangGraphCheckpoints.workspace_id,
            ).where(
                LangGraphCheckpoints.thread_id == thread_id,
                LangGraphCheckpoints.checkpoint_id == checkpoint_id,
            )
            owner_result = await session.execute(owner_stmt)
            existing_owner = owner_result.one_or_none()
            # LangGraph may persist task writes before creating the corresponding
            # checkpoint row. A missing row is therefore not an ownership failure.
            # If the row already exists, however, its exact tenant identity must match.
            if existing_owner is not None and (
                str(existing_owner.user_id or "") != user_id
                or str(existing_owner.workspace_id or "") != workspace_id
            ):
                raise PermissionError("checkpoint does not belong to current user")

            for idx, (channel, value) in enumerate(writes):
                value_blob = pickle.dumps(value)
                
                write_obj = LangGraphWrites(
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                    task_id=task_id,
                    idx=idx,
                    channel=channel,
                    type="pickle",
                    value=value_blob
                )
                await session.merge(write_obj)
            await session.commit()

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """从数据库获取检查点元组。"""
        thread_id, user_id, workspace_id = _require_checkpoint_identity(config)
        checkpoint_id = config["configurable"].get("checkpoint_id")
        
        async with self._get_session() as session:
            if checkpoint_id:
                # 获取特定检查点
                stmt = select(LangGraphCheckpoints).where(
                    LangGraphCheckpoints.thread_id == thread_id,
                    LangGraphCheckpoints.checkpoint_id == checkpoint_id,
                    LangGraphCheckpoints.user_id == user_id,
                    LangGraphCheckpoints.workspace_id == workspace_id,
                )
            else:
                # [FIX] 获取最新检查点 - 使用 checkpoint_id 排序
                stmt = select(LangGraphCheckpoints).where(
                    LangGraphCheckpoints.thread_id == thread_id,
                    LangGraphCheckpoints.user_id == user_id,
                    LangGraphCheckpoints.workspace_id == workspace_id,
                )
                stmt = stmt.order_by(desc(LangGraphCheckpoints.checkpoint_id)).limit(1)
            
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            
            if not row:
                return None
            
            # 反序列化
            try:
                checkpoint = pickle.loads(row.checkpoint)
            except Exception as e:
                logger.error(f"反序列化检查点失败 {row.checkpoint_id}: {e}")
                return None

            # 获取 'parent_config'
            parent_config = None
            if row.parent_checkpoint_id:
                parent_config = {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": row.parent_checkpoint_id,
                        "user_id": user_id,
                        "workspace_id": workspace_id,
                    }
                }
                
            # [FIX] 核心修复：始终读取 pending_writes
            # 移除 if checkpoint_id 的限制，只要拿到了 row，就用 row.checkpoint_id 查 writes
            # 这确保 aupdate_state 写入的增量状态能被正确读取
            pending_writes = []
            stmt_writes = select(LangGraphWrites).where(
                LangGraphWrites.thread_id == thread_id,
                LangGraphWrites.checkpoint_id == row.checkpoint_id  # 使用 row.checkpoint_id
            ).order_by(LangGraphWrites.task_id, LangGraphWrites.idx)
            
            result_writes = await session.execute(stmt_writes)
            write_rows = result_writes.scalars().all()
            
            for w in write_rows:
                try:
                    val = pickle.loads(w.value)
                    pending_writes.append((w.task_id, w.channel, val))
                except Exception:
                    pass
            
            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": row.checkpoint_id,
                        "user_id": user_id,
                        "workspace_id": workspace_id,
                    }
                },
                checkpoint=checkpoint,
                metadata=row.metadata_,
                parent_config=parent_config,
                pending_writes=pending_writes if pending_writes else None
            )

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """列出检查点。"""
        thread_id, user_id, workspace_id = _require_checkpoint_identity(config)
        
        async with self._get_session() as session:
            stmt = select(LangGraphCheckpoints).where(
                LangGraphCheckpoints.thread_id == thread_id,
                LangGraphCheckpoints.user_id == user_id,
                LangGraphCheckpoints.workspace_id == workspace_id,
            ).order_by(desc(LangGraphCheckpoints.created_at))
            
            if before:
                before_id = before["configurable"].get("checkpoint_id")
                if before_id:
                     subquery = select(LangGraphCheckpoints.created_at).where(
                         LangGraphCheckpoints.thread_id == thread_id,
                         LangGraphCheckpoints.checkpoint_id == before_id,
                         LangGraphCheckpoints.user_id == user_id,
                         LangGraphCheckpoints.workspace_id == workspace_id,
                     ).scalar_subquery()
                     
                     stmt = stmt.where(LangGraphCheckpoints.created_at < subquery)
            
            if limit:
                stmt = stmt.limit(limit)
                
            result = await session.execute(stmt)
            rows = result.scalars().all()
            
            for row in rows:
                try:
                    checkpoint = pickle.loads(row.checkpoint)
                    yield CheckpointTuple(
                        config={
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_id": row.checkpoint_id,
                                "user_id": user_id,
                                "workspace_id": workspace_id,
                            }
                        },
                        checkpoint=checkpoint,
                        metadata=row.metadata_,
                        parent_config={
                             "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_id": row.parent_checkpoint_id,
                                "user_id": user_id,
                                "workspace_id": workspace_id,
                            }
                        } if row.parent_checkpoint_id else None
                    )
                except Exception:
                    pass

    async def aget_all_sessions(
        self,
        limit: int = 20,
        *,
        user_id: str,
        workspace_id: str,
    ) -> List[Dict[str, Any]]:
        """
        获取会话列表 (高性能版 - 仅读取 JSON)
        
        Args:
            limit: 返回数量限制
            user_id: 当前用户 ID
            workspace_id: 当前工作区 ID
        """
        async with self._get_session() as session:
            # 1. 聚合：按 thread_id 分组，找到最新的 created_at
            base_query = select(
                LangGraphCheckpoints.thread_id,
                func.max(LangGraphCheckpoints.created_at).label("max_date")
            )
            
            if not user_id or not workspace_id:
                raise ValueError("session listing requires user_id and workspace_id")
            base_query = base_query.where(
                LangGraphCheckpoints.user_id == user_id,
                LangGraphCheckpoints.workspace_id == workspace_id,
            )
            
            threads_stmt = base_query.group_by(LangGraphCheckpoints.thread_id)\
                .order_by(desc("max_date"))\
                .limit(limit)
            
            result = await session.execute(threads_stmt)
            threads = result.all()
            
            sessions_list = []
            
            for thread_id, last_active in threads:
                # 2. 查询元数据 (仅读取 metadata_ JSON，绝不触碰 checkpoint BLOB)
                # 添加 limit(1) 防止多行错误
                meta_stmt = select(LangGraphCheckpoints.metadata_).where(
                    LangGraphCheckpoints.thread_id == thread_id,
                    LangGraphCheckpoints.created_at == last_active,
                    LangGraphCheckpoints.user_id == user_id,
                    LangGraphCheckpoints.workspace_id == workspace_id,
                ).limit(1)
                meta_result = await session.execute(meta_stmt)
                metadata = meta_result.scalar_one_or_none() or {}
                
                # 从 JSON 获取标题
                title = metadata.get("thread_title")
                
                # 兜底：如果元数据中没有标题（旧数据），使用默认值或“历史对话”
                # 这里我们不回退到 pickle，以避免崩溃。
                if not title:
                   title = f"历史对话 ({last_active.strftime('%m-%d %H:%M')})"
                
                sessions_list.append({
                    "id": thread_id,
                    "title": title,
                    "updatedAt": last_active.isoformat(),
                    "createdAt": last_active.isoformat()
                })
                
            return sessions_list

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        raise NotImplementedError("请使用 aget_tuple")

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: Optional[Dict[str, Any]] = None) -> RunnableConfig:
        raise NotImplementedError("请使用 aput")

    def put_writes(self, config: RunnableConfig, writes: Sequence[Tuple[str, Any]], task_id: str) -> None:
        raise NotImplementedError("请使用 aput_writes")
        
    def list(self, config: RunnableConfig, *, filter: Optional[Dict[str, Any]] = None, before: Optional[RunnableConfig] = None, limit: Optional[int] = None) -> AsyncIterator[CheckpointTuple]:
         raise NotImplementedError("请使用 alist")
