import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, ".")

from app.services import wiki_service as wiki_service_module
from app.services.wiki_service import WikiService


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.select_calls = 0
        self.delete_revision_calls = 0
        self.added = []

    async def execute(self, stmt):
        sql = str(stmt).upper()
        if sql.startswith("DELETE"):
            self.delete_revision_calls += 1
            return FakeRowsResult([])
        if self.select_calls == 0:
            self.select_calls += 1
            return FakeScalarResult(self.pages)
        self.select_calls += 1
        return FakeRowsResult([("f1", "deleted.md", True)])

    def add(self, obj):
        self.added.append(obj)


class FakeSessionScope:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDbManager:
    def __init__(self, session):
        self.session = session

    def session_scope(self):
        return FakeSessionScope(self.session)


@pytest.mark.asyncio
async def test_purge_orphans_archives_page_and_clears_revisions(monkeypatch):
    page = SimpleNamespace(
        id="p1",
        slug="deludata",
        title="DeluData",
        status="published",
        version=1,
    )
    session = FakeSession([page])
    monkeypatch.setattr(
        wiki_service_module,
        "get_async_db_manager",
        lambda: FakeDbManager(session),
    )

    result = await WikiService().purge_orphans_for_deleted_files(
        workspace_id="ws1",
        triggered_by="file_delete:f1",
    )

    assert result["purged_count"] == 1
    assert page.status == "archived"
    assert page.version == 2
    assert session.delete_revision_calls == 1
    assert session.added == []
