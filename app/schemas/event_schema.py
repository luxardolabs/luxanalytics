from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings


class EventBase(BaseModel):
    # The SDK sets one id per event at track time and resends it unchanged on every retry: the
    # idempotency key. A duplicate is acknowledged and dropped (LUXANALYTI-68). Optional, so a
    # client that does not send one is still accepted. The wire key is `id`; the name says whose id
    # it is (not one of our UUID keys).
    client_event_id: str | None = Field(
        None,
        alias="id",
        min_length=1,
        max_length=64,
        description="Client-generated event id; resending it is acknowledged, not stored twice",
        examples=["5f0c3c1e-2c1b-4d8a-9a57-1d3c7f3e9b10"],
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Event name",
        examples=["screen_view"],
    )
    # Sent as an ISO8601 string, held as an aware datetime (parse_timestamp).
    timestamp: datetime = Field(
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
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Event name cannot be empty")
        return v.strip()

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v: object) -> datetime:
        """ISO8601 strings only (the SDK's wire format); naive means UTC; bounded clock skew."""
        if not isinstance(v, str):
            raise ValueError("timestamp must be an ISO8601 string")
        try:
            dt = datetime.fromisoformat(
                v.removesuffix("Z") + "+00:00" if v.endswith("Z") else v
            )
        except ValueError as e:
            raise ValueError(f"Invalid timestamp format: {e}") from e

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        # Not too far in the future (configurable tolerance for clock skew)
        tolerance = settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE
        if dt > datetime.now(UTC) + timedelta(seconds=tolerance):
            raise ValueError(
                f"Event timestamp cannot be more than {tolerance} seconds in the future"
            )
        return dt


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
    """An event's stored columns, read from the ORM row: what core hands the view."""

    id: UUID
    app_id: str
    name: str
    timestamp: datetime
    received_at: datetime
    user_id: str | None
    session_id: str | None
    device_id: str | None
    device_model: str | None
    os_version: str | None
    app_version: str | None
    platform: str | None
    # JSONB the SDK fills per event name: keys and value types are the caller's.
    properties: dict[str, Any] | None
    event_metadata: dict[str, Any] | None

    model_config = ConfigDict(from_attributes=True)


class IngestResult(BaseModel):
    """What one ingest request did: events accepted, and how many of them were newly stored
    (the rest were duplicates of an id this app had already sent)."""

    received: int
    stored: int


class EventResponse(BaseModel):
    status: Literal["success"] = Field(examples=["success"])
    events_received: int = Field(examples=[1])
    # Events whose id this app had already sent (or that repeated within the request): accepted,
    # not stored again.
    duplicates: int = Field(0, examples=[0])
    message: str | None = Field(examples=["Successfully processed 1 analytics events"])


class AppStatsResponse(BaseModel):
    """GET /api/v1/events/stats: the calling app's event total over the stats window."""

    app_id: str
    total_events: int
    status: Literal["active"]


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
    @classmethod
    def validate_events(cls, v: list[EventCreate]) -> list[EventCreate]:
        if not v:
            raise ValueError("At least one event is required")
        return v
