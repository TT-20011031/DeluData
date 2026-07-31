"""
一次性迁移脚本：为旧会话添加标题到 metadata

运行方式: python scripts/migrate_session_titles.py
"""
import asyncio
import pickle
import sys
import os
sys.path.append(os.getcwd())

from sqlalchemy import select, update
from app.core.db.database import get_async_db_manager
from app.models.system import LangGraphCheckpoints

async def migrate():
    db = get_async_db_manager()
    updated_count = 0
    skipped_count = 0
    error_count = 0
    
    print("开始迁移会话标题...")
    
    async with db.session_scope() as session:
        # 获取所有 checkpoint
        stmt = select(LangGraphCheckpoints)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        
        print(f"找到 {len(rows)} 条检查点记录")
        
        for row in rows:
            try:
                # 检查是否已有标题
                metadata = row.metadata_ or {}
                if metadata.get("thread_title"):
                    skipped_count += 1
                    continue
                
                # 尝试从 pickle 中提取标题
                data = pickle.loads(row.checkpoint)
                channel_values = data.get("channel_values", {})
                
                title = None
                if isinstance(channel_values, dict):
                    title = channel_values.get("user_query")
                    if not title:
                        title = channel_values.get("summary")
                    
                    if title and isinstance(title, str):
                        title = title[:100]
                
                if title:
                    # 更新 metadata
                    new_metadata = metadata.copy()
                    new_metadata["thread_title"] = title
                    
                    update_stmt = update(LangGraphCheckpoints).where(
                        LangGraphCheckpoints.thread_id == row.thread_id,
                        LangGraphCheckpoints.checkpoint_id == row.checkpoint_id
                    ).values(metadata_=new_metadata)
                    
                    await session.execute(update_stmt)
                    updated_count += 1
                    print(f"  ✓ 更新: {row.thread_id[:8]}... -> {title[:30]}...")
                else:
                    skipped_count += 1
                    
            except Exception as e:
                error_count += 1
                print(f"  ✗ 错误 ({row.thread_id[:8]}...): {e}")
        
        await session.commit()
    
    print(f"\n迁移完成!")
    print(f"  更新: {updated_count}")
    print(f"  跳过: {skipped_count}")
    print(f"  错误: {error_count}")

if __name__ == "__main__":
    asyncio.run(migrate())
