from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import re
import shutil

from sqlalchemy import select

from ...defaults import DEFAULT_TYPST_CODE
from ...models import Project

# Filesystem storage root (same as mounted /static in main.py)
STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT") or Path(__file__).resolve().parent.parent.parent / "storage")


def _project_storage_dir(project_id: str) -> Path:
    return STORAGE_ROOT / "projects" / project_id


def _project_images_dir(project_id: str) -> Path:
    return _project_storage_dir(project_id) / "images"


def _extract_image_paths(code: str) -> set[str]:
    """Extract image file paths from Typst code.

    Handles both old format: #image("path") and new format:
    #align(center, image("path", ...))

    Returns a set of relative paths like "projects/123/images/file.jpg".
    """

    paths: set[str] = set()
    old_pattern = r'#image\("([^"]+)"\)'
    new_pattern = r'#align\(center,\s*image\("([^"]+)"'

    for pattern in [old_pattern, new_pattern]:
        for match in re.finditer(pattern, code):
            path = match.group(1)
            if path.startswith('/static/'):
                rel_path = path[len('/static/'):]
                paths.add(rel_path)

    return paths


def cleanup_all_unreferenced_images() -> None:
    """Periodic background task: cleanup all unreferenced images across all projects."""

    try:
        from ...db import SessionLocal

        db = SessionLocal()
        try:
            projects = db.scalars(select(Project)).all()
            for project in projects:
                images_dir = _project_images_dir(project.id)
                if not images_dir.exists():
                    continue

                referenced_images = _extract_image_paths(project.typst_code or '')
                referenced_filenames = {Path(p).name for p in referenced_images}

                for img_file in images_dir.iterdir():
                    if img_file.is_file() and img_file.name not in referenced_filenames:
                        try:
                            img_file.unlink()
                        except Exception:
                            pass
        finally:
            db.close()
    except Exception:
        pass
