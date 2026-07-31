import sys
import os
import asyncio
import traceback

# Add backend directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

async def debug_sessions():
    print("Starting debug_sessions...")
    try:
        from app.core.db.checkpointer import MySQLSaver
        print("Imported MySQLSaver successfully.")
        
        saver = MySQLSaver()
        print("Initialized MySQLSaver.")
        
        print("Calling aget_all_sessions...")
        sessions = await saver.aget_all_sessions(limit=5)
        print(f"Success! Retrieved {len(sessions)} sessions.")
        for s in sessions:
            print(s)
            
    except Exception:
        print("Caught Exception:")
        traceback.print_exc()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(debug_sessions())
