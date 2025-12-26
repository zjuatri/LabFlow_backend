from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image

from app.schemas import ImageCropRequest
from .typst_shared import (
    compress_image_to_2mb,
    project_images_dir,
    STORAGE_ROOT,
)

router = APIRouter(tags=["typst"])


@router.post("/projects/{project_id}/images/upload")
async def upload_image(project_id: str, file: UploadFile = File(...)):
    try:
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="Empty file")

        compressed, forced_ext = compress_image_to_2mb(contents)

        images_dir = project_images_dir(project_id)
        images_dir.mkdir(parents=True, exist_ok=True)

        ext = forced_ext or (file.filename.split(".")[-1].lower() if file.filename and "." in file.filename else "png")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}.{ext}"
        dest_path = images_dir / filename

        dest_path.write_bytes(compressed)

        public_path = f"/static/projects/{project_id}/images/{filename}"
        # Backward/forward compatible: frontend expects `url`
        return {"url": public_path, "path": public_path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/images/crop")
async def crop_image(request: ImageCropRequest):
    try:
        # Remove query parameters if any
        clean_url = request.image_url.split("?")[0]
        if not clean_url.startswith("/static/"):
            raise HTTPException(status_code=400, detail="Invalid image_url")

        rel_path = clean_url[len("/static/"):]
        file_path = (STORAGE_ROOT / rel_path).resolve()
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Image not found")

        img = Image.open(file_path)

        # crop_* are in percentage (0-100); convert to source-image pixel coordinates
        left = int(float(request.crop_x) * img.width / 100.0)
        top = int(float(request.crop_y) * img.height / 100.0)
        right = int((float(request.crop_x) + float(request.crop_width)) * img.width / 100.0)
        bottom = int((float(request.crop_y) + float(request.crop_height)) * img.height / 100.0)

        left = max(0, min(left, img.width))
        right = max(0, min(right, img.width))
        top = max(0, min(top, img.height))
        bottom = max(0, min(bottom, img.height))

        if right <= left or bottom <= top:
            raise HTTPException(status_code=400, detail="Invalid crop region")

        cropped = img.crop((left, top, right, bottom))
        cropped.save(file_path)
        # Return 'url' to match frontend expectation
        return {"url": request.image_url, "image_url": request.image_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Compatibility route: frontend calls /projects/{project_id}/images/crop
@router.post("/projects/{project_id}/images/crop")
async def crop_image_project(project_id: str, request: ImageCropRequest):
    return await crop_image(request)
