from __future__ import annotations

import base64
import io
import os
import random
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...glm_client import GlmApiError, glm_chat_completions
from ...models import Project, User
from ...security import get_current_user
from .typst_shared import project_images_dir

router = APIRouter(tags=["images"])  # same prefix (/api) is applied in main


class ImageSummaryItem(BaseModel):
    filename: str
    url: str | None = None
    page: int | None = None


class ImagesSummarizeRequest(BaseModel):
    images: list[ImageSummaryItem] = Field(default_factory=list)
    model: str = "glm-4.6v-flash"
    max_images: int = Field(default=12, ge=1, le=60)


def _summarize_one_image(*, image_bytes: bytes, filename: str, model: str) -> str:
    # Keep prompt short and stable; deepseek will decide where to place images.
    system_prompt = (
        "你是一个严谨的图片理解助手。\n"
        "请用中文对图片做简短概括（1~3 句）。\n"
        "重点：图片类型（示意图/电路图/仪器照片/曲线图/表格截图/论文截图等）、关键元素、可能对应实验哪一步。\n"
        "不要输出 Markdown，不要输出代码块，不要输出多余前后缀。"
    )

    b64 = base64.b64encode(image_bytes).decode("ascii")
    user_msg: dict[str, Any] = {
        "role": "user",
        "content": [
            {"type": "text", "text": f"请概括这张图片：{filename}"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }

    try:
        res = glm_chat_completions(
            model=model,
            messages=[{"role": "system", "content": system_prompt}, user_msg],
            stream=False,
            thinking_enabled=False,
            clear_thinking=True,
            timeout_s=120.0,
        )
    except GlmApiError as e:
        raise RuntimeError(str(e))

    if not res.ok:
        raise RuntimeError(f"GLM upstream error: {res.status_code}: {res.text}")

    try:
        data = res.json()
        content = data.get("choices", [])[0].get("message", {}).get("content")
    except Exception:
        content = None

    return str(content or "").strip()


def _should_retry_rate_limit(err_text: str) -> bool:
    t = err_text or ""
    return ("429" in t) or ("1305" in t) or ("请求过多" in t) or ("rate" in t.lower())


def _summarize_one_image_with_retry(*, image_bytes: bytes, filename: str, model: str) -> str:
    """Retry on 429/1305 as long as needed (with backoff).

    We intentionally do not emit placeholders like "(概括失败: ...)".
    For non-rate-limit errors (e.g. 401/invalid key), we fail fast.
    """

    base_s = float(os.getenv("GLM_IMAGES_SUMMARY_RETRY_BACKOFF_BASE_S") or "1.5")
    cap_s = float(os.getenv("GLM_IMAGES_SUMMARY_RETRY_BACKOFF_CAP_S") or "30")
    max_attempts_env = os.getenv("GLM_IMAGES_SUMMARY_RETRY_MAX_ATTEMPTS")
    max_attempts = int(max_attempts_env) if (max_attempts_env and max_attempts_env.isdigit()) else 0
    attempt = 0

    while True:
        try:
            return _summarize_one_image(image_bytes=image_bytes, filename=filename, model=model)
        except Exception as e:
            msg = str(e)
            if not _should_retry_rate_limit(msg):
                raise

            attempt += 1
            if max_attempts > 0 and attempt >= max_attempts:
                # Still no placeholder output; surface as an error so caller can retry.
                raise RuntimeError(f"GLM rate-limited after {attempt} attempts: {msg}")

            # Exponential backoff with jitter.
            wait = min(cap_s, base_s * (2 ** min(attempt, 10)))
            wait = wait + random.uniform(0.0, min(0.5, base_s))
            time.sleep(wait)


@router.post("/projects/{project_id}/images/summarize")
async def summarize_project_images(
    project_id: str,
    payload: ImagesSummarizeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project is None or project.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    images_dir = project_images_dir(project_id)

    # Decide which images to summarize.
    items = payload.images or []
    if not items:
        if not images_dir.exists():
            return {"project_id": project_id, "model": payload.model, "summaries": []}
        for p in sorted(images_dir.glob("*")):
            if p.is_file():
                items.append(ImageSummaryItem(filename=p.name))

    items = items[: payload.max_images]

    out = []
    # Space out requests to be rate-limit friendly.
    interval_s = float(os.getenv("GLM_IMAGES_SUMMARY_INTERVAL_S") or "0.6")
    for it in items:
        file_path = images_dir / it.filename
        if not file_path.exists():
            continue

        raw = file_path.read_bytes()
        # Some stored images might be jpeg; glm accepts data URL anyway.
        summary = _summarize_one_image_with_retry(image_bytes=raw, filename=it.filename, model=payload.model)

        url = it.url or f"/static/projects/{project_id}/images/{it.filename}"
        out.append(
            {
                "filename": it.filename,
                "url": url,
                "page": it.page,
                "summary": summary,
            }
        )

        if interval_s > 0:
            time.sleep(interval_s)

    return {"project_id": project_id, "model": payload.model, "summaries": out}
