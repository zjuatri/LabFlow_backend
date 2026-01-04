from fastapi import APIRouter

from .system import router as system_router
from .auth import router as auth_router
from .projects import router as projects_router
from .typst import router as typst_router
from . import (
    projects,
    images_summary,
    typst_images,
    pdf_ingest,
    ai,
    docs,
    pdf_table_formula_vision,
    pdf_formula_vision,
    typst_render,
    typst_charts,
    auth,
    manage,
    office_ingest
)

router = APIRouter()

router.include_router(system_router)
router.include_router(auth_router)
router.include_router(projects.router)
router.include_router(typst_router)
router.include_router(ai.router)
router.include_router(manage.router)
router.include_router(pdf_ingest.router)
router.include_router(pdf_table_formula_vision.router)
router.include_router(pdf_formula_vision.router)
router.include_router(images_summary.router)
router.include_router(docs.router)
router.include_router(office_ingest.router)

