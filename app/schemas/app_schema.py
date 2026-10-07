from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AppBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    organization: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    app_metadata: Dict[str, Any] = Field(default_factory=dict)


class AppCreate(AppBase):
    app_id: str = Field(..., min_length=1, max_length=255)


class AppUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    organization: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    is_active: Optional[bool] = None
    app_metadata: Optional[Dict[str, Any]] = None


class AppResponse(AppBase):
    id: UUID
    public_id: str
    project_id: str
    app_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    dsn: str

    class Config:
        from_attributes = True
