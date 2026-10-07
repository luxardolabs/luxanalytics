# mypy: disable-error-code="call-overload"
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings


class EventBase(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Event name",
        examples=["screen_view"],
    )
    timestamp: str = Field(
        ..., description="ISO8601 timestamp string", examples=["2025-06-02T15:00:00Z"]
    )
    user_id: str | None = Field(
        None, max_length=255, description="User identifier", examples=["user123"]
    )
    session_id: str | None = Field(
        None, max_length=255, description="Session identifier", examples=["session456"]
    )
    metadata: dict[str, str] = Field(
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
                dt = dt.replace(tzinfo=UTC)

            # Check not too far in future (configurable tolerance for clock skew)
            now = datetime.now(UTC)
            tolerance = settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE
            if dt > now + timedelta(seconds=tolerance):
                raise ValueError(
                    f"Event timestamp cannot be more than {tolerance} seconds in the future"
                )

            return dt  # Return datetime object for database
        except ValueError as e:
            raise ValueError(f"Invalid timestamp format: {e}")


class EventCreate(EventBase):
    model_config = ConfigDict(
        json_schema_extra={
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
        },
    )


class EventInDB(BaseModel):
    id: UUID
    app_id: str
    name: str
    timestamp: datetime
    user_id: str | None
    session_id: str | None
    event_metadata: dict[str, str]
    received_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EventResponse(BaseModel):
    status: str = Field(examples=["success"])
    events_received: int = Field(examples=[1])
    message: str | None = Field(examples=["Successfully processed 1 analytics events"])


class BatchEventRequest(BaseModel):
    events: list[EventCreate] = Field(..., min_length=1, max_length=1000)

    model_config = ConfigDict(
        json_schema_extra={
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
        },
    )

    @field_validator("events")
    def validate_events(cls, v):
        if not v:
            raise ValueError("At least one event is required")
        return v
