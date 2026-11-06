"""An app_id is checked when an app is created: lowercase letters, digits, underscores and hyphens.
An app that already exists keeps working whatever its app_id looks like."""

import base64
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_model import App


@pytest.mark.db
@pytest.mark.parametrize(
    "app_id", ["a/b", "A_b", "café", "a b", "a.b", "-lead", "_lead", "a\x00b", "a\nb"]
)
async def test_creating_an_app_with_a_malformed_app_id_is_a_form_error(
    dashboard_client: AsyncClient, db: AsyncSession, app_id: str
) -> None:
    response = await dashboard_client.post(
        "/apps/", data={"name": "Odd", "app_id": app_id}
    )
    assert response.status_code == 400
    assert response.headers["hx-retarget"] == "#form-errors"
    assert (await db.execute(select(App).where(App.name == "Odd"))).first() is None


@pytest.mark.db
@pytest.mark.parametrize("app_id", ["my_app", "sdk-integration", "app2", "9lives"])
async def test_creating_an_app_with_a_well_formed_app_id_works(
    dashboard_client: AsyncClient, db: AsyncSession, app_id: str
) -> None:
    response = await dashboard_client.post(
        "/apps/", data={"name": "Fine", "app_id": app_id}
    )
    assert response.status_code == 200
    assert (await db.execute(select(App).where(App.app_id == app_id))).first()


@pytest.mark.db
async def test_an_existing_app_with_another_app_id_shape_stays_usable(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    db.add(
        App(
            id=uuid4(),
            public_id="legacy_public_id_1234567890ab",
            project_id="6543210987654321",
            app_id="Legacy.App",
            name="Legacy",
            is_active=True,
            app_metadata={},
        )
    )
    await db.flush()

    edit = await dashboard_client.get("/apps/Legacy.App/edit")
    assert edit.status_code == 200
    update = await dashboard_client.put(
        "/apps/Legacy.App", data={"name": "Legacy renamed", "is_active": "on"}
    )
    assert update.status_code == 200
    token = base64.b64encode(b"legacy_public_id_1234567890ab:").decode()
    ingest = await dashboard_client.post(
        "/api/v1/events/6543210987654321",
        json={
            "name": "app_opened",
            "timestamp": "2026-10-06T12:00:00Z",
            "metadata": {},
        },
        headers={"Authorization": f"Basic {token}"},
    )
    assert ingest.status_code == 200
    assert ingest.json()["events_received"] == 1
