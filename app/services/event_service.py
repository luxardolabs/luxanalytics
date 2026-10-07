"""Event ingest service — business logic for event creation. Calls CRUD for DB access."""

import uuid
from datetime import UTC, datetime
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import DEVICE_CONTEXT_KEYS
from app.crud.device_crud import device_crud
from app.crud.event_crud import EventWriteCRUD
from app.models.event_model import Event
from app.schemas.event_schema import EventCreate

event_write_crud = EventWriteCRUD()


class EventService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_events(
        self, app_id: str, events: List[EventCreate]
    ) -> List[Event]:
        """Create events with promoted columns, properties JSONB, and device upserts."""
        received_at = datetime.now(UTC)
        insert_data = []
        devices_to_upsert: dict[str, dict] = {}

        for event_data in events:
            event_id = str(uuid.uuid4())
            metadata = event_data.metadata or {}

            device_id = metadata.get("device_id")
            device_model = metadata.get("device_model")
            os_version = metadata.get("system_version")
            app_version = metadata.get("app_version")
            platform = metadata.get("platform", "ios")

            properties = {
                k: v for k, v in metadata.items()
                if k not in DEVICE_CONTEXT_KEYS
            }

            insert_data.append({
                "id": event_id,
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
                "properties": properties or None,
                "event_metadata": metadata,
                "received_at": received_at,
            })

            if device_id and device_id not in devices_to_upsert:
                devices_to_upsert[device_id] = {
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
                    "is_testflight": metadata.get("is_testflight") == "true",
                    "platform": platform,
                    "first_seen": received_at,
                    "last_seen": received_at,
                }

        await event_write_crud.bulk_insert(self.db, insert_data)

        for device_data in devices_to_upsert.values():
            await device_crud.upsert(self.db, device_data)

        return [Event(**data) for data in insert_data]
