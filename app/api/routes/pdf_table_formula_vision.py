from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from starlette.requests import ClientDisconnect
from sqlalchemy.orm import Session

from ...db import get_db
from ...glm_client import GlmApiError, glm_chat_completions
from ...models import Project, User
from ...prompt_store import load_prompts
from ...security import get_current_user
from ...pdf_ingest import extract_pdf_payload
from ...pdf_render import render_pdf_crop_to_png
from ..routes.typst_shared import project_images_dir

router = APIRouter(tags=["pdf", "vision", "tables"])

# Rate-limit friendly settings
_REQUEST_INTERVAL_S = 0.3  # seconds between GLM requests
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE_S = 2.0  # exponential backoff base


def _call_glm_with_retry(model: str, messages: list, timeout_s: float = 180.0):
    """Call GLM API with retry on 429 rate-limit errors."""
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            res = glm_chat_completions(
                model=model,
                messages=messages,
                stream=False,
                thinking_enabled=False,
                clear_thinking=True,
                response_format={"type": "text"},
                timeout_s=timeout_s,
            )
            if res.ok:
                return res
            # Check for rate-limit (429)
            if res.status_code == 429:
                wait = _RETRY_BACKOFF_BASE_S * (2 ** attempt)
                time.sleep(wait)
                continue
            # Other non-OK status: don't retry
            return res
        except GlmApiError as e:
            last_exc = e
            # If the error message contains 429 / rate limit, retry
            if "429" in str(e) or "1305" in str(e):
                wait = _RETRY_BACKOFF_BASE_S * (2 ** attempt)
                time.sleep(wait)
                continue
            raise
    # Exhausted retries
    if last_exc:
        raise last_exc
    return res


def _extract_json_object(text: str) -> dict:
    """Best-effort extraction of a JSON object from model text.
    
    Handles LaTeX content by treating backslashes carefully to avoid
    JSON escape sequence issues (e.g., \\beta, \\text).
    """

    text = (text or "").strip()
    if not text:
        raise ValueError("empty content")

    # Helper to fix backslashes in JSON string values for LaTeX
    def fix_latex_escapes(json_str: str) -> str:
        """Replace single backslashes with double backslashes inside JSON string values."""
        import re
        # Match JSON string values (content between quotes, accounting for escaped quotes)
        # This regex finds "..." patterns and replaces single \ with \\
        def replace_in_string(match):
            content = match.group(1)
            # Replace single backslash with double backslash, but avoid already-escaped ones
            # This is tricky: we need to escape \ that aren't already escaped
            # Simple approach: replace all \ with \\ then fix any \\\\ back to \\
            fixed = content.replace('\\', '\\\\')
            return f'"{fixed}"'
        
        # Pattern to match string values in JSON (handles escaped quotes)
        # This matches "...", being careful about escaped quotes inside
        pattern = r'"((?:[^"\\]|\\.)*)?"'
        return re.sub(pattern, replace_in_string, json_str)

    # Fast path: try direct parse first
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # Try to locate first {...} block and fix escapes
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = text[start : end + 1]
        try:
            # Try parsing with escape fixes for LaTeX content
            fixed = fix_latex_escapes(snippet)
            obj = json.loads(fixed)
            if isinstance(obj, dict):
                return obj
        except Exception:
            # Fall back to original if fixing breaks it
            try:
                obj = json.loads(snippet)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

    raise ValueError("unable to parse JSON object")


@router.post("/projects/{project_id}/pdf/table/formula/vision")
async def pdf_table_formula_vision(
    project_id: str,
    file: UploadFile = File(...),
    page_start: int | None = Query(default=None, ge=1),
    page_end: int | None = Query(default=None, ge=1),
    max_pages: int = 2,
    render_scale: float = 2.0,
    model: str = "glm-4.6v-flash",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Parse formulas inside PDF tables and attach LaTeX back to each table cell.

    Strategy (Route A):
      1) Use pdfplumber to find structured tables (with merged-cell info).
      2) For each visible (non-placeholder) cell, crop-render that bbox into a small PNG.
      3) Send cropped cell image to GLM vision, ask it to output a JSON with `latex`.
      4) Return tables with cells extended: {content,rowspan,colspan,is_placeholder,bbox,latex?}

    Notes:
      - This endpoint focuses on *tables* only.
      - It does not require manual selection.
    """

    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf is supported")

    try:
        # Read entire upload early so proxies/clients can finish sending before
        # we start long-running work (render + multiple vision calls).
        pdf_bytes = await file.read()
    except ClientDisconnect:
        raise HTTPException(status_code=499, detail="Client disconnected during upload")
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    if page_start is not None and page_end is not None and page_end < page_start:
        raise HTTPException(status_code=400, detail="page_end must be >= page_start")

    images_dir = project_images_dir(project_id)
    images_dir.mkdir(parents=True, exist_ok=True)

    prompts = load_prompts()
    table_cell_prompt = str(prompts.get("table_cell_ocr_prompt") or "").strip()
    if not table_cell_prompt:
        table_cell_prompt = '你是一个严谨的 OCR/公式识别助手。最终输出 JSON: {"latex": ""}。'

    out_tables: list[dict] = []
    rendered_cell_images: list[dict] = []
    diagnostics: list[dict] = []

    parsed = extract_pdf_payload(
        pdf_bytes,
        max_pages=max_pages,
        max_chars_per_page=1,
        page_start=page_start,
        page_end=page_end,
    )

    if not parsed.tables:
        # This matches what you're seeing in diagnostics: both find_tables and extract_tables failed.
        diagnostics.append(
            {
                "page": page_start,
                "find_tables_count": 0,
                "extract_tables_count": 0,
                "find_table_bboxes_count": 0,
                "fallback_used": False,
                "reason": "no tables extracted by ingest pipeline (likely scanned/image-based table)",
            }
        )
    else:
        for t_index, t in enumerate(parsed.tables):
            cells_out = []
            for r_i, row in enumerate(t.cells or []):
                for c_i, cell in enumerate(row or []):
                    if bool(cell.get("is_placeholder")):
                        continue
                    bbox_obj = cell.get("bbox")
                    if not bbox_obj:
                        continue

                    bbox_tuple = (
                        float(bbox_obj.get("x0")),
                        float(bbox_obj.get("top")),
                        float(bbox_obj.get("x1")),
                        float(bbox_obj.get("bottom")),
                    )

                    # Render cell crop to an image for vision.
                    try:
                        crop = render_pdf_crop_to_png(
                            pdf_bytes,
                            page_number=int(t.page_number),
                            bbox=bbox_tuple,
                            scale=render_scale,
                            padding_px=10,
                        )
                    except Exception:
                        cells_out.append(
                            {
                                "content": str(cell.get("content") or ""),
                                "bbox": bbox_obj,
                                "latex": None,
                                "row": int(r_i),
                                "col": int(c_i),
                                "rowspan": int(cell.get("rowspan") or 1),
                                "colspan": int(cell.get("colspan") or 1),
                            }
                        )
                        continue

                    cell_img_name = f"pdfcell_p{t.page_number}_t{t_index}_r{len(cells_out)}_vision.png"
                    (images_dir / cell_img_name).write_bytes(crop.png_bytes)
                    cell_img_url = f"/static/projects/{project_id}/images/{cell_img_name}"
                    rendered_cell_images.append(
                        {
                            "page": int(t.page_number),
                            "table_index": int(t_index),
                            "cell_index": int(len(cells_out)),
                            "filename": cell_img_name,
                            "url": cell_img_url,
                            "width": crop.width,
                            "height": crop.height,
                            "bbox": bbox_obj,
                        }
                    )

                    b64 = base64.b64encode(crop.png_bytes).decode("ascii")
                    system_prompt = table_cell_prompt
                    user_prompt = {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "识别该表格单元格中的公式，输出 JSON {latex}."},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        ],
                    }

                    # Rate-limit friendly: add interval between requests
                    time.sleep(_REQUEST_INTERVAL_S)

                    try:
                        res = _call_glm_with_retry(
                            model=model,
                            messages=[{"role": "system", "content": system_prompt}, user_prompt],
                            timeout_s=180.0,
                        )
                    except GlmApiError as e:
                        raise HTTPException(status_code=500, detail=str(e))

                    if not res.ok:
                        raise HTTPException(status_code=502, detail=f"GLM upstream error: {res.status_code}: {res.text}")

                    data = {}
                    try:
                        data = res.json()
                    except Exception:
                        data = {}

                    content = None
                    try:
                        content = data.get("choices", [])[0].get("message", {}).get("content")
                    except Exception:
                        content = None

                    latex = ""
                    try:
                        obj = _extract_json_object(content or "")
                        latex = str(obj.get("latex") or "")
                    except Exception:
                        latex = ""

                    cells_out.append(
                        {
                            "content": str(cell.get("content") or ""),
                            "bbox": bbox_obj,
                            "latex": latex,
                            "row": int(r_i),
                            "col": int(c_i),
                            "rowspan": int(cell.get("rowspan") or 1),
                            "colspan": int(cell.get("colspan") or 1),
                        }
                    )

            out_tables.append(
                {
                    "page": int(t.page_number),
                    "table_index": int(t_index),
                    "cells": cells_out,
                    "rows": int(getattr(t, "rows", 0) or 0),
                    "cols": int(getattr(t, "cols", 0) or 0),
                }
            )

        diagnostics.append(
            {
                "page_start": page_start,
                "page_end": page_end,
                "tables_extracted": len(parsed.tables),
                "cells_rendered": len(rendered_cell_images),
            }
        )

    return {
        "project_id": project_id,
        "filename": file.filename,
        "model": model,
        "limits": {
            "page_start": page_start,
            "page_end": page_end,
            "max_pages": max_pages,
            "render_scale": render_scale,
        },
        "tables": out_tables,
        "rendered_cell_images": rendered_cell_images,
        "diagnostics": diagnostics,
        "extracted_at": datetime.utcnow().isoformat() + "Z",
    }
