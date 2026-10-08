"""The event detail panel reads properties and the event's own device columns, not the legacy
event_metadata column (LUXANALYTI-16): a feedback event shows its category, content and device
context as it was when the event was sent."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_model import Event
from tests.test_apps_web import _login


@pytest.mark.db
async def test_feedback_detail_reads_properties_and_the_device(
    client: AsyncClient, db: AsyncSession
) -> None:
    now = datetime.now(UTC)
    event_id = uuid4()
    await db.execute(
        insert(Event).values(
            id=event_id,
            app_id="test_app",
            name="feedback_submitted",
            timestamp=now,
            received_at=now,
            device_id="dev-fb",
            device_model="iPhone17,1",
            os_version="26.0",
            app_version="1.4",
            device_type="iPhone",
            build_number="42",
            locale="en_GB",
            timezone="Europe/London",
            is_testflight=True,
            properties={
                "feedback_category": "bug_report",
                "feedback_content": "Crash on save",
            },
            event_metadata=None,  # rows the panel must render without the legacy column
        )
    )
    await _login(client, "198.51.100.230")
    html = (await client.get(f"/dashboard/events/detail/{event_id}")).text
    for expected in (
        "Bug Report",
        "Crash on save",
        "1.4 (42)",
        "en_GB",
        "Europe/London",
        "TestFlight",
    ):
        assert expected in html, expected
