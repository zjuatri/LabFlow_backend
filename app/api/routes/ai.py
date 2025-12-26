from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ...models import User
from ...schemas import DeepSeekChatRequest, DeepSeekChatResponse
from ...security import get_current_user

router = APIRouter()


@router.post("/ai/chat", response_model=DeepSeekChatResponse)
async def chat_with_deepseek(
    payload: DeepSeekChatRequest,
    current_user: User = Depends(get_current_user),
):
    """Chat with DeepSeek AI model (ZJU endpoint).

    Supports deepseek-r1-671b and deepseek-v3.
    Thinking mode is controlled by `payload.thinking`.
    """

    from openai import OpenAI

    api_key = os.environ.get("ZJU_DEEPSEEK_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ZJU_DEEPSEEK_API_KEY not configured (ZJU_DEEPSEEK_API_KEY 未配置)",
        )

    allowed_models = ["deepseek-r1-671b", "deepseek-v3"]
    if payload.model not in allowed_models:
        raise HTTPException(status_code=400, detail=f"Invalid model. Allowed models: {', '.join(allowed_models)}")

    client = OpenAI(
        api_key=api_key,
        base_url="https://chat.zju.edu.cn/api/ai/v1",
        timeout=120.0,
        max_retries=2,
    )

    extra_body = {"thinking": {"type": "enabled" if payload.thinking else "disabled"}}

    def sse(data_obj: dict) -> bytes:
        return ("data: " + json.dumps(data_obj, ensure_ascii=False) + "\n\n").encode("utf-8")

    if payload.stream:
        stream = client.chat.completions.create(
            model=payload.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that helps users generate scientific experiment reports. Please respond in Chinese.",
                },
                {"role": "user", "content": payload.message},
            ],
            stream=True,
            extra_body=extra_body,
        )

        def gen():
            in_think = False
            buffer = ""
            thought_acc = ""
            last_usage = None

            yield sse({"type": "meta", "model": payload.model})

            for event in stream:
                try:
                    if getattr(event, "usage", None) is not None:
                        usage = event.usage
                        last_usage = {
                            "prompt_tokens": getattr(usage, "prompt_tokens", None),
                            "completion_tokens": getattr(usage, "completion_tokens", None),
                            "total_tokens": getattr(usage, "total_tokens", None),
                        }
                except Exception:
                    pass

                try:
                    delta = event.choices[0].delta

                    reasoning_piece = getattr(delta, "reasoning_content", None)
                    if reasoning_piece:
                        thought_acc += reasoning_piece
                        yield sse({"type": "thought", "delta": reasoning_piece})

                    piece = getattr(delta, "content", None)
                    if not piece:
                        continue
                except Exception:
                    continue

                buffer += piece

                while buffer:
                    if not in_think:
                        idx = buffer.find("<think>")
                        if idx == -1:
                            yield sse({"type": "content", "delta": buffer})
                            buffer = ""
                        else:
                            if idx > 0:
                                pre = buffer[:idx]
                                yield sse({"type": "content", "delta": pre})
                            buffer = buffer[idx + len("<think>") :]
                            in_think = True
                    else:
                        idx = buffer.find("</think>")
                        if idx == -1:
                            yield sse({"type": "thought", "delta": buffer})
                            buffer = ""
                        else:
                            if idx > 0:
                                mid = buffer[:idx]
                                yield sse({"type": "thought", "delta": mid})
                            buffer = buffer[idx + len("</think>") :]
                            in_think = False

            if last_usage is not None:
                yield sse({"type": "usage", "usage": last_usage})
            yield sse({"type": "done"})
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    response = client.chat.completions.create(
        model=payload.model,
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant that helps users generate scientific experiment reports. Please respond in Chinese.",
            },
            {"role": "user", "content": payload.message},
        ],
        stream=False,
        extra_body=extra_body,
        timeout=120.0,
    )

    if not response.choices or not response.choices[0].message.content:
        raise HTTPException(status_code=500, detail="No response from AI model (AI 模型未返回内容)")

    msg = response.choices[0].message
    content = msg.content

    thought: str | None = None
    cleaned = content

    try:
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            thought = str(reasoning).strip()
    except Exception:
        pass

    try:
        m = re.search(r"<think>([\s\S]*?)</think>", content)
        if m:
            if thought is None:
                thought = m.group(1).strip()
            cleaned = re.sub(r"<think>[\s\S]*?</think>\s*", "", content).strip()
    except Exception:
        cleaned = content

    usage_dict = None
    try:
        if getattr(response, "usage", None) is not None:
            usage = response.usage
            usage_dict = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
    except Exception:
        usage_dict = None

    return DeepSeekChatResponse(response=cleaned, model=payload.model, thought=thought, usage=usage_dict)
