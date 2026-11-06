"""An event whose stored properties are not a JSON object (a list, a string, a number: rows ingest
never writes) shows as an event without properties; no dashboard page fails on it."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_model import Event


@pytest.mark.db
@pytest.mark.parametrize("properties", [[1, 2], "str", 7, True])
async def test_the_pages_render_an_event_with_non_object_properties(
    dashboard_client: AsyncClient, db: AsyncSession, properties: object
) -> None:
    event_id = str(uuid4())
    await db.execute(
        insert(Event).values(
            [
                {
                    "id": event_id,
                    "app_id": "odd_app",
                    "name": "screen_viewed",
                    "timestamp": datetime.now(UTC),
                    "received_at": datetime.now(UTC),
                    "user_id": "odd_user",
                    "session_id": "odd_session",
                    "properties": properties,
                }
            ]
        )
    )
    await db.flush()

    for path in [
        "/dashboard/events/content?app_id=odd_app",
        "/dashboard/explorer/content?app_id=odd_app",
        f"/dashboard/events/detail/{event_id}",
        "/dashboard/session/odd_session",
        "/dashboard/user/odd_user",
        "/dashboard/feedback/content?app_id=odd_app",
        "/dashboard/errors/content?app_id=odd_app",
        "/dashboard/overview/content?app_id=odd_app",
    ]:
        response = await dashboard_client.get(path)
        assert response.status_code == 200, path


@pytest.mark.db
async def test_the_explorer_counts_only_events_with_object_properties(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    rows = [
        {
            "id": str(uuid4()),
            "app_id": "odd_app",
            "name": name,
            "timestamp": datetime.now(UTC),
            "received_at": datetime.now(UTC),
            "properties": properties,
        }
        for name, properties in [
            ("object_event", {"screen": "home"}),
            ("list_event", [1, 2]),
        ]
    ]
    await db.execute(insert(Event).values(rows))
    await db.flush()

    response = await dashboard_client.get("/dashboard/explorer/content?app_id=odd_app")
    assert response.status_code == 200
    assert "Object Event" in response.text
    assert "List Event" not in response.text
