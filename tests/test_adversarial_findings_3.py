"""Defects the third adversarial pass (/adversarial on 97df396..HEAD) reproduced, kept as
regressions. Each failed before its fix."""

import json
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_model import Device
from app.models.event_model import Event
from app.schemas.event_schema import EventCreate
from app.services.core.event_core_service import EventCoreService
from tests.test_ingest_http import PROJECT, _dsn_auth

pytestmark = pytest.mark.db


async def _post(client: AsyncClient, raw: bytes) -> int:
    response = await client.post(
        f"/api/v1/events/{PROJECT}",
        content=raw,
        headers={**_dsn_auth(), "Content-Type": "application/json"},
    )
    return response.status_code


# ── A lone UTF-16 surrogate cannot be stored (like NUL): a 422, not a 500 ──


@pytest.mark.parametrize(
    "raw",
    [
        b'{"name":"a\\ud800b","timestamp":"2026-10-06T12:00:00Z","metadata":{}}',
        b'{"name":"ok","user_id":"\\udfff","timestamp":"2026-10-06T12:00:00Z","metadata":{}}',
        b'{"name":"ok","timestamp":"2026-10-06T12:00:00Z","metadata":{"screen":"\\ud800"}}',
        b'{"name":"ok","timestamp":"2026-10-06T12:00:00Z","metadata":{"\\ud800":"x"}}',
        b'{"name":"ok","timestamp":"2026-10-06T12:00:00Z","metadata":{"locale":"\\ud800"}}',
    ],
    ids=["name", "user_id", "property", "key", "locale_column"],
)
async def test_a_lone_surrogate_is_a_client_error(
    client: AsyncClient, sample_app: object, raw: bytes
) -> None:
    assert await _post(client, raw) == 422


async def test_a_surrogate_in_the_client_id_stores_the_event_undeduplicated(
    client: AsyncClient, db: AsyncSession, sample_app: object
) -> None:
    raw = b'{"id":"\\ud800","name":"sid","timestamp":"2026-10-06T12:00:00Z","metadata":{}}'
    assert await _post(client, raw) == 200
    stored = (await db.execute(select(Event).where(Event.name == "sid"))).scalar_one()
    assert stored.client_event_id is None


# ── A timestamp out of range is a broken clock: stored at receive time, not a 500 ──


@pytest.mark.parametrize(
    "ts", ["0001-01-01T00:00:00+05:00", "0001-01-01T00:00:00", "1970-01-01T00:00:00Z"]
)
async def test_a_broken_past_clock_is_clamped_to_now(
    client: AsyncClient, db: AsyncSession, sample_app: object, ts: str
) -> None:
    before = datetime.now(UTC)
    raw = json.dumps({"name": "old_clock", "timestamp": ts, "metadata": {}}).encode()
    assert await _post(client, raw) == 200
    stored = (
        await db.execute(select(Event).where(Event.name == "old_clock"))
    ).scalar_one()
    assert before <= stored.timestamp <= datetime.now(UTC)


async def test_the_far_future_at_a_negative_offset_is_clamped(
    client: AsyncClient, sample_app: object
) -> None:
    raw = json.dumps(
        {"name": "far", "timestamp": "9999-12-31T23:59:59-14:00", "metadata": {}}
    ).encode()
    assert await _post(client, raw) == 200


# ── A later event that omits a device key keeps what the device record knew ──


async def test_device_context_survives_an_event_that_omits_it(db: AsyncSession) -> None:
    svc = EventCoreService(db)
    now = datetime.now(UTC).isoformat()
    full = {
        "device_id": "keep-ctx",
        "system_version": "18.0",
        "is_testflight": "true",
        "locale": "en_US",
    }
    await svc.create_events(
        "test_app", [EventCreate(name="a", timestamp=now, metadata=full)]
    )
    sparse = {"device_id": "keep-ctx", "screen": "home"}
    await svc.create_events(
        "test_app", [EventCreate(name="b", timestamp=now, metadata=sparse)]
    )
    await db.flush()
    device = (
        await db.execute(select(Device).where(Device.device_id == "keep-ctx"))
    ).scalar_one()
    await db.refresh(device)
    assert device.is_testflight is True
    assert device.os_version == "18.0"
    assert device.locale == "en_US"
