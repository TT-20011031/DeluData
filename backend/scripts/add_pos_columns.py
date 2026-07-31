
import asyncio
from sqlalchemy import text
from app.core.db.database import get_async_db_manager

async def add_columns():
    db_manager = get_async_db_manager()
    print("Connecting to DB...")
    async with db_manager.session_scope() as session:
        print("Checking tables...")
        
        # Check folders table
        try:
            await session.execute(text("ALTER TABLE folders ADD COLUMN x INTEGER DEFAULT 0"))
            print("Added x to folders")
        except Exception as e:
            print(f"Folders x: {e}")

        try:
            await session.execute(text("ALTER TABLE folders ADD COLUMN y INTEGER DEFAULT 0"))
            print("Added y to folders")
        except Exception as e:
            print(f"Folders y: {e}")

        # Check files table
        try:
            await session.execute(text("ALTER TABLE files ADD COLUMN x INTEGER DEFAULT 0"))
            print("Added x to files")
        except Exception as e:
            print(f"Files x: {e}")

        try:
            await session.execute(text("ALTER TABLE files ADD COLUMN y INTEGER DEFAULT 0"))
            print("Added y to files")
        except Exception as e:
            print(f"Files y: {e}")
            
        await session.commit()
        print("Done.")

if __name__ == "__main__":
    import sys
    import os
    sys.path.append(os.getcwd())
    asyncio.run(add_columns())
