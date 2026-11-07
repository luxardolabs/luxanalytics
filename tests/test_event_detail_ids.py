"""The event detail panel reads an event by its canonical UUID, whichever textual form of it the
URL holds; anything that is not a UUID is a 404."""

from datetime import UTC, datetime
from urllib.parse import quote
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_model import Event


@pytest.mark.db
@pytest.mark.parametrize(
    "form",
    [
        "{id}",
        "{id_upper}",
        "{id_hex}",
        "urn:uuid:{id}",
        "{{{id}}}",
    ],
)
async def test_every_textual_form_of_a_uuid_finds_the_event(
    dashboard_client: AsyncClient, db: AsyncSession, form: str
) -> None:
    event_id = uuid4()
    await db.execute(
        insert(Event).values(
            [
                {
                    "id": str(event_id),
                    "app_id": "detail_app",
                    "name": "detail_probe",
                    "timestamp": datetime.now(UTC),
                    "received_at": datetime.now(UTC),
                    "properties": None,
                }
            ]
        )
    )
    await db.flush()
    path = form.format(id=event_id, id_upper=str(event_id).upper(), id_hex=event_id.hex)

    response = await dashboard_client.get(
        f"/dashboard/events/detail/{quote(path, safe=':')}"
    )
    assert response.status_code == 200
    assert "Detail Probe" in response.text


@pytest.mark.db
@pytest.mark.parametrize("value", ["not-a-uuid", "12345", "a%00b", "%7Bnope%7D"])
async def test_something_that_is_not_a_uuid_is_a_404(
    dashboard_client: AsyncClient, value: str
) -> None:
    response = await dashboard_client.get(f"/dashboard/events/detail/{value}")
    assert response.status_code == 404
