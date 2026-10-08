"""Defects the fourth adversarial pass (/adversarial on 1203d2c..46a8f84) reproduced, kept as
regressions. Each failed before its fix. The backup and settings ones live beside their peers in
test_backup_script.py and test_settings.py."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_model import Device
from app.schemas.event_schema import EventCreate
from app.services.core.event_core_service import EventCoreService
from tests.test_ingest_http import PROJECT, _dsn_auth

pytestmark = pytest.mark.db


async def test_a_body_that_is_not_utf8_is_a_client_error(
    client: AsyncClient, sample_app: object
) -> None:
    raw = b'{"name":"a\xff","timestamp":"2026-10-06T12:00:00Z","metadata":{}}'
    response = await client.post(
        f"/api/v1/events/{PROJECT}",
        content=raw,
        headers={**_dsn_auth(), "Content-Type": "application/json"},
    )
    assert response.status_code == 400


async def test_a_batch_leaves_the_device_at_its_latest_values(db: AsyncSession) -> None:
    """An offline queue flushes oldest first: the device must end at the newest event's values,
    not the first event's."""
    svc = EventCoreService(db)

    def event(name: str, ts: str, **md: str) -> EventCreate:
        return EventCreate(
            name=name, timestamp=ts, metadata={"device_id": "batch-dev", **md}
        )

    await svc.create_events(
        "test_app",
        [event("a", "2026-10-06T12:00:00Z", app_version="1.1", is_testflight="false")],
    )
    await svc.create_events(
        "test_app",
        [
            event(
                "b",
                "2026-10-06T12:01:00Z",
                app_version="1.0",
                is_testflight="true",
                locale="fr_FR",
            ),
            event(
                "c", "2026-10-06T12:02:00Z", app_version="1.2", is_testflight="false"
            ),
        ],
    )
    await db.flush()
    device = (
        await db.execute(select(Device).where(Device.device_id == "batch-dev"))
    ).scalar_one()
    await db.refresh(device)
    assert (device.app_version, device.is_testflight) == ("1.2", False)
    assert (
        device.locale == "fr_FR"
    )  # sent only by the earlier event: still the latest known
