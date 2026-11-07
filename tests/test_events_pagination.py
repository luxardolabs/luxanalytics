"""The events list never shows an empty table under a total: a page past the end reads the last
page, and switching apps lands on the first page."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_model import Event


def _event(app_id: str, name: str) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "app_id": app_id,
        "name": name,
        "timestamp": datetime.now(UTC),
        "received_at": datetime.now(UTC),
        "properties": None,
    }


@pytest.mark.db
@pytest.mark.parametrize("route", ["events", "events/content"])
async def test_a_page_past_the_end_shows_the_last_page(
    dashboard_client: AsyncClient, db: AsyncSession, route: str
) -> None:
    await db.execute(insert(Event), [_event("pg", "past_probe") for _ in range(3)])
    await db.flush()

    response = await dashboard_client.get(f"/dashboard/{route}?app_id=pg&page=7")
    assert response.status_code == 200
    assert "Past Probe" in response.text
    assert "No events" not in response.text


@pytest.mark.db
async def test_switching_to_a_smaller_app_from_a_later_page_lands_on_its_events(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    await db.execute(
        insert(Event),
        [_event("big", "big_probe") for _ in range(120)]
        + [_event("small", "small_probe") for _ in range(3)],
    )
    await db.flush()

    switched = await dashboard_client.post(
        "/set-app-context",
        data={"app_id": "small"},
        headers={"HX-Current-URL": "https://test/dashboard/events?page=3&hours=24"},
    )
    target = switched.headers["hx-redirect"]
    assert "page=" not in target
    assert "hours=24" in target

    page = await dashboard_client.get(target)
    assert page.status_code == 200
    assert "Small Probe" in page.text
