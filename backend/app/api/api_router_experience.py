"""API router composition for experience-backend process."""

from fastapi import APIRouter

from app.api import health
from app.experience.api.router import router as experience_router

api_router = APIRouter()
api_router.include_router(experience_router, tags=["Experience"])

health_router = health.router

