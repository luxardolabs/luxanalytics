"""A device whose clock runs fast must not lose its events (LUXANALYTI-80).

The SDK stamps events with the device clock and drops a batch the server answers with 4xx, so a
rejected future timestamp silently lost every event from a fast device. A timestamp beyond the
tolerance is clamped to the server's receive time instead; one within it is stored as sent."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.event_model import Event
from tests.test_ingest_http import PROJECT, _dsn_auth


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


async def _send(client: AsyncClient, name: str, at: datetime) -> int:
    response = await client.post(
        f"/api/v1/events/{PROJECT}",
        json={"name": name, "timestamp": _iso(at), "metadata": {}},
        headers=_dsn_auth(),
    )
    return response.status_code


async def _stored(db: AsyncSession, name: str) -> Event:
    return (await db.execute(select(Event).where(Event.name == name))).scalar_one()


@pytest.mark.db
async def test_a_fast_clock_is_clamped_not_rejected(
    client: AsyncClient, db: AsyncSession, sample_app: object
) -> None:
    sent = datetime.now(UTC) + timedelta(hours=3)
    assert await _send(client, "fast_clock", sent) == 200
    event = await _stored(db, "fast_clock")
    assert event.timestamp <= event.received_at + timedelta(seconds=1)
    assert event.timestamp < sent


@pytest.mark.db
async def test_a_small_skew_is_stored_as_sent(
    client: AsyncClient, db: AsyncSession, sample_app: object
) -> None:
    sent = datetime.now(UTC) + timedelta(
        seconds=settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE // 2
    )
    assert await _send(client, "small_skew", sent) == 200
    assert (await _stored(db, "small_skew")).timestamp == sent.replace(
        microsecond=sent.microsecond
    )


@pytest.mark.db
async def test_a_past_timestamp_is_stored_as_sent(
    client: AsyncClient, db: AsyncSession, sample_app: object
) -> None:
    sent = datetime.now(UTC) - timedelta(days=2)
    assert await _send(client, "queued_offline", sent) == 200
    assert (await _stored(db, "queued_offline")).timestamp == sent
