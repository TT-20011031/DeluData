"""[治理 hook] purge_wiki_orphans_after_file_delete 单元测试。

直接测模块级函数（而非 FileDeleteService 实例方法），
避免 import FileDeleteService 类时被 models→api→filesystem 的循环 import 拖累。

覆盖：
1. 正常路径：purge 被调用，workspace_id 与 triggered_by 正确
2. purge 抛错：被捕获，不向上传播（不阻塞主删除流程）
3. workspace_id 为空：直接跳过，不调用 purge
4. purged_count=0 时不打印 info（避免日志噪声）
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
class TestPurgeHook:
    async def test_正常路径_purge被调用且参数正确(self, monkeypatch):
        # 在测试内部 lazy import，避免 module 级触发循环 import
        from app.services.file_delete_service import purge_wiki_orphans_after_file_delete

        captured: dict[str, str] = {}

        class FakeWiki:
            async def purge_orphans_for_deleted_files(self, *, workspace_id, triggered_by):
                captured["workspace_id"] = workspace_id
                captured["triggered_by"] = triggered_by
                return {"workspace_id": workspace_id, "purged_count": 3, "purged": []}

        monkeypatch.setattr(
            "app.services.wiki_service.get_wiki_service",
            lambda: FakeWiki(),
        )

        await purge_wiki_orphans_after_file_delete(workspace_id="ws-x", file_id="abc")

        assert captured["workspace_id"] == "ws-x"
        assert captured["triggered_by"] == "file_delete:abc"

    async def test_purge失败_主流程不受影响(self, monkeypatch):
        from app.services.file_delete_service import purge_wiki_orphans_after_file_delete

        class FakeWiki:
            async def purge_orphans_for_deleted_files(self, **_kw):
                raise RuntimeError("simulated chroma down")

        monkeypatch.setattr(
            "app.services.wiki_service.get_wiki_service",
            lambda: FakeWiki(),
        )

        # 期望不抛异常（仅 logger.warning）
        await purge_wiki_orphans_after_file_delete(workspace_id="ws-1", file_id="f-1")

    async def test_workspace_id_为空_直接跳过(self, monkeypatch):
        from app.services.file_delete_service import purge_wiki_orphans_after_file_delete

        called = SimpleNamespace(count=0)

        class FakeWiki:
            async def purge_orphans_for_deleted_files(self, **_kw):
                called.count += 1
                return {}

        monkeypatch.setattr(
            "app.services.wiki_service.get_wiki_service",
            lambda: FakeWiki(),
        )

        await purge_wiki_orphans_after_file_delete(workspace_id=None, file_id="f-1")
        assert called.count == 0

    async def test_purged_count_为零_不打印_info(self, monkeypatch, caplog):
        import logging

        from app.services.file_delete_service import purge_wiki_orphans_after_file_delete

        class FakeWiki:
            async def purge_orphans_for_deleted_files(self, **_kw):
                return {"purged_count": 0, "purged": []}

        monkeypatch.setattr(
            "app.services.wiki_service.get_wiki_service",
            lambda: FakeWiki(),
        )

        with caplog.at_level(logging.INFO, logger="app.services.file_delete_service"):
            await purge_wiki_orphans_after_file_delete(workspace_id="ws-1", file_id="f-1")

        assert not any("auto wiki purge_orphans triggered" in r.message for r in caplog.records)
