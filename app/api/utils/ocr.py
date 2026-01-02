import base64
import os
import random
import time
from ...glm_client import GlmApiError, glm_chat_completions
from .text_extraction import extract_json_object

def _glm_vision_page_ocr(*, png_bytes: bytes, model: str, system_prompt: str, timeout_s: float = 180.0) -> str:
    """Use GLM vision to OCR a page and preserve inline math as LaTeX."""

    b64 = base64.b64encode(png_bytes).decode("ascii")
    system_prompt = (system_prompt or "").strip()
    if not system_prompt:
        raise RuntimeError("PDF page OCR prompt is empty")

    user_prompt = {
        "role": "user",
        "content": [
            {"type": "text", "text": "请 OCR 该页面并按要求输出 JSON {text}。"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }

    try:
        res = glm_chat_completions(
            model=model,
            messages=[{"role": "system", "content": system_prompt}, user_prompt],
            stream=False,
            thinking_enabled=False,
            clear_thinking=True,
            # If the upstream supports it, this nudges it to strict JSON.
            response_format={"type": "json_object"},
            timeout_s=timeout_s,
        )
    except GlmApiError as e:
        raise RuntimeError(str(e))

    if not res.ok:
        raise RuntimeError(f"GLM upstream error: {res.status_code}: {res.text}")

    data: dict = {}
    try:
        data = res.json()
    except Exception:
        data = {}

    content = None
    try:
        content = data.get("choices", [])[0].get("message", {}).get("content")
    except Exception:
        content = None

    try:
        obj = extract_json_object(content or "")
    except Exception as e:
        raw = (content or "").strip()
        head = raw[:400]
        tail = raw[-400:] if len(raw) > 800 else ""
        preview = head + ("\n...\n" + tail if tail else "")
        raise RuntimeError(f"unable to parse JSON object; raw preview:\n{preview}")

    lines = obj.get("lines")
    if isinstance(lines, list):
        return "\n".join(str(x) for x in lines if x is not None)

    # Back-compat if model still returns {text: "..."}
    return str(obj.get("text") or "")


def _should_retry_rate_limit(err_text: str) -> bool:
    t = err_text or ""
    return ("429" in t) or ("1305" in t) or ("请求过多" in t) or ("rate" in t.lower())


def glm_vision_page_ocr_with_retry(*, png_bytes: bytes, model: str, system_prompt: str, timeout_s: float = 180.0) -> str:
    """OCR with retry/backoff on GLM 429/1305 rate limits.

    Defaults to unlimited retries (so callers won't see transient 429 errors),
    but can be bounded via env GLM_OCR_RETRY_MAX_ATTEMPTS.
    """

    base_s = float(os.getenv("GLM_OCR_RETRY_BACKOFF_BASE_S") or "1.5")
    cap_s = float(os.getenv("GLM_OCR_RETRY_BACKOFF_CAP_S") or "30")
    max_attempts_env = os.getenv("GLM_OCR_RETRY_MAX_ATTEMPTS")
    max_attempts = int(max_attempts_env) if (max_attempts_env and max_attempts_env.isdigit()) else 0
    attempt = 0

    while True:
        try:
            return _glm_vision_page_ocr(
                png_bytes=png_bytes,
                model=model,
                system_prompt=system_prompt,
                timeout_s=timeout_s,
            )
        except Exception as e:
            msg = str(e)
            if not _should_retry_rate_limit(msg):
                raise

            attempt += 1
            if max_attempts > 0 and attempt >= max_attempts:
                raise RuntimeError(f"GLM rate-limited after {attempt} attempts: {msg}")

            wait = min(cap_s, base_s * (2 ** min(attempt, 10)))
            wait = wait + random.uniform(0.0, min(0.5, base_s))
            time.sleep(wait)
