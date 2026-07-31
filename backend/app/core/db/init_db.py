"""
Database initialization helpers.
"""

import logging

from sqlalchemy import text

from .database import Base, get_async_db_manager
from app.models.config.db_config import UserDBConfigModel  # noqa: F401
from app.models.config.db_whitelist import WorkspaceDBWhitelistModel  # noqa: F401
from app.models.config.semantic import SemanticEvaluationRunModel  # noqa: F401
from app.models.config.permission_evidence import SemanticAccessEvidenceSetModel  # noqa: F401
from app.models.auth.organization_semantic import WorkspaceBusinessContextModel  # noqa: F401
from app.models.config.sql_example import SqlExampleModel  # noqa: F401
from app.experience import models as experience_models  # noqa: F401
from app.museum import db as museum_models  # noqa: F401

logger = logging.getLogger(__name__)


async def init_database():
    """Create tables and initialize RBAC permissions."""

    from app.core.security.rbac_init import init_rbac
    from app.models.auth.rbac import PermissionModel, RoleModel, UserModel  # noqa: F401
    from app.models.auth.authorization import (  # noqa: F401
        AssignmentModel,
        AuthorizationAuditEventModel,
        AuthorizationExceptionModel,
        AuthorizationRevisionModel,
        PositionModel,
        RoleBindingModel,
    )
    from app.models.knowledge.ingestion_task import IngestionTask  # noqa: F401
    from app.models.knowledge.tree_node import TreeNode  # noqa: F401

    db_manager = get_async_db_manager()

    try:
        async with db_manager.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables created/verified")

        async with db_manager.get_session() as session:
            await init_rbac(session)
            logger.info("RBAC initialized")

    except Exception as e:
        logger.error("Database init failed: %s", e)
        raise


async def check_database_connection():
    """Check DB connectivity and server version."""

    db_manager = get_async_db_manager()

    try:
        async with db_manager.engine.begin() as conn:
            result = await conn.execute(text("SELECT VERSION()"))
            version = result.scalar()
            logger.info("MySQL version: %s", version)
            return {"connected": True, "version": version}
    except Exception as e:
        logger.error("Database connection check failed: %s", e)
        return {"connected": False, "error": str(e)}
