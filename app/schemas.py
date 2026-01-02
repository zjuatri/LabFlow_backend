from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, EmailStr, Field, field_serializer


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
    type: str = "report"
    source_project_id: str | None = None


class ProjectUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    type: str | None = None
    typst_code: str | None = None


class ProjectResponse(BaseModel):
    id: str
    title: str
    type: str
    typst_code: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, dt: datetime) -> str:
        """Ensure datetime is serialized with UTC timezone info.
        
        SQLite doesn't preserve timezone info, so naive datetimes loaded from DB
        are assumed to be UTC and serialized with 'Z' suffix.
        """
        if dt.tzinfo is None:
            # Naive datetime - assume UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")



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


class DeepSeekChatRequest(BaseModel):
    message: str
    model: str = "deepseek-v3"  # 默认使用 deepseek-v3
    stream: bool = False
    thinking: bool = False


class DeepSeekChatResponse(BaseModel):
    response: str
    model: str
    thought: str | None = None
    usage: dict | None = None
