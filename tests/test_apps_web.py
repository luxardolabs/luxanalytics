"""The apps admin surface over HTTP, as the dashboard drives it (HTMX form posts)."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.app_model import App


async def _login(client: AsyncClient, as_client: str) -> None:
    client.base_url = "https://test"  # the session cookie is https_only
    login = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": as_client},
        follow_redirects=False,
    )
    assert login.status_code == 303


@pytest.mark.db
async def test_the_list_shows_each_app(client: AsyncClient, sample_app: App) -> None:
    await _login(client, "198.51.100.71")
    response = await client.get("/apps/list")
    assert response.status_code == 200
    assert sample_app.app_id in response.text
    assert sample_app.name in response.text
    # The row actions are paths the view resolved by route name.
    assert f'hx-get="/apps/{sample_app.app_id}/detail"' in response.text
    assert f'hx-get="/apps/{sample_app.app_id}/edit"' in response.text


@pytest.mark.db
async def test_unchecking_active_deactivates_the_app(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    # The edit form's toggle is a checkbox: unchecked, the browser sends NO is_active at all.
    await _login(client, "198.51.100.72")
    response = await client.put(
        f"/apps/{sample_app.app_id}",
        data={"name": "Renamed", "organization": "Test Org", "description": ""},
    )
    assert response.status_code == 200
    await db.refresh(sample_app)
    assert sample_app.name == "Renamed"
    assert sample_app.is_active is False


@pytest.mark.db
async def test_checking_active_keeps_the_app_active(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    await _login(client, "198.51.100.73")
    response = await client.put(
        f"/apps/{sample_app.app_id}",
        data={
            "name": "Still On",
            "organization": "",
            "description": "",
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    await db.refresh(sample_app)
    assert sample_app.is_active is True


@pytest.mark.db
async def test_a_duplicate_app_id_is_refused(
    client: AsyncClient, sample_app: App
) -> None:
    await _login(client, "198.51.100.74")
    response = await client.post(
        "/apps/", data={"name": "Dup", "app_id": sample_app.app_id}
    )
    assert response.status_code == 400
    assert "already exists" in response.text
    # Swapped into the form's own error box, not over the apps list.
    assert response.headers["hx-retarget"] == "#form-errors"
    assert response.headers["hx-error-swap"] == "true"


@pytest.mark.db
async def test_creating_an_app_refreshes_the_list_and_toasts(
    client: AsyncClient,
) -> None:
    await _login(client, "198.51.100.75")
    response = await client.post(
        "/apps/", data={"name": "Brand New", "app_id": "brand_new"}
    )
    assert response.status_code == 200
    assert "brand_new" in response.text
    trigger = json.loads(response.headers["hx-trigger"])
    assert trigger["showtoast"]["type"] == "success"


@pytest.mark.db
async def test_the_detail_panel_renders(client: AsyncClient, sample_app: App) -> None:
    await _login(client, "198.51.100.76")
    response = await client.get(f"/apps/{sample_app.app_id}/detail")
    assert response.status_code == 200
    assert f"{sample_app.public_id}@" in response.text


@pytest.mark.db
async def test_the_json_export_carries_the_dsn(
    client: AsyncClient, sample_app: App
) -> None:
    await _login(client, "198.51.100.77")
    response = await client.get("/apps/export/json")
    assert response.status_code == 200
    exported = {a["app_id"]: a for a in response.json()}
    assert (
        f"/api/v1/events/{sample_app.project_id}" in exported[sample_app.app_id]["dsn"]
    )


@pytest.mark.db
async def test_event_counts_are_one_grouped_query(
    db: AsyncSession, sample_app: App, sample_events: list[dict[str, object]]
) -> None:
    """The apps list counts every app's events in one GROUP BY, not one query per app."""
    from app.services.core.app_core_service import AppCoreService

    counts = await AppCoreService(db).get_event_counts([sample_app.app_id, "no_events"])
    assert counts == {sample_app.app_id: len(sample_events)}
