"""Admin backend process entrypoint."""

from app.api.api_router_admin import api_router, health_router
from app.config import get_settings
from app.entrypoints.common import create_application

app = create_application(
    title="DeluData Admin Backend",
    description="Management APIs for tenant admins and platform operators.",
    api_router=api_router,
    health_router=health_router,
    runtime_mode="admin",
)


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.entrypoints.admin_main:app",
        host=settings.app.host,
        port=settings.app.admin_port,
        reload=settings.app.debug,
        access_log=False,
    )
