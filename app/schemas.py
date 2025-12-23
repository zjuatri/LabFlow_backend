from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProjectCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ProjectUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    typst_code: str | None = None


class ProjectResponse(BaseModel):
    id: str
    title: str
    typst_code: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TypstRenderRequest(BaseModel):
    code: str


class ChartRenderRequest(BaseModel):
    chart_type: str
    title: str = ''
    x_label: str = ''
    y_label: str = ''
    legend: bool = True
    data: list[dict] = []


class ImageCropRequest(BaseModel):
    image_url: str
    crop_x: float
    crop_y: float
    crop_width: float
    crop_height: float
    image_width: float
    image_height: float
