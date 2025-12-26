from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
import tempfile
import os
import shutil
from io import BytesIO
from PIL import Image

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..defaults import DEFAULT_TYPST_CODE
from ..models import Project, User
from ..schemas import (
    LoginRequest,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
    RegisterRequest,
    TokenResponse,
    TypstRenderRequest,
    ChartRenderRequest,
    ImageCropRequest,
)
from ..security import create_access_token, get_current_user, hash_password, verify_password

import re

router = APIRouter()

# Filesystem storage root (same as mounted /static in main.py)
STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT") or Path(__file__).resolve().parent.parent.parent / "storage")

def _project_storage_dir(project_id: str) -> Path:
    return STORAGE_ROOT / "projects" / project_id

def _project_images_dir(project_id: str) -> Path:
    return _project_storage_dir(project_id) / "images"

def _project_charts_dir(project_id: str) -> Path:
    return _project_storage_dir(project_id) / "charts"

def _extract_image_paths(code: str) -> set[str]:
    """Extract image file paths from Typst code.
    Handles both old format: #image("path")
    and new format: #align(center, image("path", ...))
    Returns a set of relative paths like "projects/123/images/file.jpg"
    """
    paths = set()
    # Match both formats
    old_pattern = r'#image\("([^"]+)"\)'
    # Supports: #align(left, image("..." ...)), #align(center, ...), #align(right, ...)
    new_pattern = r'#align\(\s*(?:left|center|right)\s*,\s*image\("([^"]+)"'
    
    for pattern in [old_pattern, new_pattern]:
        for match in re.finditer(pattern, code):
            path = match.group(1)
            # Extract relative path from /static/... URLs
            if path.startswith('/static/'):
                rel_path = path[len('/static/'):]
                paths.add(rel_path)
    
    return paths

def _cleanup_unused_images(project_id: str, old_code: str, new_code: str) -> None:
    """Delete images that were referenced in old_code but not in new_code."""
    old_images = _extract_image_paths(old_code or '')
    new_images = _extract_image_paths(new_code or '')
    
    # Find deleted images
    deleted_images = old_images - new_images
    
    for img_rel_path in deleted_images:
        try:
            img_full_path = (STORAGE_ROOT / img_rel_path).resolve()
            if img_full_path.exists() and _project_images_dir(project_id) in img_full_path.parents:
                img_full_path.unlink()
        except Exception:
            # Ignore errors during cleanup
            pass

def _prepare_typst_compilation(code: str, temp_root: Path) -> str:
    """
    Scans code for /static/ image references, copies them to the temp directory,
    and updates the code to use relative paths. This avoids absolute path issues
    across different OSs and Typst environments.
    Handles both old #image("...") and new #align(center, image("...", ...)) formats.
    """
    # Match both old and new formats
    # Old: #image("/static/path")
    # New: #align(left|center|right, image("/static/path", width: ..., height: ...))
    pattern = r'(#(?:align\(\s*(?:left|center|right)\s*,\s*)?image\()"(/static/[^"]+)"'
    
    def repl(m: re.Match[str]) -> str:
        prefix = m.group(1)  # e.g., "#image(" or "#align(center, image("
        url_path = m.group(2)  # e.g., "/static/projects/123/img.png"
        
        # Remove /static/ prefix
        if url_path.startswith("/static/"):
            rel_path = url_path[len("/static/"):]
        else:
            return m.group(0)
            
        # Source file path
        try:
            source_path = (STORAGE_ROOT / rel_path).resolve()
        except Exception:
            return m.group(0)
        
        if not source_path.exists():
            return m.group(0)
            
        # Destination in temp dir
        # We keep the directory structure to avoid filename collisions
        dest_path = temp_root / rel_path
        
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_path)
        except Exception:
            return m.group(0)
        
        # Return relative path (forward slashes for Typst)
        # Typst in the temp dir can now find "projects/123/img.png"
        return f'{prefix}"{rel_path}"'

    return re.sub(pattern, repl, code)


def cleanup_all_unreferenced_images() -> None:
    """Periodic background task: cleanup all unreferenced images across all projects."""
    try:
        from ..db import SessionLocal
        db = SessionLocal()
        try:
            projects = db.scalars(select(Project)).all()
            for project in projects:
                images_dir = _project_images_dir(project.id)
                if not images_dir.exists():
                    continue
                
                # Get referenced images
                referenced_images = _extract_image_paths(project.typst_code or '')
                
                # Convert to just filenames for comparison
                referenced_filenames = {Path(p).name for p in referenced_images}
                
                # Delete unreferenced images
                for img_file in images_dir.iterdir():
                    if img_file.is_file() and img_file.name not in referenced_filenames:
                        try:
                            img_file.unlink()
                        except Exception:
                            pass
        finally:
            db.close()
    except Exception:
        # Silently fail - don't crash the scheduler
        pass


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/")
async def root():
    return {"message": "Welcome to LabFlow Backend"}


@router.post("/auth/register", response_model=TokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=400, detail="Email already registered (该邮箱已注册)")

    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered (该邮箱已注册)")
    db.refresh(user)

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token)


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token)


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(Project).where(Project.user_id == current_user.id).order_by(Project.updated_at.desc())
    return list(db.scalars(stmt).all())


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()
    project = Project(
        user_id=current_user.id,
        title=payload.title,
        typst_code=DEFAULT_TYPST_CODE,
        created_at=now,
        updated_at=now,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Backfill legacy projects that were created with empty typst_code.
    if not (project.typst_code or "").strip():
        project.typst_code = DEFAULT_TYPST_CODE
        project.updated_at = datetime.utcnow()
        db.add(project)
        db.commit()
        db.refresh(project)
    return project


@router.put("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Save old code to detect deleted images
    old_code = project.typst_code

    if payload.title is not None:
        project.title = payload.title
    if payload.typst_code is not None:
        project.typst_code = payload.typst_code
        # Clean up images no longer referenced
        _cleanup_unused_images(project_id, old_code, payload.typst_code)
    project.updated_at = datetime.utcnow()

    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
    # Remove project storage (images, etc.)
    try:
        shutil.rmtree(_project_storage_dir(project_id), ignore_errors=True)
    except Exception:
        # Ignore filesystem errors during cleanup
        pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/render-typst")
def render_typst(
    request: TypstRenderRequest,
    _current_user: User = Depends(get_current_user),
):
    """服务器端渲染 Typst 为 SVG（支持多页，需要登录）。"""
    typst_bin = os.getenv("TYPST_BIN") or shutil.which("typst")
    if not typst_bin:
        raise HTTPException(
            status_code=500,
            detail=(
                "Typst CLI not found. Install 'typst' and ensure it is on PATH, "
                "or set TYPST_BIN to the full path of typst.exe."
            ),
        )

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            input_file = temp_path / "input.typ"
            output_pattern = temp_path / "output-{n}.svg"

            # Copy static images to temp dir and use relative paths
            code = _prepare_typst_compilation(request.code, temp_path)
            input_file.write_text(code, encoding="utf-8")

            result = subprocess.run(
                [typst_bin, "compile", str(input_file), str(output_pattern), "--format", "svg"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                raise HTTPException(status_code=400, detail=f"Typst compilation failed: {error_msg}")

            # Collect all output SVG files (output-1.svg, output-2.svg, ...)
            svg_files = sorted(temp_path.glob("output-*.svg"))
            if not svg_files:
                raise HTTPException(status_code=500, detail="No SVG files generated")

            # Read and combine all SVG pages into a JSON array
            pages = []
            for svg_file in svg_files:
                pages.append(svg_file.read_text(encoding="utf-8"))

            return {"pages": pages}

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Typst compilation timeout")


@router.post("/projects/{project_id}/images/upload")
def upload_image(
    project_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload an image file scoped to a project. Returns a URL under /static. Max 2MB."""
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Validate extension
    original_name = Path(file.filename or "image").name
    ext = original_name.split(".")[-1].lower() if "." in original_name else ""
    allowed = {"png", "jpg", "jpeg", "gif", "webp"}
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    images_dir = _project_images_dir(project_id)
    images_dir.mkdir(parents=True, exist_ok=True)

    # Read file into memory and compress if needed
    file_data = file.file.read()
    max_size = 2 * 1024 * 1024  # 2MB

    if len(file_data) > max_size:
        # Auto-compress image
        try:
            img = Image.open(BytesIO(file_data))
            # Convert RGBA to RGB if needed (for JPEG)
            if img.mode in ('RGBA', 'LA'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = rgb_img
            # Compress: reduce quality progressively until under 2MB
            quality = 85
            while quality >= 30:
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=quality, optimize=True)
                if buffer.tell() <= max_size:
                    file_data = buffer.getvalue()
                    ext = 'jpg'  # Save compressed as JPEG
                    break
                quality -= 5
            if len(file_data) > max_size:
                raise HTTPException(status_code=413, detail="Unable to compress image below 2MB")
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=400, detail=f"Image compression failed: {str(e)}")

    # Generate unique filename to avoid collisions
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    safe_base = "uploaded"
    target_name = f"{safe_base}-{ts}.{ext}"
    target_path = images_dir / target_name

    with target_path.open("wb") as out:
        out.write(file_data)

    url = f"/static/projects/{project_id}/images/{target_name}"
    return {"url": url}


@router.post("/projects/{project_id}/images/crop")
def crop_image(
    project_id: str,
    payload: ImageCropRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crop an image based on provided crop coordinates. Returns a new image URL."""
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Extract filename from URL like "/static/projects/123/images/file.png"
    if not payload.image_url.startswith("/static/"):
        raise HTTPException(status_code=400, detail="Invalid image URL")
    
    rel_path = payload.image_url[len("/static/"):]
    source_path = (STORAGE_ROOT / rel_path).resolve()
    
    # Security check: ensure the image belongs to this project
    project_images_dir = _project_images_dir(project_id)
    if not str(source_path).startswith(str(project_images_dir)):
        raise HTTPException(status_code=403, detail="Image does not belong to this project")
    
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        # Load image
        img = Image.open(source_path)
        
        # Calculate crop box from normalized coordinates
        # The coordinates are given as percentages of the original image dimensions
        left = int((payload.crop_x / 100) * payload.image_width)
        top = int((payload.crop_y / 100) * payload.image_height)
        right = int(left + (payload.crop_width / 100) * payload.image_width)
        bottom = int(top + (payload.crop_height / 100) * payload.image_height)
        
        # Clamp to image bounds
        left = max(0, min(left, img.width))
        top = max(0, min(top, img.height))
        right = max(left + 1, min(right, img.width))
        bottom = max(top + 1, min(bottom, img.height))
        
        # Crop image
        cropped_img = img.crop((left, top, right, bottom))
        
        # Save cropped image
        images_dir = _project_images_dir(project_id)
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        ext = source_path.suffix.lstrip('.').lower() or 'png'
        target_name = f"cropped-{ts}.{ext}"
        target_path = images_dir / target_name
        
        # Convert RGBA to RGB if saving as JPEG
        if ext.lower() in {'jpg', 'jpeg'} and cropped_img.mode == 'RGBA':
            rgb_img = Image.new('RGB', cropped_img.size, (255, 255, 255))
            rgb_img.paste(cropped_img, mask=cropped_img.split()[-1])
            rgb_img.save(target_path, format='JPEG', quality=90, optimize=True)
        else:
            cropped_img.save(target_path, quality=90, optimize=True)
        
        url = f"/static/projects/{project_id}/images/{target_name}"
        return {"url": url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Image crop failed: {str(e)}")

@router.post("/projects/{project_id}/charts/render")
def render_chart(
    project_id: str,
    payload: ChartRenderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Render a chart using matplotlib and store it under /static/projects/<id>/charts."""
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Import here to avoid importing matplotlib at module import time (faster startup)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    chart_type = (payload.chart_type or "").strip().lower()
    if chart_type not in {"scatter", "bar", "pie", "hbar"}:
        raise HTTPException(status_code=400, detail="Unsupported chart type")

    data = payload.data or []
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="Invalid data")

    # Font setup (Chinese support). We try multiple common fonts and fall back gracefully.
    # This runs per request; cheap enough for typical usage.
    try:
        preferred_fonts = [
            # Windows
            "Microsoft YaHei",
            "SimHei",
            # Linux common
            "Noto Sans CJK SC",
            "Noto Sans CJK",
            "WenQuanYi Micro Hei",
            "AR PL UMing CN",
        ]
        installed = {f.name for f in font_manager.fontManager.ttflist}
        candidates = [name for name in preferred_fonts if name in installed]
        # Keep matplotlib default fallbacks too.
        if candidates:
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["font.sans-serif"] = candidates + ["DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        # Never fail chart rendering due to font detection.
        pass

    fig, ax = plt.subplots(figsize=(6, 4), dpi=150)

    try:
        if chart_type == "scatter":
            # data: [{x,y,series?}]
            # x can be numeric OR categorical (string). If any non-numeric x exists,
            # we treat x as categorical for all points.
            raw_points: list[tuple[str, object, float]] = []  # (series, x_raw, y)
            any_non_numeric_x = False
            for row in data:
                if not isinstance(row, dict):
                    continue
                x = row.get("x")
                y = row.get("y")
                try:
                    yf = float(y)
                except Exception:
                    continue
                # Detect whether x is numeric.
                xf: float | None = None
                try:
                    xf = float(x)
                    # Avoid treating empty/None as 0.
                    if x is None or (isinstance(x, str) and not x.strip()):
                        xf = None
                except Exception:
                    xf = None

                s = str(row.get("series") or "")
                if xf is None:
                    any_non_numeric_x = True
                raw_points.append((s, x, yf))

            if not raw_points:
                raise HTTPException(status_code=400, detail="No valid scatter points")

            if any_non_numeric_x:
                # Categorical x: map labels to positions.
                x_order: list[str] = []
                x_index: dict[str, int] = {}
                points_by_series: dict[str, list[tuple[int, float]]] = {}
                for s, x_raw, yf in raw_points:
                    x_label = str(x_raw).strip()
                    if not x_label:
                        continue
                    if x_label not in x_index:
                        x_index[x_label] = len(x_order)
                        x_order.append(x_label)
                    points_by_series.setdefault(s, []).append((x_index[x_label], yf))

                if not points_by_series:
                    raise HTTPException(status_code=400, detail="No valid scatter points")

                for s, pts in points_by_series.items():
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    label = s if s else None
                    ax.scatter(xs, ys, s=18, label=label)

                ax.set_xticks(list(range(len(x_order))))
                ax.set_xticklabels(x_order, rotation=30, ha="right")
            else:
                # Numeric x.
                series_map: dict[str, list[tuple[float, float]]] = {}
                for s, x_raw, yf in raw_points:
                    try:
                        xf = float(x_raw)
                    except Exception:
                        continue
                    series_map.setdefault(s, []).append((xf, yf))

                if not series_map:
                    raise HTTPException(status_code=400, detail="No valid scatter points")

                for s, pts in series_map.items():
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    label = s if s else None
                    ax.scatter(xs, ys, s=18, label=label)

            ax.set_xlabel(payload.x_label or "")
            ax.set_ylabel(payload.y_label or "")
            if payload.title:
                ax.set_title(payload.title)
            if payload.legend:
                # show legend only if there is at least one non-empty series label
                try:
                    handles, labels = ax.get_legend_handles_labels()
                    if any(l for l in labels):
                        ax.legend(loc="best")
                except Exception:
                    pass

        elif chart_type in {"bar", "hbar"}:
            # data: [{label,value,series?}]
            # group by series for grouped bars
            series_map: dict[str, dict[str, float]] = {}
            labels_order: list[str] = []

            for row in data:
                if not isinstance(row, dict):
                    continue
                label = str(row.get("label") or row.get("x") or row.get("name") or "").strip()
                if not label:
                    continue
                try:
                    val = float(row.get("value") if row.get("value") is not None else row.get("y"))
                except Exception:
                    continue
                s = str(row.get("series") or "")
                series_map.setdefault(s, {})[label] = val
                if label not in labels_order:
                    labels_order.append(label)

            if not labels_order:
                raise HTTPException(status_code=400, detail="No valid bar data")

            series_keys = list(series_map.keys())
            if len(series_keys) == 1:
                vals = [series_map[series_keys[0]].get(l, 0.0) for l in labels_order]
                if chart_type == "bar":
                    ax.bar(labels_order, vals)
                    ax.set_xlabel(payload.x_label or "")
                    ax.set_ylabel(payload.y_label or "")
                else:
                    ax.barh(labels_order, vals)
                    ax.set_xlabel(payload.x_label or "")
                    ax.set_ylabel(payload.y_label or "")
            else:
                x = list(range(len(labels_order)))
                width = 0.8 / max(1, len(series_keys))
                for idx, s in enumerate(series_keys):
                    vals = [series_map[s].get(l, 0.0) for l in labels_order]
                    label = s if s else f"series{idx+1}"
                    if chart_type == "bar":
                        xs = [xi + idx * width for xi in x]
                        ax.bar(xs, vals, width=width, label=label)
                    else:
                        ys = [yi + idx * width for yi in x]
                        ax.barh(ys, vals, height=width, label=label)

                if chart_type == "bar":
                    ax.set_xticks([xi + (len(series_keys) - 1) * width / 2 for xi in x])
                    ax.set_xticklabels(labels_order)
                    ax.set_xlabel(payload.x_label or "")
                    ax.set_ylabel(payload.y_label or "")
                else:
                    ax.set_yticks([yi + (len(series_keys) - 1) * width / 2 for yi in x])
                    ax.set_yticklabels(labels_order)
                    ax.set_xlabel(payload.x_label or "")
                    ax.set_ylabel(payload.y_label or "")

                if payload.legend:
                    ax.legend(loc="best")

            if payload.title:
                ax.set_title(payload.title)
            fig.tight_layout()

        elif chart_type == "pie":
            # data: [{label,value}] (if series present, use first series)
            series_map: dict[str, list[tuple[str, float]]] = {}
            for row in data:
                if not isinstance(row, dict):
                    continue
                label = str(row.get("label") or row.get("name") or row.get("x") or "").strip()
                if not label:
                    continue
                try:
                    val = float(row.get("value") if row.get("value") is not None else row.get("y"))
                except Exception:
                    continue
                s = str(row.get("series") or "")
                series_map.setdefault(s, []).append((label, val))

            if not series_map:
                raise HTTPException(status_code=400, detail="No valid pie data")

            # Choose a series deterministically: prefer empty series else first
            chosen_key = "" if "" in series_map else sorted(series_map.keys())[0]
            items = series_map[chosen_key]
            labels = [x[0] for x in items]
            vals = [x[1] for x in items]

            if payload.legend:
                wedges, _ = ax.pie(vals)
                ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1.0, 0.5))
            else:
                ax.pie(vals, labels=labels)

            if payload.title:
                ax.set_title(payload.title)
            ax.axis("equal")

        # Save
        charts_dir = _project_charts_dir(project_id)
        charts_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        filename = f"chart-{ts}.png"
        target_path = charts_dir / filename
        fig.savefig(target_path, format="png", bbox_inches="tight")
        url = f"/static/projects/{project_id}/charts/{filename}"
        return {"url": url}
    finally:
        plt.close(fig)
