from __future__ import annotations

import base64
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Project, User
from ...security import get_current_user
from ...pdf_render import render_pdf_pages_to_png
from ..routes.typst_shared import project_images_dir
from ...glm_client import GlmApiError, glm_chat_completions

router = APIRouter(tags=["pdf", "vision"])


@router.post("/projects/{project_id}/pdf/formula/vision")
async def pdf_formula_with_vision(
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
    """Use GLM vision to parse PDF pages with formulas into LaTeX/blocks.

    This endpoint is intended ONLY for PDF-formula scenarios.

    Returns:
        - rendered page images saved to project images dir
        - model output (text) that should contain LaTeX and/or blocks JSON
    """

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

    # Render a few pages to PNG (for vision)
    pages = render_pdf_pages_to_png(
        pdf_bytes,
        page_start=page_start,
        page_end=page_end,
        max_pages=max_pages,
        scale=render_scale,
    )

    images_dir = project_images_dir(project_id)
    images_dir.mkdir(parents=True, exist_ok=True)

    rendered_images = []
    vision_contents = []

    for p in pages:
        filename = f"pdfpage_p{p.page_number}_vision.png"
        (images_dir / filename).write_bytes(p.png_bytes)
        url = f"/static/projects/{project_id}/images/{filename}"
        rendered_images.append(
            {
                "page": p.page_number,
                "filename": filename,
                "url": url,
                "width": p.width,
                "height": p.height,
                "mime": "image/png",
            }
        )

        b64 = base64.b64encode(p.png_bytes).decode("ascii")
        vision_contents.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64}",
                },
            }
        )

    system_prompt = (
        "你是一个严谨的实验报告助手。\n"
        "我会给你 PDF 页面截图（可能包含公式和表格）。\n"
        "请你：\n"
        "1) 尽可能识别页面中的数学公式并转换为 LaTeX（行内用 $...$，独立公式用 $$...$$）。\n"
        "2) 如果页面中有表格，请图像理解后输出为 blocks JSON 里的 tablePayload（cells content 用纯文本）。\n"
        "3) 如果无法识别某些公式，用 [UNREADABLE] 标注，并描述它在页面中的位置。\n"
        "4) 最终输出必须是一个 JSON，对象包含字段: latex (string), blocks (array)。\n"
        "blocks 的格式与 LabFlow 方案 B 一致：每个 block 至少包含 type 和 content。\n"
    )

    user_prompt = {
        "role": "user",
        "content": [
            {"type": "text", "text": "请解析这些 PDF 截图中的公式/表格，输出 JSON: {latex, blocks}."},
            *vision_contents,
        ],
    }

    try:
        res = glm_chat_completions(
            model=model,
            messages=[{"role": "system", "content": system_prompt}, user_prompt],
            stream=False,
            thinking_enabled=True,
            clear_thinking=True,
            response_format={"type": "text"},
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
        raise HTTPException(status_code=502, detail=f"GLM returned non-JSON: {res.text[:500]}")

    # Zhipu style: choices[0].message.content
    content = None
    try:
        content = data.get("choices", [])[0].get("message", {}).get("content")
    except Exception:
        content = None

    if not content:
        raise HTTPException(status_code=502, detail=f"GLM returned empty content: {data}")

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
        "rendered_images": rendered_images,
        "glm_raw": data,
        "content": content,
        "extracted_at": datetime.utcnow().isoformat() + "Z",
    }
