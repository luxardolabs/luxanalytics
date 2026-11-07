"""Deleting an app keeps its row, so its app_id stays taken and its events stay with it."""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_model import App
from app.models.event_model import Event
from app.scripts.seed_sdk_app import SDK_APP_ID, ensure_sdk_app
from app.services.core.app_core_service import AppCoreService
from tests.test_apps_web import _login
from tests.test_auth import _signed_hmac_post
from tests.test_ingest_http import EVENT, PROJECT, _dsn_auth


async def _event_count(db: AsyncSession, app_id: str) -> int:
    result = await db.execute(
        select(func.count()).select_from(Event).where(Event.app_id == app_id)
    )
    return result.scalar_one()


@pytest.mark.db
async def test_a_deleted_apps_id_cannot_be_registered_again(
    client: AsyncClient,
    db: AsyncSession,
    sample_app: App,
    sample_events: list[dict[str, object]],
) -> None:
    await _login(client, "198.51.100.190")
    kept = await _event_count(db, sample_app.app_id)
    assert kept > 0

    deleted = await client.delete(f"/apps/{sample_app.app_id}")
    assert deleted.status_code == 200

    again = await client.post(
        "/apps/", data={"name": "Someone else", "app_id": sample_app.app_id}
    )
    assert again.status_code == 400
    assert "deleted app" in again.text
    # The events stay, held by the deleted app's row.
    assert await _event_count(db, sample_app.app_id) == kept
    rows = await db.execute(select(App).where(App.app_id == sample_app.app_id))
    assert len(rows.scalars().all()) == 1


@pytest.mark.db
async def test_a_deleted_app_is_gone_from_the_list_and_its_panel(
    client: AsyncClient, sample_app: App
) -> None:
    await _login(client, "198.51.100.191")
    assert sample_app.name in (await client.get("/apps/list")).text

    await client.delete(f"/apps/{sample_app.app_id}")

    assert sample_app.name not in (await client.get("/apps/list")).text
    assert sample_app.name not in (await client.get("/apps/list?q=test")).text
    assert (await client.get(f"/apps/{sample_app.app_id}/detail")).status_code == 404


@pytest.mark.db
async def test_deleting_an_inactive_app_keeps_hmac_ingest_refused(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    await _login(client, "198.51.100.192")
    sample_app.is_active = False
    await db.flush()
    await client.delete(f"/apps/{sample_app.app_id}")
    body, headers = _signed_hmac_post(sample_app.app_id)

    response = await client.post("/api/v1/events/", content=body, headers=headers)

    assert response.status_code == 403, response.text
    assert await _event_count(db, sample_app.app_id) == 0


@pytest.mark.db
async def test_deleting_an_active_app_refuses_hmac_ingest(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    await _login(client, "198.51.100.193")
    await client.delete(f"/apps/{sample_app.app_id}")
    body, headers = _signed_hmac_post(sample_app.app_id)

    response = await client.post("/api/v1/events/", content=body, headers=headers)

    assert response.status_code == 403, response.text
    assert await _event_count(db, sample_app.app_id) == 0


@pytest.mark.db
async def test_deleting_an_app_refuses_dsn_ingest(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    await _login(client, "198.51.100.194")
    await client.delete(f"/apps/{sample_app.app_id}")

    by_project = await client.post(
        f"/api/v1/events/{PROJECT}", json=EVENT, headers=_dsn_auth()
    )
    by_public_id = await client.post(
        "/api/v1/events/public", json=EVENT, headers=_dsn_auth()
    )

    assert by_project.status_code == 404
    assert by_public_id.status_code == 401
    assert await _event_count(db, sample_app.app_id) == 0


@pytest.mark.db
async def test_the_seed_restores_a_deleted_sdk_app(db: AsyncSession) -> None:
    core = AppCoreService(db)
    created = await ensure_sdk_app(db)
    assert await core.delete_app(SDK_APP_ID)

    restored = await ensure_sdk_app(db)

    assert restored.deleted_at is None
    assert restored.is_active
    assert (restored.id, restored.public_id) == (created.id, created.public_id)
