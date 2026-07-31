"""API router composition for admin-backend process."""

from fastapi import APIRouter, Depends

from app.api import events, health
from app.api.deps import (
    require_workspace_kiosk_enabled,
    require_workspace_museum_enabled,
)
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.config import router as config_router
from app.api.database import router as database_router
from app.api.files import router as files_router
from app.api.knowledge import router as knowledge_router
from app.api.maintenance_records import router as maintenance_records_router
from app.api.organization import router as organization_router
from app.api.platform import auth_router as platform_auth_router
from app.api.platform import router as platform_router
from app.api.platform.admin_mgmt import router as platform_admin_mgmt_router
from app.api.platform.db_whitelist import router as platform_db_whitelist_router
from app.api.platform.knowledge_governance import (
    router as platform_knowledge_governance_router,
)
from app.api.science_admin.router import router as science_admin_router
from app.api.sandbox import router as sandbox_router
from app.api.voice import router as voice_router
from app.api.wiki import router as wiki_router
from app.museum.router import museum_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["Auth"])
api_router.include_router(chat_router, prefix="/chat", tags=["Chat"])
api_router.include_router(knowledge_router, tags=["Knowledge"])
api_router.include_router(maintenance_records_router, tags=["Maintenance Records"])
api_router.include_router(admin_router, prefix="/admin", tags=["Admin"])
api_router.include_router(
    science_admin_router,
    tags=["Science Admin"],
    dependencies=[Depends(require_workspace_kiosk_enabled)],
)
api_router.include_router(config_router, prefix="/config", tags=["Config"])
api_router.include_router(database_router, prefix="/db", tags=["Database"])
api_router.include_router(organization_router, tags=["Organization"])
api_router.include_router(files_router, prefix="/files", tags=["Files"])
api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(sandbox_router, prefix="/sandbox", tags=["Sandbox"])
api_router.include_router(wiki_router, tags=["Wiki"])
api_router.include_router(
    museum_router,
    prefix="/museum",
    tags=["Museum"],
    dependencies=[Depends(require_workspace_museum_enabled)],
)
api_router.include_router(voice_router, tags=["Voice"])
api_router.include_router(platform_router, tags=["Platform"])
api_router.include_router(platform_auth_router, prefix="/platform", tags=["Platform Auth"])
api_router.include_router(
    platform_admin_mgmt_router, prefix="/platform", tags=["Platform Admin Mgmt"]
)
api_router.include_router(platform_db_whitelist_router, tags=["Platform DB Whitelist"])
api_router.include_router(
    platform_knowledge_governance_router,
    tags=["Platform Knowledge Governance"],
)

health_router = health.router
