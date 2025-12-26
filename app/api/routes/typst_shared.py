from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
import os
import re
import shutil

from PIL import Image
from fastapi import HTTPException

# Filesystem storage root (same as mounted /static in main.py)
# main.py uses <repo_root>/storage (i.e., parent of app/)
STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT") or (Path(__file__).resolve().parents[3] / "storage"))


def project_storage_dir(project_id: str) -> Path:
    return STORAGE_ROOT / "projects" / project_id


def project_images_dir(project_id: str) -> Path:
    return project_storage_dir(project_id) / "images"


def project_charts_dir(project_id: str) -> Path:
    return project_storage_dir(project_id) / "charts"


def extract_image_paths(code: str) -> set[str]:
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


def cleanup_unused_images(project_id: str, old_code: str, new_code: str) -> None:
    old_images = extract_image_paths(old_code or '')
    new_images = extract_image_paths(new_code or '')
    for img_rel_path in (old_images - new_images):
        try:
            img_full_path = (STORAGE_ROOT / img_rel_path).resolve()
            if img_full_path.exists() and project_images_dir(project_id) in img_full_path.parents:
                img_full_path.unlink()
        except Exception:
            pass


def prepare_typst_compilation(code: str, temp_root: Path) -> str:
    pattern = r'(#(?:align\(center,\s*)?image\()"(/static/[^"]+)"'

    def repl(m: re.Match[str]) -> str:
        prefix = m.group(1)
        url_path = m.group(2)

        if not url_path.startswith("/static/"):
            return m.group(0)

        rel_path = url_path[len("/static/"):]
        try:
            source_path = (STORAGE_ROOT / rel_path).resolve()
        except Exception:
            return m.group(0)

        if not source_path.exists():
            return m.group(0)

        dest_path = temp_root / rel_path
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)
        except Exception:
            return m.group(0)

        return f'{prefix}"{rel_path}"'

    return re.sub(pattern, repl, code)


def compress_image_to_2mb(file_data: bytes) -> tuple[bytes, str]:
    """Return (new_bytes, ext). Uses JPEG when compression occurs."""

    max_size = 2 * 1024 * 1024
    if len(file_data) <= max_size:
        return file_data, ""

    try:
        img = Image.open(BytesIO(file_data))
        if img.mode in ("RGBA", "LA"):
            rgb_img = Image.new("RGB", img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = rgb_img

        quality = 85
        while quality >= 30:
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            if buf.tell() <= max_size:
                return buf.getvalue(), "jpg"
            quality -= 5

        raise HTTPException(status_code=413, detail="Unable to compress image below 2MB")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Image compression failed: {str(e)}")
