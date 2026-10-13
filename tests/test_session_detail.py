"""The session panel lists a capped number of events, but its count and duration are the whole
session's: they used to be taken from the capped list, so a long session read as 200 events and
ended at its 200th."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.event_model import Event
from app.services.core.analytics_core_service import (
    SESSION_EVENTS_SHOWN,
    AnalyticsCoreService,
)

START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


async def _seed_session(db: AsyncSession, session_id: str, count: int) -> None:
    rows = [
        {
            "id": str(uuid4()),
            "app_id": "session_app",
            "name": "screen_viewed",
            "timestamp": START + timedelta(seconds=i),
            "received_at": START + timedelta(seconds=i),
            "user_id": "session_user",
            "session_id": session_id,
            "properties": None,
        }
        for i in range(count)
    ]
    await db.execute(insert(Event), rows)
    await db.flush()


@pytest.mark.db
async def test_a_long_session_reports_its_whole_count_and_span(
    db: AsyncSession,
) -> None:
    count = SESSION_EVENTS_SHOWN + 5
    await _seed_session(db, "long_session", count)
    detail = await AnalyticsCoreService(db).get_session_detail("long_session")
    assert len(detail["events"]) == SESSION_EVENTS_SHOWN
    assert detail["total_events"] == count
    assert detail["last_event"] == START + timedelta(seconds=count - 1)
    assert detail["duration_seconds"] == count - 1


@pytest.mark.db
async def test_a_short_session_is_whole_control(db: AsyncSession) -> None:
    await _seed_session(db, "short_session", 3)
    detail = await AnalyticsCoreService(db).get_session_detail("short_session")
    assert detail["total_events"] == 3
    assert detail["duration_seconds"] == 2


@pytest.mark.db
async def test_the_panel_says_when_it_shows_only_the_first_events(
    client: AsyncClient, db: AsyncSession
) -> None:
    count = SESSION_EVENTS_SHOWN + 5
    await _seed_session(db, "long_session", count)
    client.base_url = "https://test"
    r = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": "198.51.100.220"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = await client.get("/dashboard/session/long_session")
    assert page.status_code == 200
    assert f"Showing the first {SESSION_EVENTS_SHOWN} of {count} events" in " ".join(
        page.text.split()
    )
