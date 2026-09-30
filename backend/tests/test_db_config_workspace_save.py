import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.config.db_config import UserDBConfig, save_workspace_db_config_async


class _SessionScope:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class WorkspaceDBConfigSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_configured_user_row_for_workspace(self):
        configured_by = "user-1"
        workspace_id = "workspace-1"
        existing = SimpleNamespace(
            user_id=configured_by,
            workspace_id=None,
            configured_by=None,
            host="old-host",
            port=3306,
            username="old-user",
            encrypted_password="old-password",
            database="old-db",
            readonly_username=None,
            readonly_encrypted_password=None,
            is_active=False,
            updated_at=None,
        )
        no_workspace_result = SimpleNamespace(scalar_one_or_none=lambda: None)
        user_result = SimpleNamespace(scalar_one_or_none=lambda: existing)
        session = SimpleNamespace(
            execute=AsyncMock(side_effect=[no_workspace_result, user_result]),
            add=unittest.mock.Mock(),
        )
        manager = SimpleNamespace(session_scope=lambda: _SessionScope(session))
        config = UserDBConfig(
            user_id=configured_by,
            workspace_id=workspace_id,
            configured_by=configured_by,
            host="47.118.50.65",
            port=3306,
            username="root",
            encrypted_password="encrypted",
            database="xinhui_test",
        )

        with patch("app.models.config.db_config.get_async_db_manager", return_value=manager):
            await save_workspace_db_config_async(workspace_id, config, configured_by)

        self.assertEqual(existing.user_id, configured_by)
        self.assertEqual(existing.workspace_id, workspace_id)
        self.assertEqual(existing.configured_by, configured_by)
        self.assertEqual(existing.host, "47.118.50.65")
        self.assertTrue(existing.is_active)
        session.add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
