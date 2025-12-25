from fastapi import APIRouter

from .system import router as system_router
from .auth import router as auth_router
from .projects import router as projects_router
from .typst import router as typst_router
from .ai import router as ai_router

router = APIRouter()

# Keep paths identical to the original file.
router.include_router(system_router)
router.include_router(auth_router)
router.include_router(projects_router)
router.include_router(typst_router)
router.include_router(ai_router)
