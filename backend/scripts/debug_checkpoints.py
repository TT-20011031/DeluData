import asyncio
import pickle
import sys
import os
sys.path.append(os.getcwd())
from sqlalchemy import select, text
from app.core.db.database import get_async_db_manager
from app.models.system import LangGraphCheckpoints

async def debug():
    db = get_async_db_manager()
    print("Connecting to DB...")
    async with db.session_scope() as session:
        # Check count
        result = await session.execute(text("SELECT COUNT(*) FROM langgraph_checkpoints"))
        count = result.scalar()
        print(f"Total Checkpoints: {count}")
        
        if count == 0:
            print("Table is empty! Persistence is NOT working.")
            return

        # Check distinct threads using the exact query from checkpointer
        from sqlalchemy import func, desc
        threads_stmt = select(
            LangGraphCheckpoints.thread_id,
            func.max(LangGraphCheckpoints.created_at).label("max_date")
        ).group_by(LangGraphCheckpoints.thread_id).order_by(desc("max_date")).limit(10)
        
        
        print("Executing threads query...", flush=True)
        try:
            result = await session.execute(threads_stmt)
            threads = result.all()
            print(f"Threads found from DB ({len(threads)}):", flush=True)
            for t in threads:
                print(f" - Thread: {t.thread_id} | Max Date: {t.max_date}", flush=True)
                
                # Try to load latest
                cp_stmt = select(LangGraphCheckpoints).where(
                    LangGraphCheckpoints.thread_id == t.thread_id
                ).order_by(desc(LangGraphCheckpoints.created_at)).limit(1)
                cp_res = await session.execute(cp_stmt)
                cp = cp_res.scalar_one_or_none()
                if cp:
                     try:
                         blob = cp.checkpoint
                         print(f"   Checkpoint size: {len(blob)}", flush=True)
                         data = pickle.loads(blob)
                         channel_keys = list(data.get('channel_values', {}).keys())
                         print(f"   Pickle Load OK. Keys: {channel_keys}", flush=True)
                     except Exception as e:
                         print(f"   Pickle Error: {e}", flush=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Query Failed: {e}", flush=True)



if __name__ == "__main__":
    asyncio.run(debug())
