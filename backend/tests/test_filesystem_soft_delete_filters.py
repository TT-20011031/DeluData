import sys

import pytest
from sqlalchemy import select

sys.path.insert(0, ".")

from app.models.knowledge.graph import File
from app.services.filesystem_service import FilesystemService


def test_file_queries_exclude_soft_deleted_by_default():
    service = FilesystemService(db=None, workspace_id="ws_test")  # type: ignore[arg-type]
    stmt = service._filter_by_workspace(select(File), File)
    sql = str(stmt)

    assert "files.workspace_id" in sql
    assert "files.is_deleted IS false" in sql


def test_file_queries_can_include_soft_deleted_when_needed():
    service = FilesystemService(db=None, workspace_id="ws_test")  # type: ignore[arg-type]
    stmt = service._filter_by_workspace(select(File), File, include_deleted=True)
    sql = str(stmt)

    assert "files.workspace_id" in sql
    assert "files.is_deleted IS false" not in sql


class _EmptyScalars:
    def all(self):
        return []


class _EmptyResult:
    def scalars(self):
        return _EmptyScalars()


class _CapturingDb:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _EmptyResult()


@pytest.mark.asyncio
async def test_workspace_admin_default_structure_does_not_filter_files_by_uploader():
    db = _CapturingDb()
    service = FilesystemService(db=db, workspace_id="ws_test")  # type: ignore[arg-type]

    await service.get_folder_structure(
        user_id="workspace-owner",
        is_workspace_admin=True,
    )

    file_sql = str(db.statements[1])
    assert "files.workspace_id" in file_sql
    where_sql = file_sql.split("WHERE", 1)[1]
    assert "files.user_id" not in where_sql
