"""The events search is scoped like the list beside it: to the selected app and the time window."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_model import Event


def _event(app_id: str, name: str, age: timedelta = timedelta()) -> dict[str, object]:
    when = datetime.now(UTC) - age
    return {
        "id": str(uuid4()),
        "app_id": app_id,
        "name": name,
        "timestamp": when,
        "received_at": when,
        "properties": None,
    }


@pytest.mark.db
async def test_a_scoped_search_does_not_list_another_apps_events(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    await db.execute(
        insert(Event), [_event("app_a", "mine_evt"), _event("app_b", "theirs_evt")]
    )
    await db.flush()

    scoped = await dashboard_client.get(
        "/dashboard/events/content?app_id=app_a&search_q=evt"
    )
    assert "Mine Evt" in scoped.text
    assert "Theirs Evt" not in scoped.text

    everywhere = await dashboard_client.get("/dashboard/events/content?search_q=evt")
    assert "Mine Evt" in everywhere.text and "Theirs Evt" in everywhere.text


@pytest.mark.db
async def test_a_search_honours_the_time_window(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    await db.execute(
        insert(Event),
        [
            _event("app_a", "recent_evt"),
            _event("app_a", "old_evt", age=timedelta(hours=48)),
        ],
    )
    await db.flush()

    day = await dashboard_client.get(
        "/dashboard/events/content?app_id=app_a&search_q=evt&hours=24"
    )
    assert "Recent Evt" in day.text
    assert "Old Evt" not in day.text

    everything = await dashboard_client.get(
        "/dashboard/events/content?app_id=app_a&search_q=evt&hours=0"
    )
    assert "Recent Evt" in everything.text and "Old Evt" in everything.text
