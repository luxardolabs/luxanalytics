# mypy: disable-error-code="call-overload"
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings


class EventBase(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Event name",
        example="screen_view",
    )
    timestamp: str = Field(
        ..., description="ISO8601 timestamp string", example="2025-06-02T15:00:00Z"
    )
    user_id: Optional[str] = Field(
        None, max_length=255, description="User identifier", example="user123"
    )
    session_id: Optional[str] = Field(
        None, max_length=255, description="Session identifier", example="session456"
    )
    metadata: Dict[str, str] = Field(
        default_factory=dict, description="Event metadata as string-to-string map"
    )

    @field_validator("name")
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Event name cannot be empty")
        return v.strip()

    @field_validator("timestamp")
    def validate_timestamp(cls, v):
        try:
            # Parse ISO8601 string to datetime
            if v.endswith("Z"):
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(v)

            # Ensure timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            # Check not too far in future (configurable tolerance for clock skew)
            now = datetime.now(timezone.utc)
            tolerance = settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE
            if dt > now + timedelta(seconds=tolerance):
                raise ValueError(
                    f"Event timestamp cannot be more than {tolerance} seconds in the future"
                )

            return dt  # Return datetime object for database
        except ValueError as e:
            raise ValueError(f"Invalid timestamp format: {e}")


class EventCreate(EventBase):
    class Config:
        json_schema_extra = {
            "example": {
                "name": "screen_view",
                "timestamp": "2025-06-02T15:00:00Z",
                "user_id": "user123",
                "session_id": "session456",
                "metadata": {
                    "screen": "profile",
                    "device_model": "iPhone14,2",
                    "app_version": "1.0.0",
                },
            }
        }


class EventInDB(BaseModel):
    id: UUID
    app_id: str
    name: str
    timestamp: datetime
    user_id: Optional[str]
    session_id: Optional[str]
    event_metadata: Dict[str, str]
    received_at: datetime

    class Config:
        from_attributes = True


class EventResponse(BaseModel):
    status: str = Field(example="success")
    events_received: int = Field(example=1)
    message: Optional[str] = Field(example="Successfully processed 1 analytics events")


class BatchEventRequest(BaseModel):
    events: List[EventCreate] = Field(..., min_items=1, max_items=1000)

    class Config:
        json_schema_extra = {
            "example": {
                "events": [
                    {
                        "name": "screen_view",
                        "timestamp": "2025-06-02T15:00:00Z",
                        "user_id": "user123",
                        "session_id": "session456",
                        "metadata": {"screen": "home"},
                    },
                    {
                        "name": "button_click",
                        "timestamp": "2025-06-02T15:01:00Z",
                        "user_id": "user123",
                        "session_id": "session456",
                        "metadata": {"button": "save"},
                    },
                ]
            }
        }

    @field_validator("events")
    def validate_events(cls, v):
        if not v:
            raise ValueError("At least one event is required")
        return v
