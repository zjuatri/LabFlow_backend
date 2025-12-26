from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Project, User
from ...security import get_current_user
from ...pdf_ingest import extract_pdf_payload
from ...pdf_images import extract_and_save_embedded_images
from .typst_shared import project_images_dir

router = APIRouter(tags=["pdf"])


@router.post("/projects/{project_id}/pdf/ingest")
async def ingest_pdf(
    project_id: str,
    file: UploadFile = File(...),
    page_start: int | None = Query(default=None, ge=1),
    page_end: int | None = Query(default=None, ge=1),
    max_pages: int = 10,
    max_chars_per_page: int = 20000,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf is supported")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    if page_start is not None and page_end is not None and page_end < page_start:
        raise HTTPException(status_code=400, detail="page_end must be >= page_start")

    parsed = extract_pdf_payload(
        pdf_bytes,
        max_pages=max_pages,
        max_chars_per_page=max_chars_per_page,
        page_start=page_start,
        page_end=page_end,
    )

    images_dir = project_images_dir(project_id)
    images_dir.mkdir(parents=True, exist_ok=True)

    saved_images = []
    extracted_images = extract_and_save_embedded_images(
        pdf_bytes,
        project_id=project_id,
        images_dir=images_dir,
        max_images=50,
        max_bytes=2_000_000,
        page_start=page_start,
        page_end=page_end,
    )
    for img in extracted_images:
        public_url = f"/static/projects/{project_id}/images/{img.filename}"
        saved_images.append(
            {
                "filename": img.filename,
                "url": public_url,
                "page": img.page_number,
                "width": img.width,
                "height": img.height,
                "mime": img.mime,
            }
        )

    tables = []
    for t in parsed.tables:
        tables.append(
            {
                "page": t.page_number,
                "rows": t.rows,
                "cols": t.cols,
                "tablePayload": {
                    "caption": f"PDF表格（第{t.page_number}页）",
                    "style": "three-line",
                    "rows": t.rows,
                    "cols": t.cols,
                    "cells": t.cells,
                },
                "csv_preview": t.csv_preview,
            }
        )

    return {
        "project_id": project_id,
        "filename": file.filename,
        "limits": {
            "max_pages": max_pages,
            "max_chars_per_page": max_chars_per_page,
            "page_start": page_start,
            "page_end": page_end,
        },
        "text_pages": parsed.pages_text,
        "tables": tables,
        "images": saved_images,
        "extracted_at": datetime.utcnow().isoformat() + "Z",
    }
