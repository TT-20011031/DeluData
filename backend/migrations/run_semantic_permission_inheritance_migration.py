"""Make organization semantic policies propagate to descendants.

Run with: python -m migrations.run_semantic_permission_inheritance_migration
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select, update

from app.core.db.database import get_async_db_manager
from app.models.auth.authorization import AssignmentModel, PositionModel
from app.models.config.semantic import SemanticPolicyBindingModel
from app.services.authorization_service import bump_authorization_revision


async def migrate() -> dict[str, int]:
    db = get_async_db_manager()
    async with db.session_scope() as session:
        rows = list((await session.execute(select(SemanticPolicyBindingModel).where(
            SemanticPolicyBindingModel.target_type == "org_unit",
            SemanticPolicyBindingModel.include_descendants == False,  # noqa: E712
        ).with_for_update())).scalars())
        workspace_ids = sorted({row.workspace_id for row in rows})
        affected_accounts = 0
        for workspace_id in workspace_ids:
            affected_accounts += int((await session.execute(select(
                func.count(func.distinct(AssignmentModel.user_id)),
            ).join(
                PositionModel, PositionModel.id == AssignmentModel.position_id,
            ).where(
                AssignmentModel.workspace_id == workspace_id,
                AssignmentModel.status == True,  # noqa: E712
            ))).scalar_one() or 0)
            await bump_authorization_revision(session, workspace_id)
        if rows:
            await session.execute(update(SemanticPolicyBindingModel).where(
                SemanticPolicyBindingModel.id.in_([row.id for row in rows]),
            ).values(include_descendants=True))
        return {
            "updated_bindings": len(rows),
            "updated_workspaces": len(workspace_ids),
            "affected_accounts": affected_accounts,
        }


if __name__ == "__main__":
    print(asyncio.run(migrate()))
