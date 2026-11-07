"""The nav selector accepts every app_id its dropdown offers, up to what a cookie can hold."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_model import Event

HX = {"HX-Current-URL": "https://test/dashboard/events"}


def _event(app_id: str) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "app_id": app_id,
        "name": "long_probe",
        "timestamp": datetime.now(UTC),
        "received_at": datetime.now(UTC),
        "properties": None,
    }


@pytest.mark.db
@pytest.mark.parametrize("app_id", ["l" * 300, "l" * 2000, "é" * 400])
async def test_a_long_app_id_the_dropdown_offers_can_be_selected(
    dashboard_client: AsyncClient, db: AsyncSession, app_id: str
) -> None:
    # events.app_id is unbounded text, and the dropdown lists the app_ids of events.
    await db.execute(insert(Event), [_event(app_id)])
    await db.flush()
    assert app_id in (await dashboard_client.get("/dashboard/apps-dropdown")).text

    response = await dashboard_client.post(
        "/set-app-context", data={"app_id": app_id}, headers=HX
    )
    assert response.status_code == 204

    listing = await dashboard_client.get("/dashboard/events/content")
    assert "Long Probe" in listing.text


@pytest.mark.db
@pytest.mark.parametrize("app_id", ["l" * 4000, "é" * 700])
async def test_an_app_id_no_cookie_can_hold_is_a_422(
    dashboard_client: AsyncClient, app_id: str
) -> None:
    response = await dashboard_client.post(
        "/set-app-context", data={"app_id": app_id}, headers=HX
    )
    assert response.status_code == 422
