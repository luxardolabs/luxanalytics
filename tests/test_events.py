"""Tests for event ingestion with promoted columns and device upsert."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_model import Device
from app.schemas.event_schema import EventCreate
from app.services.core.event_core_service import EventCoreService


@pytest.mark.asyncio
async def test_create_single_event_promotes_columns(db: AsyncSession) -> None:
    """Event creation should populate promoted columns from metadata."""
    svc = EventCoreService(db)

    event_data = EventCreate(
        name="screen_view",
        timestamp=datetime.now(UTC).isoformat(),
        user_id="user_1",
        session_id="session_1",
        metadata={
            "device_id": "dev_abc123",
            "device_model": "iPhone17,1",
            "device_type": "iPhone",
            "system_version": "18.3",
            "app_version": "1.0.23",
            "build_number": "3",
            "screen_resolution": "402x874",
            "locale": "en_US",
            "timezone": "America/Chicago",
            "is_testflight": "true",
            "screen": "home",
            "feature": "main",
        },
    )

    created = await svc.create_events("test_app", [event_data])
    assert len(created) == 1

    event = created[0]
    assert event.device_id == "dev_abc123"
    assert event.device_model == "iPhone17,1"
    assert event.os_version == "18.3"
    assert event.app_version == "1.0.23"
    assert event.platform == "ios"
    # The rest of the device context, as it was for THIS event (LUXANALYTI-16).
    assert event.device_type == "iPhone"
    assert event.build_number == "3"
    assert event.screen_resolution == "402x874"
    assert event.locale == "en_US"
    assert event.timezone == "America/Chicago"
    assert event.is_testflight is True

    # Properties should NOT contain device context keys
    assert event.properties is not None
    assert event.event_metadata is not None
    assert "device_id" not in event.properties
    assert "device_model" not in event.properties
    assert "system_version" not in event.properties

    # Properties should contain event-specific keys
    assert event.properties["screen"] == "home"
    assert event.properties["feature"] == "main"

    # Legacy column should have the full metadata
    assert event.event_metadata["device_id"] == "dev_abc123"
    assert event.event_metadata["screen"] == "home"


@pytest.mark.asyncio
async def test_create_batch_events(db: AsyncSession) -> None:
    """Batch event creation should handle multiple events."""
    svc = EventCoreService(db)
    now = datetime.now(UTC).isoformat()

    events = [
        EventCreate(name="screen_view", timestamp=now, metadata={"screen": "home"}),
        EventCreate(
            name="button_tapped", timestamp=now, metadata={"button_name": "save"}
        ),
        EventCreate(
            name="app_launched", timestamp=now, metadata={"launch_type": "cold"}
        ),
    ]

    created = await svc.create_events("test_app", events)
    assert len(created) == 3
    assert {e.name for e in created} == {"screen_view", "button_tapped", "app_launched"}


@pytest.mark.asyncio
async def test_device_upsert_on_ingest(db: AsyncSession) -> None:
    """Ingesting events with device_id should create/update device records."""
    from sqlalchemy import select

    svc = EventCoreService(db)
    now = datetime.now(UTC).isoformat()

    event1 = EventCreate(
        name="screen_view",
        timestamp=now,
        metadata={
            "device_id": "upsert_test_device",
            "device_model": "iPhone16,1",
            "system_version": "17.0",
            "app_version": "1.0.0",
            "is_testflight": "true",
            "locale": "en_US",
            "timezone": "America/Chicago",
        },
    )
    await svc.create_events("test_app", [event1])
    await db.flush()

    result = await db.execute(
        select(Device).where(Device.device_id == "upsert_test_device")
    )
    device = result.scalar_one()
    assert device.device_model == "iPhone16,1"
    assert device.os_version == "17.0"
    assert device.is_testflight is True

    # Second event — newer version
    event2 = EventCreate(
        name="screen_view",
        timestamp=now,
        metadata={
            "device_id": "upsert_test_device",
            "device_model": "iPhone16,1",
            "system_version": "17.1",
            "app_version": "1.0.1",
            "is_testflight": "false",
            "locale": "en_US",
            "timezone": "America/Chicago",
        },
    )
    await svc.create_events("test_app", [event2])
    await db.flush()

    await db.refresh(device)
    assert device.os_version == "17.1"
    assert device.app_version == "1.0.1"
    assert device.is_testflight is False


@pytest.mark.asyncio
async def test_platform_defaults_to_ios(db: AsyncSession) -> None:
    svc = EventCoreService(db)
    now = datetime.now(UTC).isoformat()
    event = EventCreate(name="screen_view", timestamp=now, metadata={"screen": "home"})
    created = await svc.create_events("test_app", [event])
    assert created[0].platform == "ios"


@pytest.mark.asyncio
async def test_platform_from_metadata(db: AsyncSession) -> None:
    svc = EventCoreService(db)
    now = datetime.now(UTC).isoformat()
    event = EventCreate(
        name="screen_view", timestamp=now, metadata={"platform": "android"}
    )
    created = await svc.create_events("test_app", [event])
    assert created[0].platform == "android"


@pytest.mark.asyncio
async def test_event_without_device_id(db: AsyncSession) -> None:
    """Events without device_id should work — no device upsert."""
    from sqlalchemy import func, select

    svc = EventCoreService(db)
    now = datetime.now(UTC).isoformat()

    before = (await db.execute(select(func.count(Device.device_id)))).scalar()

    event = EventCreate(name="screen_view", timestamp=now, metadata={"screen": "home"})
    await svc.create_events("test_app", [event])
    await db.flush()

    after = (await db.execute(select(func.count(Device.device_id)))).scalar()
    assert after == before
