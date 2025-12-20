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

def _extract_image_paths(code: str) -> set[str]:
    """Extract image file paths from Typst code.
    Handles both old format: #image("path")
    and new format: #align(center, image("path", ...))
    Returns a set of relative paths like "projects/123/images/file.jpg"
    """
    paths = set()
    # Match both formats
    old_pattern = r'#image\("([^"]+)"\)'
    new_pattern = r'#align\(center,\s*image\("([^"]+)"'
    
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
    # New: #align(center, image("/static/path", width: ..., height: ...))
    pattern = r'(#(?:align\(center,\s*)?image\()"(/static/[^"]+)"'
    
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
