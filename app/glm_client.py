from __future__ import annotations

import os
from typing import Any

import requests


class GlmApiError(RuntimeError):
    pass


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

    try:
        res = requests.post(url, json=payload, headers=headers, stream=stream, timeout=timeout_s)
    except requests.RequestException as e:
        raise GlmApiError(f"GLM request failed: {e}")

    return res
