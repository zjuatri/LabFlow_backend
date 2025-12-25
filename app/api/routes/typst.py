
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from .typst_charts import router as typst_charts_router
from .typst_images import router as typst_images_router
from .typst_render import router as typst_render_router
from .typst_shared import STORAGE_ROOT, cleanup_unused_images

router = APIRouter(tags=["typst"])


def _project_storage_dir(project_id: str) -> Path:
    return STORAGE_ROOT / "projects" / project_id


router.include_router(typst_render_router)
router.include_router(typst_images_router)
router.include_router(typst_charts_router)
