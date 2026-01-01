from __future__ import annotations

import os
import random
import time
from typing import Any

import requests


class GlmApiError(RuntimeError):
    pass


def _parse_retry_after_seconds(res: requests.Response) -> float | None:
    ra = res.headers.get("Retry-After")
    if not ra:
        return None
    try:
        return float(ra)
    except Exception:
        return None


def _compute_backoff_s(*, attempt: int, base_s: float, cap_s: float) -> float:
    # attempt: 0,1,2...
    # Exponential backoff with small jitter.
    wait = base_s * (2**attempt)
    jitter = random.uniform(0.0, min(0.5, base_s))
    return min(cap_s, wait + jitter)


def _post_with_retry(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    stream: bool,
    timeout_s: float,
    max_attempts: int,
    backoff_base_s: float,
    backoff_cap_s: float,
) -> requests.Response:
    retry_statuses = {429, 500, 502, 503, 504}

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            res = requests.post(url, json=payload, headers=headers, stream=stream, timeout=timeout_s)
        except requests.RequestException as e:
            last_exc = e
            if attempt >= max_attempts - 1:
                break
            time.sleep(_compute_backoff_s(attempt=attempt, base_s=backoff_base_s, cap_s=backoff_cap_s))
            continue

        if res.status_code in retry_statuses and attempt < max_attempts - 1:
            retry_after = _parse_retry_after_seconds(res)
            try:
                res.close()
            except Exception:
                pass
            wait = (
                retry_after
                if retry_after is not None
                else _compute_backoff_s(attempt=attempt, base_s=backoff_base_s, cap_s=backoff_cap_s)
            )
            time.sleep(wait)
            continue

        return res

    if last_exc is not None:
        raise GlmApiError(f"GLM request failed: {last_exc}")
    raise GlmApiError("GLM request failed after retries")


def glm_chat_completions(
    *,
    model: str,
    messages: list[dict[str, Any]],
    stream: bool = False,
    temperature: float = 1.0,
    top_p: float = 0.95,
    do_sample: bool = True,
    thinking_enabled: bool = False,
    clear_thinking: bool = True,
    response_format: dict[str, Any] | None = None,
    timeout_s: float = 120.0,
) -> requests.Response:
    """Call ZhipuAI (bigmodel.cn) chat completions.

    Notes:
        - Uses env var `GLM_API_KEY`.
        - Returns raw `requests.Response` so caller may stream.
    """

    api_key = os.getenv("GLM_API_KEY")
    if not api_key:
        raise GlmApiError("GLM_API_KEY not configured")

    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
        "thinking": {
            "type": "enabled" if thinking_enabled else "disabled",
            "clear_thinking": bool(clear_thinking),
        },
        "do_sample": bool(do_sample),
        "top_p": top_p,
        "tool_stream": False,
        "response_format": response_format or {"type": "text"},
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Retry/backoff (primarily for 429 rate-limit). Defaults can be overridden by env.
    max_attempts = int(os.getenv("GLM_RETRY_MAX_ATTEMPTS") or "4")
    backoff_base_s = float(os.getenv("GLM_RETRY_BACKOFF_BASE_S") or "1.0")
    backoff_cap_s = float(os.getenv("GLM_RETRY_BACKOFF_CAP_S") or "20.0")

    return _post_with_retry(
        url,
        payload=payload,
        headers=headers,
        stream=stream,
        timeout_s=timeout_s,
        max_attempts=max_attempts,
        backoff_base_s=backoff_base_s,
        backoff_cap_s=backoff_cap_s,
    )
