"""Ingest is idempotent on the SDK's event `id` (LUXANALYTI-68): a retried batch whose first
response was lost must not store its events twice. A duplicate is acknowledged (2xx), not an error."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_model import App
from app.models.event_model import Event
from tests.test_ingest_http import PROJECT, _dsn_auth


def _event(event_id: str | None) -> dict[str, object]:
    event: dict[str, object] = {
        "name": "app_opened",
        "timestamp": "2026-10-06T12:00:00Z",
        "metadata": {},
    }
    if event_id is not None:
        event["id"] = event_id
    return event


async def _stored(db: AsyncSession, app_id: str = "test_app") -> int:
    query = select(func.count()).select_from(Event).where(Event.app_id == app_id)
    return (await db.execute(query)).scalar_one()


async def _post(
    client: AsyncClient, events: list[dict[str, object]]
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/events/{PROJECT}", json={"events": events}, headers=_dsn_auth()
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


@pytest.mark.db
async def test_a_retried_event_is_stored_once(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    event = _event(str(uuid.uuid4()))
    first = await _post(client, [event])
    retry = await _post(client, [event])  # the SDK resends after a lost response
    assert await _stored(db) == 1
    assert first["duplicates"] == 0
    assert retry["events_received"] == 1 and retry["duplicates"] == 1


@pytest.mark.db
async def test_the_same_id_under_two_apps_is_two_events(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    from app.schemas.event_schema import EventCreate
    from app.services.core.event_core_service import EventCoreService

    shared = str(uuid.uuid4())
    await _post(client, [_event(shared)])
    await EventCoreService(db).create_events(
        "other_app", [EventCreate(**_event(shared))]
    )
    assert await _stored(db) == 1
    assert await _stored(db, "other_app") == 1


@pytest.mark.db
async def test_an_event_without_an_id_is_still_accepted(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    await _post(client, [_event(None)])
    await _post(client, [_event(None)])
    assert await _stored(db) == 2  # no key, no dedupe


@pytest.mark.db
async def test_a_batch_repeating_an_id_stores_it_once(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    event = _event(str(uuid.uuid4()))
    body = await _post(client, [event, event, _event(str(uuid.uuid4()))])
    assert await _stored(db) == 2
    assert body["events_received"] == 3 and body["duplicates"] == 1
