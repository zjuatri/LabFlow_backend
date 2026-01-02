from __future__ import annotations

import base64
import json
import os
import random
import re
import time
from datetime import datetime, timezone

import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ...db import get_db
from ...glm_client import GlmApiError, glm_chat_completions
from ...models import Project, User
from ...prompt_store import load_prompts
from ...security import get_current_user
from ...pdf_ingest import extract_pdf_payload
from ...pdf_images import extract_and_save_embedded_images
from ...pdf_render import render_pdf_pages_to_png
from .typst_shared import project_images_dir

from PIL import Image
from fastapi import Request
import shutil
import zipfile
from ...mineru_client import MinerUClient
from ..utils.text_extraction import extract_json_object
from ..utils.ocr import glm_vision_page_ocr_with_retry

router = APIRouter(tags=["pdf"])





@router.post("/projects/{project_id}/pdf/ingest-url")
async def ingest_pdf_url(
    project_id: str,
    url: str = Query(..., description="Public URL of the PDF to parse"),
    page_start: int | None = Query(None),
    page_end: int | None = Query(None),
    parser_mode: str = Query("mineru"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """MinerU-specific endpoint: parse PDF from a public URL directly."""
    import logging
    logger = logging.getLogger(__name__)
    
    # Validate project exists
    project = db.query(Project).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Prepare images directory
    images_dir = project_images_dir(project_id)
    images_dir.mkdir(parents=True, exist_ok=True)
    
    # Build page_ranges
    page_ranges = None
    if page_start is not None or page_end is not None:
        if page_start is not None and page_end is not None:
            page_ranges = f"{page_start}-{page_end}"
        elif page_start is not None:
            page_ranges = f"{page_start}-600"
        else:
            page_ranges = f"1-{page_end}"
    
    print(f"\n{'='*80}\n🔗 MinerU URL-based ingest: {url}\n   page_ranges: {page_ranges}\n{'='*80}\n", flush=True)
    
    try:
        client = MinerUClient()
        task_id = client.create_task(url, is_ocr=True, page_ranges=page_ranges)
        logger.info(f"MinerU: Task created with id={task_id}")
        
        info = client.poll_task(task_id)
        full_zip_url = info.get("full_zip_url")
        if not full_zip_url:
            raise HTTPException(status_code=500, detail="MinerU succeeded but no zip url")
        
        # Download and extract
        import requests
        zip_resp = requests.get(full_zip_url, timeout=60)
        zip_resp.raise_for_status()
        
        saved_images = []
        md_content = ""
        
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
            # Debug: log all files in the zip
            all_files = z.namelist()
            print(f"\n{'='*60}\n📦 MinerU ZIP contents ({len(all_files)} files):", flush=True)
            for name in all_files:
                print(f"   - {name}", flush=True)
            print(f"{'='*60}\n", flush=True)
            
            for name in all_files:
                lower_name = name.lower()
                
                # Extract markdown files
                if name.endswith(".md"):
                    md_content = z.read(name).decode("utf-8", errors="ignore")
                    print(f"📝 Found markdown: {name} ({len(md_content)} chars)", flush=True)
                
                # Extract image files (support various directory structures)
                # MinerU may use: images/, image/, or put images at root level
                is_image = (
                    lower_name.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')) and
                    not name.endswith("/")  # Not a directory
                )
                if is_image:
                    base_name = os.path.basename(name)
                    # Prefix with task ID to avoid collisions
                    target_name = f"mineru_{task_id[:8]}_{base_name}"
                    target_path = images_dir / target_name
                    target_path.write_bytes(z.read(name))
                    print(f"🖼️ Extracted image: {name} -> {target_name}", flush=True)
                    
                    saved_images.append({
                        "filename": target_name,
                        "url": f"/static/projects/{project_id}/images/{target_name}",
                        "page": 0,
                        "source": "mineru",
                        "original_name": base_name,  # Keep original name for MD link rewriting
                    })
        
        # Rewrite image links in markdown to use our saved paths
        for img in saved_images:
            original = img.get("original_name", "")
            if original and original in md_content:
                new_path = img["url"]
                # Handle various markdown image syntaxes
                md_content = md_content.replace(f"](images/{original})", f"]({new_path})")
                md_content = md_content.replace(f"](/images/{original})", f"]({new_path})")
                md_content = md_content.replace(f"]({original})", f"]({new_path})")
                print(f"🔗 Rewrote image link: {original} -> {new_path}", flush=True)
        
        ocr_text_pages = [{
            "page": 1,
            "text": md_content,
            "error": None
        }]
        
        return {
            "project_id": project_id,
            "parser_mode": "mineru",
            "source_url": url,
            "ocr_text_pages": ocr_text_pages,
            "images": saved_images,
            "tables": [],
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
        
    except Exception as e:
        logger.error(f"MinerU URL ingest failed: {e}")
        raise HTTPException(status_code=500, detail=f"MinerU failed: {str(e)}")


@router.post("/projects/{project_id}/pdf/ingest")
async def ingest_pdf(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    page_start: int | None = Query(default=None, ge=1),
    page_end: int | None = Query(default=None, ge=1),
    max_pages: int = 10,
    max_chars_per_page: int = 20000,
    ocr_math: bool = False,
    ocr_model: str = "glm-4.6v-flash",
    ocr_scale: float = 2.0,
    parser_mode: str = "local",
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

    parsed = None
    ocr_text_pages = None
    saved_images = []
    mineru_debug_url = None  # For frontend debugging
    
    # Ensure images dir exists
    images_dir = project_images_dir(project_id)
    images_dir.mkdir(parents=True, exist_ok=True)

    if parser_mode == "mineru":
        # === MinerU Path ===
        # 1. Save PDF to static
        # Note: We must save it to a public location.
        # We share the same directory structure: storage/projects/{id}/files/
        files_dir = images_dir.parent / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = files_dir / (file.filename or "input.pdf")
        pdf_path.write_bytes(pdf_bytes)
        
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"MinerU: PDF saved to {pdf_path} ({len(pdf_bytes)} bytes)")

        # 2. Construct Public URL
        # Priority: Env Var -> Request Base URL
        public_base = os.getenv("PUBLIC_BASE_URL")
        if not public_base:
            # Fallback to request.base_url (e.g. http://localhost:8000/)
            # Be careful with trailing slashes
            public_base = str(request.base_url).rstrip("/")
        
        # URL-encode the filename to handle non-ASCII characters (e.g., Chinese)
        from urllib.parse import quote
        encoded_filename = quote(pdf_path.name, safe='')
        pdf_url = f"{public_base}/static/projects/{project_id}/files/{encoded_filename}"
        mineru_debug_url = pdf_url  # Save for response
        logger.info(f"MinerU: Constructed PDF URL: {pdf_url}")
        print(f"\n{'='*80}\n🔗 MinerU PDF URL: {pdf_url}\n{'='*80}\n", flush=True)
        
        # 3. Call MinerU with page range support
        # Convert page_start/page_end to MinerU's page_ranges format
        page_ranges = None
        if page_start is not None or page_end is not None:
            if page_start is not None and page_end is not None:
                # Both specified: "start-end"
                page_ranges = f"{page_start}-{page_end}"
            elif page_start is not None:
                # Only start: "start-600" (assuming max 600 pages as per MinerU limit)
                page_ranges = f"{page_start}-600"
            else:
                # Only end: "1-end"
                page_ranges = f"1-{page_end}"
        
        logger.info(f"MinerU: page_ranges={page_ranges}")
        
        client = MinerUClient()
        task_id = client.create_task(pdf_url, is_ocr=True, page_ranges=page_ranges)
        logger.info(f"MinerU: Task created with id={task_id}")
        # Poll
        info = client.poll_task(task_id)
        full_zip_url = info.get("full_zip_url")
        if not full_zip_url:
            raise HTTPException(status_code=500, detail="MinerU succeeded but no zip url")

        # 4. Download and Extract Zip
        import requests
        zip_resp = requests.get(full_zip_url, timeout=60)
        zip_resp.raise_for_status()
        
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
            # MinerU zip usually has: input.md, images/xxx.jpg
            # We look for .md file
            md_content = ""
            for name in z.namelist():
                if name.endswith(".md"):
                    md_content = z.read(name).decode("utf-8", errors="ignore")
                elif name.startswith("images/") and not name.endswith("/"):
                    # Extract image to our images_dir
                    # We flatten the name or keep it? MinerU uses standard names references in MD.
                    # content matches image path.
                    # Let's save it to images_dir and we might need to adjust MD links if we wanted perfect render,
                    # but for pure text ingesting, we just need to save them so they exist.
                    # We will rename them to avoid collisions? Or kep original?
                    # MinerU images are usually named uniquely per task or generic.
                    # Let's prefix with mineru_
                    base_name = os.path.basename(name)
                    target_name = f"mineru_{task_id[:8]}_{base_name}"
                    (images_dir / target_name).write_bytes(z.read(name))
                    
                    saved_images.append({
                        "filename": target_name,
                        "url": f"/static/projects/{project_id}/images/{target_name}",
                        "page": 0, # Unknown page mapping from simple zip
                        "width": 0,
                        "height": 0,
                        "mime": "image/jpeg" if target_name.endswith(".jpg") else "image/png",
                        "source": "mineru"
                    })

        # Mock parsed object for compatibility
        # We treat the whole markdown as page 1
        class MockParsed:
            pages_text = [md_content]
            tables = []
        parsed = MockParsed()
        
        # Populate ocr_text_pages so frontend context picks it up
        ocr_text_pages = [{
            "page": 1,
            "text": md_content,
            "error": None
        }]
        
    else:
        # === Local Path (Original) ===
        parsed = extract_pdf_payload(
            pdf_bytes,
            max_pages=max_pages,
            max_chars_per_page=max_chars_per_page,
            page_start=page_start,
            page_end=page_end,
        )

    ocr_text_pages = None
    rendered_pages_for_ocr = None
    # Support OCR math only for local mode for now, or if explicitly requested on top of MinerU (unlikely needed)
    if parser_mode == "local" and ocr_math:
        prompts = load_prompts()
        pdf_ocr_prompt = str(prompts.get("pdf_page_ocr_prompt") or "")
        rendered_pages_for_ocr = render_pdf_pages_to_png(
            pdf_bytes,
            page_start=page_start,
            page_end=page_end,
            max_pages=max_pages,
            scale=ocr_scale,
        )
        ocr_text_pages = []
        # Space out OCR requests to be rate-limit friendly.
        interval_s = float(os.getenv("GLM_OCR_INTERVAL_S") or "0.6")
        for rp in rendered_pages_for_ocr:
            if interval_s > 0:
                time.sleep(interval_s)
            try:
                text = glm_vision_page_ocr_with_retry(
                    png_bytes=rp.png_bytes,
                    model=ocr_model,
                    system_prompt=pdf_ocr_prompt,
                )
                ocr_text_pages.append(
                    {
                        "page": int(rp.page_number),
                        "text": text,
                        "error": None,
                    }
                )
            except Exception as e:
                ocr_text_pages.append(
                    {
                        "page": int(rp.page_number),
                        "text": "",
                        "error": str(e),
                    }
                )

    # If MinerU is used, images_dir was already created and used
    if parser_mode == "local":
        images_dir = project_images_dir(project_id)
        images_dir.mkdir(parents=True, exist_ok=True)

        extracted_images = extract_and_save_embedded_images(
            pdf_bytes,
            project_id=project_id,
            images_dir=images_dir,
            max_images=50,
            max_bytes=2_000_000,
            page_start=page_start,
            page_end=page_end,
        )

        # Fallback for scanned PDFs / uncommon encodings:
        # if we couldn't extract any embedded images, save rendered page previews as PNG.
        if not extracted_images:
            page_previews = rendered_pages_for_ocr
            if page_previews is None:
                page_previews = render_pdf_pages_to_png(
                    pdf_bytes,
                    page_start=page_start,
                    page_end=page_end,
                    max_pages=max_pages,
                    scale=1.5,
                )

            for rp in page_previews:
                filename = f"page_p{int(rp.page_number)}_render.png"
                dest = images_dir / filename
                try:
                    # Ensure we always write a valid PNG (rp.png_bytes should already be PNG).
                    im = Image.open(io.BytesIO(rp.png_bytes))
                    im.load()
                    buf = io.BytesIO()
                    im.save(buf, format="PNG", optimize=True)
                    dest.write_bytes(buf.getvalue())
                    extracted_images.append(
                        {
                            "filename": filename,
                            "mime": "image/png",
                            "width": int(im.width),
                            "height": int(im.height),
                            "page_number": int(rp.page_number),
                            "source": "page_render",
                        }
                    )
                except Exception:
                    # If something went wrong, skip the preview.
                    continue

        for img in extracted_images:
            filename = img.filename if hasattr(img, "filename") else str(img.get("filename"))
            public_url = f"/static/projects/{project_id}/images/{filename}"
            source = "embedded"
            if not hasattr(img, "filename"):
                source = str(img.get("source") or "embedded")
            saved_images.append(
                {
                    "filename": filename,
                    "url": public_url,
                    "page": (img.page_number if hasattr(img, "page_number") else int(img.get("page_number") or 0)),
                    "width": (img.width if hasattr(img, "width") else img.get("width")),
                    "height": (img.height if hasattr(img, "height") else img.get("height")),
                    "mime": (img.mime if hasattr(img, "mime") else str(img.get("mime") or "image/png")),
                    "source": source,
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
            "ocr_math": bool(ocr_math),
            "ocr_model": ocr_model,
            "ocr_scale": float(ocr_scale),
            "parser_mode": parser_mode,
        },
        "text_pages": parsed.pages_text,
        "ocr_text_pages": ocr_text_pages,
        "tables": tables,
        "images": saved_images,
        "mineru_debug_url": mineru_debug_url,  # For debugging MinerU access issues
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
