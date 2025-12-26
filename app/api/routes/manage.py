from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...prompt_store import load_prompt, save_prompt
from ...security import require_admin

router = APIRouter()


class PromptResponse(BaseModel):
    ai_prompt: str
    updated_at: str | None = None


class PromptUpdateRequest(BaseModel):
    ai_prompt: str = Field(min_length=1)


@router.get("/manage/prompt", response_model=PromptResponse)
def get_prompt(_admin=Depends(require_admin)):
    data = load_prompt()
    return PromptResponse(ai_prompt=data["ai_prompt"], updated_at=data.get("updated_at"))


@router.put("/manage/prompt", response_model=PromptResponse)
def update_prompt(payload: PromptUpdateRequest, _admin=Depends(require_admin)):
    data = save_prompt(payload.ai_prompt)
    return PromptResponse(ai_prompt=data["ai_prompt"], updated_at=data.get("updated_at"))
