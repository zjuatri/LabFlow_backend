from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...prompt_store import load_prompt, load_prompts, save_prompt, save_prompts
from ...security import require_admin

router = APIRouter()


class PromptResponse(BaseModel):
    ai_prompt: str
    updated_at: str | None = None


class PromptUpdateRequest(BaseModel):
    ai_prompt: str = Field(min_length=1)


class PromptsResponse(BaseModel):
    ai_prompt: str
    pdf_page_ocr_prompt: str
    table_cell_ocr_prompt: str
    updated_at: str | None = None


class PromptsUpdateRequest(BaseModel):
    ai_prompt: str | None = None
    pdf_page_ocr_prompt: str | None = None
    table_cell_ocr_prompt: str | None = None


@router.get("/manage/prompt", response_model=PromptResponse)
def get_prompt(_admin=Depends(require_admin)):
    data = load_prompt()
    return PromptResponse(ai_prompt=data["ai_prompt"], updated_at=data.get("updated_at"))


@router.put("/manage/prompt", response_model=PromptResponse)
def update_prompt(payload: PromptUpdateRequest, _admin=Depends(require_admin)):
    data = save_prompt(payload.ai_prompt)
    return PromptResponse(ai_prompt=data["ai_prompt"], updated_at=data.get("updated_at"))


@router.get("/manage/prompts", response_model=PromptsResponse)
def get_prompts(_admin=Depends(require_admin)):
    data = load_prompts()
    return PromptsResponse(
        ai_prompt=data["ai_prompt"],
        pdf_page_ocr_prompt=data["pdf_page_ocr_prompt"],
        table_cell_ocr_prompt=data["table_cell_ocr_prompt"],
        updated_at=data.get("updated_at"),
    )


@router.put("/manage/prompts", response_model=PromptsResponse)
def update_prompts(payload: PromptsUpdateRequest, _admin=Depends(require_admin)):
    data = save_prompts(
        ai_prompt=payload.ai_prompt,
        pdf_page_ocr_prompt=payload.pdf_page_ocr_prompt,
        table_cell_ocr_prompt=payload.table_cell_ocr_prompt,
    )
    return PromptsResponse(
        ai_prompt=data["ai_prompt"],
        pdf_page_ocr_prompt=data["pdf_page_ocr_prompt"],
        table_cell_ocr_prompt=data["table_cell_ocr_prompt"],
        updated_at=data.get("updated_at"),
    )
