from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from app.core.config import settings

CLIENT_EVENT_ID_MAX = 64  # the events.client_event_id column width
MAX_BATCH_EVENTS = 1000
# Before this, a timestamp is a device clock reset (often to 1970), not when the event happened.
EARLIEST_TIMESTAMP = datetime(2000, 1, 1, tzinfo=UTC)


def storable(text: str) -> bool:
    """Postgres text holds neither NUL nor a lone UTF-16 surrogate (JSON's "\\ud800" decodes to one,
    and it has no UTF-8 encoding)."""
    if "\x00" in text:
        return False
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class EventBase(BaseModel):
    # The SDK sets one id per event at track time and resends it unchanged on every retry: the
    # idempotency key. A duplicate is acknowledged and dropped (LUXANALYTI-68). Optional, so a
    # client that does not send one is still accepted. The wire key is `id`; the name says whose id
    # it is (not one of our UUID keys).
    client_event_id: str | None = Field(
        None,
        alias="id",
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

    @field_validator("client_event_id", mode="before")
    @classmethod
    def usable_client_id(cls, v: object) -> str | None:
        """A key we can dedupe on, or None (stored, not deduped). Never a reason to reject the
        batch: before LUXANALYTI-68 an `id` was ignored, so a client sending some other shape
        must keep its events."""
        if isinstance(v, str) and 0 < len(v) <= CLIENT_EVENT_ID_MAX and storable(v):
            return v
        return None

    @field_validator("name", "user_id", "session_id")
    @classmethod
    def no_nul(cls, v: str | None) -> str | None:
        """Text Postgres cannot store is a client error (422), not a database error (500)."""
        if v is not None and not storable(v):
            raise ValueError("must not contain NUL characters or lone surrogates")
        return v

    @field_validator("metadata")
    @classmethod
    def no_nul_in_metadata(cls, v: dict[str, str]) -> dict[str, str]:
        if not all(storable(k) and storable(val) for k, val in v.items()):
            raise ValueError(
                "metadata must not contain NUL characters or lone surrogates"
            )
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Event name cannot be empty")
        return v.strip()

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v: object) -> datetime:
        """ISO8601 strings only (the SDK's wire format); naive means UTC; a fast clock clamped."""
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

        # A timestamp outside [EARLIEST_TIMESTAMP, now + tolerance] is a broken device clock: store
        # the receive time rather than reject it. The SDK drops a batch answered 4xx, so a rejection
        # lost every event from that device for as long as its clock was off (LUXANALYTI-80).
        # Within the window it is stored as sent, so an event queued offline keeps its real time.
        now = datetime.now(UTC)
        try:
            dt = dt.astimezone(UTC)
        except OverflowError:  # year 1 or 9999 at an offset leaves datetime's range
            return now
        latest = now + timedelta(seconds=settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE)
        if not EARLIEST_TIMESTAMP <= dt <= latest:
            return now
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
    # UIDevice.current.model: "iPhone", "iPad", etc. (an open vendor set, as the column declares).
    device_type: str | None
    build_number: str | None
    screen_resolution: str | None
    locale: str | None
    timezone: str | None
    is_testflight: bool | None
    # JSONB the SDK fills per event name: keys and value types are the caller's.
    properties: dict[str, Any] | None

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
    events: list[EventCreate] = Field(..., min_length=1, max_length=MAX_BATCH_EVENTS)

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


# The ingest body in the OpenAPI document. The ingest routes read the raw body themselves (the
# middleware inflates it and HMAC is over the wire bytes), so FastAPI cannot infer it: without this
# the published spec showed no request body at all, and a client's contract check passed anything.
# Generated from the models ingest validates with (events_from_payload), so it cannot drift.
_INGEST_BODY_SCHEMA = TypeAdapter(
    EventCreate
    | BatchEventRequest
    | Annotated[list[EventCreate], Field(min_length=1, max_length=MAX_BATCH_EVENTS)]
).json_schema(by_alias=True, ref_template="#/components/schemas/{model}")
INGEST_BODY_COMPONENTS: dict[str, Any] = _INGEST_BODY_SCHEMA.pop("$defs", {})
INGEST_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": (
            'One event, {"events": [...]}, or a list of events (at most '
            f"{MAX_BATCH_EVENTS}). May be sent with Content-Encoding: deflate (zlib, RFC 1950)."
        ),
        "content": {"application/json": {"schema": _INGEST_BODY_SCHEMA}},
    }
}
