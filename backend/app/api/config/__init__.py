"""Config API module composition."""

from fastapi import APIRouter

from .agent import router as agent_router
from .doc_styles import router as doc_styles_router
from .readiness import router as readiness_router
from .semantic import router as semantic_router
from .semantic_access_bootstrap import router as semantic_access_bootstrap_router
from .semantic_access_evidence import router as semantic_access_evidence_router
from .skills import router as skills_router
from .sql_examples import router as sql_examples_router
from .system_settings import router as system_settings_router
from .templates import router as templates_router

router = APIRouter()

router.include_router(sql_examples_router, tags=["SQL Examples"])
router.include_router(templates_router, tags=["Templates"])
router.include_router(agent_router, tags=["Agent Config"])
router.include_router(skills_router, prefix="/skills", tags=["Skills"])
router.include_router(system_settings_router, prefix="/system", tags=["System Settings"])
router.include_router(doc_styles_router, tags=["Doc Styles"])
router.include_router(readiness_router, tags=["Workspace Readiness"])
router.include_router(semantic_router, tags=["Semantic Models"])
router.include_router(semantic_access_bootstrap_router, tags=["Semantic Access Bootstrap"])
router.include_router(semantic_access_evidence_router, tags=["Semantic Access Evidence"])

__all__ = ["router"]
