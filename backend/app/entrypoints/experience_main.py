"""Experience backend process entrypoint."""

from app.api.api_router_experience import api_router, health_router
from app.config import get_settings
from app.entrypoints.common import create_application

app = create_application(
    title="DeluData Experience Backend",
    description="Guest-facing APIs for guide, quiz, and reward flows.",
    api_router=api_router,
    health_router=health_router,
    runtime_mode="experience",
)


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.entrypoints.experience_main:app",
        host=settings.app.host,
        port=settings.app.experience_port,
        reload=settings.app.debug,
        access_log=False,
    )
