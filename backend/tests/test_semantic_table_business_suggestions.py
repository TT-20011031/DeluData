import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.semantic_query_service import SemanticQueryService


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


class SemanticTableBusinessSuggestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_pending_suggestions_for_the_requested_table(self):
        now = datetime.now()
        suggestion = SimpleNamespace(
            id=901,
            workspace_id="workspace-1",
            datasource_id=2,
            object_type="column",
            object_id=3001,
            physical_name="serial_number",
            suggested_business_name="出库单号",
            suggested_description="出库业务单据编号",
            suggested_synonyms=["出库编号"],
            confidence=0.98,
            source="llm",
            status="pending",
            evidence_json={"target_context": {"table_id": 253}},
            created_at=now,
            updated_at=now,
            accepted_at=None,
        )
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=[
                _Result(scalar=SimpleNamespace(id=253)),
                _Result(rows=[suggestion]),
            ]),
        )
        manager = SimpleNamespace(get_session=lambda: _SessionContext(session))
        service = SemanticQueryService()
        service.get_active_datasource = AsyncMock(return_value=SimpleNamespace(id=2))

        with patch(
            "app.services.semantic_query_service.get_async_db_manager",
            return_value=manager,
        ):
            rows = await service.list_table_business_suggestions(
                "workspace-1",
                253,
            )

        self.assertEqual([row["id"] for row in rows], [901])
        statement = session.execute.await_args_list[1].args[0]
        sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("semantic_business_suggestions.status = 'pending'", sql)
        self.assertIn("semantic_columns.table_id = 253", sql)
        self.assertIn("semantic_business_suggestions.workspace_id = 'workspace-1'", sql)


if __name__ == "__main__":
    unittest.main()
