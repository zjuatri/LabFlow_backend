from fastapi import APIRouter

from .system import router as system_router
from .auth import router as auth_router
from .projects import router as projects_router
from .typst import router as typst_router
from .ai import router as ai_router
from .manage import router as manage_router
from .pdf_ingest import router as pdf_ingest_router
from .pdf_table_formula_vision import router as pdf_table_formula_vision_router
from .pdf_formula_vision import router as pdf_formula_vision_router

router = APIRouter()

# Keep paths identical to the original file.
router.include_router(system_router)
router.include_router(auth_router)
router.include_router(projects_router)
router.include_router(typst_router)
router.include_router(ai_router)
router.include_router(manage_router)
router.include_router(pdf_ingest_router)
router.include_router(pdf_table_formula_vision_router)
router.include_router(pdf_formula_vision_router)
