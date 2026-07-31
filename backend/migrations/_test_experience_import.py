"""Quick startup test for experience-backend."""
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log_path = os.path.join(os.path.dirname(__file__), "experience_startup_log.txt")

with open(log_path, "w", encoding="utf-8") as f:
    try:
        f.write("[1] Importing experience_main...\n")
        from app.entrypoints.experience_main import app
        f.write(f"[2] App created: {app.title}\n")
        f.write(f"[3] Routes:\n")
        for route in app.routes:
            f.write(f"     {getattr(route, 'path', '?')} {getattr(route, 'methods', '')}\n")
        f.write("[DONE] Import successful.\n")
    except Exception as e:
        f.write(f"[FAIL] {e}\n")
        traceback.print_exc(file=f)
