"""Event ingest service — business logic for event creation. Calls CRUD for DB access."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import Field, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import DEVICE_CONTEXT_KEYS
from app.core.tracing import create_service_span
from app.crud.device_crud import device_crud
from app.crud.event_crud import EventWriteCRUD, event_crud
from app.models.event_model import Event
from app.schemas.event_schema import (
    MAX_BATCH_EVENTS,
    BatchEventRequest,
    EventCreate,
    IngestResult,
)

logger = logging.getLogger(__name__)

event_write_crud = EventWriteCRUD()

# GET /stats counts the last year of events.
STATS_WINDOW_HOURS = 8760

# The bare-list form carries the same cap as {"events": [...]} (BatchEventRequest).
_EVENT_LIST: TypeAdapter[list[EventCreate]] = TypeAdapter(
    Annotated[list[EventCreate], Field(min_length=1, max_length=MAX_BATCH_EVENTS)]
)


class InvalidEventPayload(ValueError):
    """The decoded body is neither one event, {"events": [...]}, nor a list of events."""


def events_from_payload(payload: object) -> list[EventCreate]:
    """The three accepted ingest shapes, validated. Raises pydantic.ValidationError on a bad event."""
    if isinstance(payload, dict):
        if "events" in payload:
            return BatchEventRequest.model_validate(payload).events
        return [EventCreate.model_validate(payload)]
    if isinstance(payload, list):
        return _EVENT_LIST.validate_python(payload)
    raise InvalidEventPayload(
        'payload must be an event object, {"events": [...]}, or a list of events'
    )


def _latest_device_row(
    rows: list[tuple[datetime, dict[str, Any]]],
) -> dict[str, Any]:
    """One device's upsert from a batch: its events in time order, each later event's keys over
    the earlier ones. An offline queue flushes oldest first, so the first event is the stalest; a
    key no event sent stays None (the upsert then keeps the stored value)."""
    ordered = sorted(rows, key=lambda row: row[0])
    merged = dict(ordered[0][1])
    for _, row in ordered[1:]:
        merged.update({key: value for key, value in row.items() if value is not None})
    return merged


class EventCoreService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def ingest(self, app_id: str, payload: object) -> IngestResult:
        """Validate a decoded ingest payload and store its events (see events_from_payload)."""
        with create_service_span("EventCoreService", "ingest", app_id=app_id):
            events = events_from_payload(payload)
            logger.debug(
                "Analytics payload received",
                extra={
                    "app_id": app_id,
                    "batch_size": len(events),
                    "event_names": [e.name for e in events],
                },
            )
            created = await self.create_events(app_id, events)
            logger.info(
                "Analytics events processed",
                extra={
                    "app_id": app_id,
                    "events_count": len(created),
                    "duplicates": len(events) - len(created),
                    "event_types": sorted({e.name for e in created}),
                    "batch_type": "batch" if len(events) > 1 else "single",
                },
            )
            return IngestResult(received=len(events), stored=len(created))

    async def count_events(self, app_id: str, hours: int = STATS_WINDOW_HOURS) -> int:
        """Events the app sent in the last `hours`."""
        with create_service_span("EventCoreService", "count_events", app_id=app_id):
            conditions = event_crud.time_conditions(hours=hours, app_id=app_id)
            return await event_crud.count(self.db, conditions)

    async def create_events(
        self, app_id: str, events: list[EventCreate]
    ) -> list[Event]:
        """Store events with promoted columns, properties JSONB, and device upserts. Returns the
        events newly stored: one whose client id this app already sent, or that repeats earlier
        in the same batch, is acknowledged and skipped (LUXANALYTI-68)."""
        with create_service_span("EventCoreService", "create_events", app_id=app_id):
            received_at = datetime.now(UTC)
            insert_data = []
            # Each device's rows from this batch, with their event times (merged below).
            device_rows: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}

            seen_client_ids: set[str] = set()
            for event_data in events:
                client_id = event_data.client_event_id
                if client_id is not None:
                    # One INSERT cannot touch the same conflict key twice: drop repeats first.
                    if client_id in seen_client_ids:
                        continue
                    seen_client_ids.add(client_id)
                event_id = str(uuid.uuid4())
                metadata = event_data.metadata or {}

                device_id = metadata.get("device_id")
                device_model = metadata.get("device_model")
                os_version = metadata.get("system_version")
                app_version = metadata.get("app_version")
                platform = metadata.get("platform", "ios")

                properties = {
                    k: v for k, v in metadata.items() if k not in DEVICE_CONTEXT_KEYS
                }

                insert_data.append(
                    {
                        "id": event_id,
                        "client_event_id": client_id,
                        "app_id": app_id,
                        "name": event_data.name,
                        "timestamp": event_data.timestamp,
                        "user_id": event_data.user_id,
                        "session_id": event_data.session_id,
                        "device_id": device_id,
                        "device_model": device_model,
                        "os_version": os_version,
                        "app_version": app_version,
                        "platform": platform,
                        "device_type": metadata.get("device_type"),
                        "build_number": metadata.get("build_number"),
                        "screen_resolution": metadata.get("screen_resolution"),
                        "locale": metadata.get("locale"),
                        "timezone": metadata.get("timezone"),
                        "is_testflight": metadata["is_testflight"] == "true"
                        if "is_testflight" in metadata
                        else None,
                        "properties": properties or None,
                        "received_at": received_at,
                    }
                )

                if device_id:
                    device_rows.setdefault(device_id, []).append(
                        (
                            event_data.timestamp,
                            {
                                "device_id": device_id,
                                "app_id": app_id,
                                "device_model": device_model,
                                "device_type": metadata.get("device_type"),
                                "os_version": os_version,
                                "app_version": app_version,
                                "build_number": metadata.get("build_number"),
                                "screen_resolution": metadata.get("screen_resolution"),
                                "locale": metadata.get("locale"),
                                "timezone": metadata.get("timezone"),
                                "is_testflight": metadata["is_testflight"] == "true"
                                if "is_testflight" in metadata
                                else None,
                                "platform": platform,
                                "first_seen": received_at,
                                "last_seen": received_at,
                            },
                        )
                    )

            # One key order for every request: two concurrent requests carrying the same client
            # ids (or devices) in different orders would otherwise deadlock on the unique index.
            insert_data.sort(key=lambda row: str(row["client_event_id"] or ""))
            inserted = await event_write_crud.bulk_insert(self.db, insert_data)

            for device_id in sorted(device_rows):
                await device_crud.upsert(
                    self.db, _latest_device_row(device_rows[device_id])
                )

            return [Event(**data) for data in insert_data if data["id"] in inserted]
