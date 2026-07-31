"""Opt-in MySQL smoke test for the offline authorization migration.

Run with AUTH_V2_MYSQL_SMOKE=1 against a disposable database only.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text


def test_authorization_v2_migration_and_rollback_on_mysql() -> None:
    if os.getenv("AUTH_V2_MYSQL_SMOKE") != "1":
        pytest.skip("requires a disposable MySQL database")

    async def scenario() -> None:
        from app.core.db.init_db import init_database
        from app.core.db.database import get_async_db_manager
        from app.models.auth.organization import DepartmentModel
        from app.models.auth.rbac import PermissionModel, RoleModel, UserModel
        from app.models.auth.workspace import WorkspaceModel
        from migrations.run_authorization_v2_migration import MIGRATION_ID, run

        await init_database()
        manager = get_async_db_manager()
        async with manager.session_scope() as session:
            workspace = WorkspaceModel(
                id="smoke-workspace",
                name="迁移演练租户",
                code="smoke-auth-v2",
                owner_id="smoke-owner",
            )
            org = DepartmentModel(
                workspace_id=workspace.id,
                name="销售中心",
                code="SALES",
                ancestors="/",
                order_num=0,
                status=True,
            )
            session.add_all([workspace, org])
            await session.flush()
            wildcard = (await session.execute(
                select(PermissionModel).where(PermissionModel.code == "*")
            )).scalar_one()
            role = RoleModel(
                name="admin",
                description="ordinary name with a legacy wildcard grant",
                workspace_id=workspace.id,
                is_system=False,
                data_scope=2,
            )
            role.permissions = [wildcard]
            user = UserModel(
                id="smoke-owner",
                username="smoke-auth-v2-owner",
                hashed_password="not-used",
                workspace_id=workspace.id,
                department_id=org.id,
                disabled=False,
            )
            user.roles = [role]
            session.add_all([role, user])

        args = SimpleNamespace(
            dry_run=False,
            rollback=False,
            ownership_mapping_file=None,
            report_file=None,
        )
        await run(args)
        async with manager.session_scope() as session:
            status = (await session.execute(text(
                "SELECT status FROM sys_schema_migrations WHERE migration_id=:id"
            ), {"id": MIGRATION_ID})).scalar_one()
            assert status == "complete"
            assert int((await session.execute(text(
                "SELECT COUNT(*) FROM sys_assignments WHERE user_id='smoke-owner'"
            ))).scalar_one()) >= 1
            assert int((await session.execute(text(
                "SELECT COUNT(*) FROM sys_role_bindings WHERE workspace_id='smoke-workspace'"
            ))).scalar_one()) >= 1
            assert int((await session.execute(text("""
                SELECT COUNT(*) FROM sys_role_permissions rp
                JOIN sys_roles role ON role.id=rp.role_id
                JOIN sys_permissions permission ON permission.id=rp.permission_id
                WHERE role.workspace_id='smoke-workspace' AND permission.code='*'
            """))).scalar_one()) == 0

        args.rollback = True
        await run(args)
        async with manager.session_scope() as session:
            status = (await session.execute(text(
                "SELECT status FROM sys_schema_migrations WHERE migration_id=:id"
            ), {"id": MIGRATION_ID})).scalar_one()
            assert status == "rolled_back"
            assert int((await session.execute(text("""
                SELECT COUNT(*) FROM sys_role_permissions rp
                JOIN sys_roles role ON role.id=rp.role_id
                JOIN sys_permissions permission ON permission.id=rp.permission_id
                WHERE role.workspace_id='smoke-workspace' AND permission.code='*'
            """))).scalar_one()) == 1

    asyncio.run(scenario())
