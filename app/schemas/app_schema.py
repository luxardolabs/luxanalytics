from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AppBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    organization: str | None = Field(None, max_length=255)
    description: str | None = None
    app_metadata: dict[str, Any] = Field(default_factory=dict)


class AppCreate(AppBase):
    app_id: str = Field(..., min_length=1, max_length=255)


class AppUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    organization: str | None = Field(None, max_length=255)
    description: str | None = None
    is_active: bool | None = None
    app_metadata: dict[str, Any] | None = None


class AppResponse(AppBase):
    id: UUID
    public_id: str
    project_id: str
    app_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    dsn: str

    model_config = ConfigDict(from_attributes=True)
