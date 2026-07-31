"""
Phase 2 Worker 关键能力回归测试（静态断言）
"""
import sys

sys.path.insert(0, ".")


def test_config_contains_phase2_worker_settings():
    with open("app/config.py", "r", encoding="utf-8") as file_obj:
        content = file_obj.read()
    assert "max_concurrent_builds: int = 2" in content
    assert "build_max_retries: int = 2" in content
    assert "build_retry_backoff_base_sec: int = 2" in content
    assert "building_stale_minutes: int = 15" in content
    assert "default_task_priority: int = 5" in content
    assert "manual_rebuild_priority: int = 1" in content


def test_pageindex_worker_has_recovery_and_priority_queue():
    with open("app/core/rag/pageindex/pageindex_worker.py", "r", encoding="utf-8") as file_obj:
        content = file_obj.read()
    assert "asyncio.PriorityQueue" in content
    assert "RECOVERABLE_STATUSES" in content
    assert "async def recover_stale_tasks(" in content
    assert "add_done_callback(self._on_worker_done)" in content
    assert "self._queued_ids" in content
    assert "self._running_ids" in content
    assert "self._cancelled_ids" in content
    assert "if not await self._path_exists(task.file_path)" in content
    assert "await asyncio.to_thread(os.path.exists, file_path)" in content
    assert "isinstance(task, PageIndexTask)" in content
    assert "duplicate_count" in content
    assert "FilesystemService(session, workspace_id=target_workspace_id)" in content
    assert "filesystem.update_pageindex_status(" in content
    assert "tasks_snapshot = list(self._worker_tasks)" in content
    assert "async def _emit_status_payload(" in content


def test_ingestion_service_replaces_create_task_with_worker_enqueue():
    with open("app/services/ingestion_service.py", "r", encoding="utf-8") as file_obj:
        content = file_obj.read()
    assert "PageIndexTask(" in content
    assert "await worker.enqueue(" in content
    assert "source=\"upload\"" in content
    assert "source=\"reindex\"" in content
    assert "self._build_pageindex_tree" not in content


def test_main_lifespan_starts_and_stops_pageindex_worker():
    with open("app/main.py", "r", encoding="utf-8") as file_obj:
        content = file_obj.read()
    assert "await pageindex_worker.start()" in content
    assert "await pageindex_worker.recover_stale_tasks()" in content
    assert "await pageindex_worker.stop()" in content


def test_events_contains_pageindex_status_protocol():
    with open("app/api/events.py", "r", encoding="utf-8") as file_obj:
        content = file_obj.read()
    assert "PAGEINDEX_STATUS = \"pageindex_status\"" in content
    assert "async def emit_pageindex_status(" in content
    assert "\"stage\"" in content
    assert "\"attempt\"" in content
    assert "\"max_retries\"" in content
    assert "\"progress\"" in content
