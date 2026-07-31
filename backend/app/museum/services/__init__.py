"""
博物馆模块 - 服务层入口

遵循设计原则：严谨设计原则 (Design Rigor)
模块化解耦与分层架构
"""
from app.museum.services.session_repository import SessionRepository, get_session_repository
from app.museum.services.sentence_buffer import SentenceBuffer
from app.museum.services.person_recognizer import PersonRecognizer, get_person_recognizer
from app.museum.services.prompt_adjuster import PromptAdjuster, get_prompt_adjuster
from app.museum.services.guide_service import GuideService, get_guide_service
from app.museum.services.tts_service import TTSConnectionPool, get_tts_connection_pool
from app.museum.services.product_service import ProductService, get_product_service
from app.museum.services.scene_analyzer import SceneAnalyzer, get_scene_analyzer

__all__ = [
    "SessionRepository",
    "get_session_repository",
    "SentenceBuffer",
    "PersonRecognizer",
    "get_person_recognizer",
    "PromptAdjuster",
    "get_prompt_adjuster",
    "GuideService",
    "get_guide_service",
    "TTSConnectionPool",
    "get_tts_connection_pool",
    "ProductService",
    "get_product_service",
    "SceneAnalyzer",
    "get_scene_analyzer",
]
