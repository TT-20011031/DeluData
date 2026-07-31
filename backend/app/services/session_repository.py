"""
会话数据仓库 (Session Repository)

职责：封装 LangGraph Checkpointer 相关的数据库操作
遵循依赖注入模式，接收 AsyncSession
"""
import logging
from typing import Optional, List
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system.checkpoints import LangGraphCheckpoints, LangGraphWrites

logger = logging.getLogger(__name__)


class SessionRepository:
    """
    会话数据仓库
    
    封装 LangGraph Checkpoints 的 CRUD 操作
    通过依赖注入接收 db session，保证事务一致性
    """
    
    def __init__(self, db: AsyncSession):
        """
        初始化
        
        Args:
            db: 外部注入的数据库会话，由 FastAPI 依赖管理生命周期
        """
        self.db = db
    
    async def list_sessions(
        self,
        limit: int = 20,
        *,
        user_id: str,
        workspace_id: str,
    ) -> List[dict]:
        """
        获取会话列表
        
        通过 MySQLSaver 获取所有会话
        
        Args:
            limit: 返回数量限制
            user_id: 用户 ID（用于隔离）
            
        Returns:
            会话列表
        """
        try:
            from app.core.db.checkpointer import MySQLSaver
            saver = MySQLSaver()
            sessions = await saver.aget_all_sessions(
                limit=limit,
                user_id=user_id,
                workspace_id=workspace_id,
            )
            return sessions
        except Exception as e:
            logger.error(f"获取会话列表失败: {e}")
            return []
    
    async def delete_session(
        self,
        session_id: str,
        *,
        user_id: str,
        workspace_id: str,
    ) -> bool:
        """
        删除会话及相关数据
        
        删除 Checkpoints 和 Writes 两张表的相关记录
        
        Args:
            session_id: 会话 ID
            
        Returns:
            是否删除成功
        """
        try:
            owned_checkpoints_result = await self.db.execute(
                select(LangGraphCheckpoints.checkpoint_id).where(
                    LangGraphCheckpoints.thread_id == session_id,
                    LangGraphCheckpoints.user_id == user_id,
                    LangGraphCheckpoints.workspace_id == workspace_id,
                )
            )
            owned_checkpoint_ids = list(owned_checkpoints_result.scalars().all())
            if not owned_checkpoint_ids:
                return False

            # 只删除当前用户拥有的 checkpoint writes
            await self.db.execute(
                delete(LangGraphWrites).where(
                    LangGraphWrites.thread_id == session_id,
                    LangGraphWrites.checkpoint_id.in_(owned_checkpoint_ids),
                )
            )
            # 只删除当前用户、当前工作区的 checkpoints
            await self.db.execute(
                delete(LangGraphCheckpoints).where(
                    LangGraphCheckpoints.thread_id == session_id,
                    LangGraphCheckpoints.user_id == user_id,
                    LangGraphCheckpoints.workspace_id == workspace_id,
                )
            )
            await self.db.commit()
            
            logger.info(f"会话已从数据库删除: {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"删除会话失败: {e}")
            await self.db.rollback()
            raise
