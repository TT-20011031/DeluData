"""Wrapper to run migration with proper event loop policy on Windows."""
import asyncio
import platform
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Redirect all output to file
log_path = os.path.join(os.path.dirname(__file__), "migration_log.txt")
original_stdout = sys.stdout
original_stderr = sys.stderr
log_file = open(log_path, "w", encoding="utf-8")
sys.stdout = log_file
sys.stderr = log_file

from migrations.run_science_experience_migration import run_migration

try:
    asyncio.run(run_migration())
    print("\n[DONE] Migration script finished successfully.")
except Exception as e:
    print(f"\n[FAIL] Migration failed: {e}")
    import traceback
    traceback.print_exc()
finally:
    log_file.close()
    sys.stdout = original_stdout
    sys.stderr = original_stderr
