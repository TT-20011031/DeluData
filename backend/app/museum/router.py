"""
博物馆模块 - 路由聚合

Router of Routers 模式
"""
from fastapi import APIRouter

from app.museum.api.guide import router as guide_router
from app.museum.api.shop import router as shop_router
from app.museum.api.events import router as events_router
from app.museum.api.speech import router as speech_router
from app.museum.api.settings import router as settings_router

museum_router = APIRouter()

# 挂载子路由
museum_router.include_router(guide_router)
museum_router.include_router(shop_router)
museum_router.include_router(events_router)
museum_router.include_router(speech_router)
museum_router.include_router(settings_router)
