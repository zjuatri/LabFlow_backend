from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
import os
import re
import shutil

from PIL import Image, ImageDraw, ImageFont
from fastapi import HTTPException

# Filesystem storage root (same as mounted /static in main.py)
# main.py uses <repo_root>/storage (i.e., parent of app/)
STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT") or (Path(__file__).resolve().parents[3] / "storage"))

def create_placeholder_image(dest_path: Path, text: str = "图片不存在"):
    """Create a placeholder image with text if possible."""
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        width, height = 800, 600
        # Light red background
        img = Image.new('RGB', (width, height), color=(250, 235, 235))
        draw = ImageDraw.Draw(img)

        # Try to load a Chinese font on Windows
        font = None
        font_paths = [
            "C:/Windows/Fonts/msyh.ttc",   # Microsoft YaHei
            "C:/Windows/Fonts/simhei.ttf",  # SimHei
            "arial.ttf"                     # Fallback
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    font = ImageFont.truetype(fp, 60)
                    break
                except:
                    continue

        if font is None:
            font = ImageFont.load_default()

        # In newer PIL versions, textbbox is preferred
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except:
            tw, th = 400, 60 # fallback estimate

        draw.text(((width - tw) / 2, (height - th) / 2), text, fill=(200, 50, 50), font=font)
        img.save(dest_path, "JPEG")
        return True
    except Exception as e:
        print(f"Error creating placeholder image: {e}")
        return False


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
    # Match any image("...") call that references /static/ paths
    # This handles:
    # - #image("/static/...")
    # - #align(center, image("/static/..."))
    # - #block(...)[...image("/static/...")...]
    pattern = r'image\("(/static/[^"]+)"'

    def repl(m: re.Match[str]) -> str:
        url_path = m.group(1)

        if not url_path.startswith("/static/"):
            return m.group(0)

        rel_path = url_path[len("/static/"):]
        try:
            source_path = (STORAGE_ROOT / rel_path).resolve()
        except Exception:
            return m.group(0)

        if not source_path.exists():
            # Try to find a file with mineru_ prefix
            parent = source_path.parent
            basename = source_path.name
            if parent.exists():
                for f in parent.iterdir():
                    if f.name.endswith(basename) and f.is_file():
                        source_path = f
                        break
            if not source_path.exists():
                # Generate a placeholder image to prevent compilation failure
                dest_path = temp_root / rel_path
                if create_placeholder_image(dest_path):
                    return f'image("{rel_path}"'
                return m.group(0)

        dest_path = temp_root / rel_path
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)
        except Exception:
            # If copy fails, also try to generate placeholder
            if create_placeholder_image(dest_path):
                return f'image("{rel_path}"'
            return m.group(0)

        return f'image("{rel_path}"'

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
