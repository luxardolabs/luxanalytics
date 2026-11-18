"""The app forms write only what they were sent: a request without the edit form's name leaves the
Active toggle alone, a name of only invisible characters is refused like a blank one, and a blank
organization or description clears the field."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_model import App

INVISIBLE_NAMES = ["​", "⁠", "﻿", "ㅤ", "­", " ​⁠ "]


@pytest.mark.db
@pytest.mark.parametrize(
    "data", [{}, {"description": "only this"}, {"organization": "Org"}]
)
async def test_a_put_without_the_name_field_leaves_the_app_active(
    dashboard_client: AsyncClient,
    db: AsyncSession,
    sample_app: App,
    data: dict[str, str],
) -> None:
    """Inactive is the ingest kill switch: only the browser's edit form, which always sends the
    name, means an unchecked box by a missing is_active."""
    response = await dashboard_client.put(f"/apps/{sample_app.app_id}", data=data)

    assert response.status_code == 200
    await db.refresh(sample_app)
    assert sample_app.is_active is True


@pytest.mark.db
async def test_a_json_put_leaves_the_app_active(
    dashboard_client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    response = await dashboard_client.put(
        f"/apps/{sample_app.app_id}", json={"description": "x"}
    )

    assert response.status_code in (200, 422)
    await db.refresh(sample_app)
    assert sample_app.is_active is True


@pytest.mark.db
@pytest.mark.parametrize("name", INVISIBLE_NAMES)
async def test_creating_an_app_with_an_invisible_name_is_a_form_error(
    dashboard_client: AsyncClient, db: AsyncSession, name: str
) -> None:
    response = await dashboard_client.post(
        "/apps/", data={"app_id": "invisible_name", "name": name}
    )

    assert response.status_code == 400
    assert response.headers["hx-retarget"] == "#form-errors"
    assert (
        await db.execute(select(App).where(App.app_id == "invisible_name"))
    ).first() is None


@pytest.mark.db
@pytest.mark.parametrize("name", INVISIBLE_NAMES)
async def test_renaming_an_app_to_an_invisible_name_is_a_form_error(
    dashboard_client: AsyncClient, db: AsyncSession, sample_app: App, name: str
) -> None:
    response = await dashboard_client.put(
        f"/apps/{sample_app.app_id}", data={"name": name, "is_active": "on"}
    )

    assert response.status_code == 400
    await db.refresh(sample_app)
    assert sample_app.name == "Test Application"


@pytest.mark.db
async def test_a_name_with_a_visible_character_beside_invisible_ones_is_kept(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    response = await dashboard_client.post(
        "/apps/", data={"app_id": "zwj_name", "name": "A​B"}
    )

    assert response.status_code == 200
    stored = (await db.execute(select(App).where(App.app_id == "zwj_name"))).scalar_one()
    assert stored.name == "A​B"


@pytest.mark.db
@pytest.mark.parametrize("blank", ["   ", "\t\n", " ​ "])
async def test_a_blank_organization_or_description_on_create_is_stored_as_none(
    dashboard_client: AsyncClient, db: AsyncSession, blank: str
) -> None:
    response = await dashboard_client.post(
        "/apps/",
        data={
            "app_id": "blank_fields",
            "name": "Blank fields",
            "organization": blank,
            "description": blank,
        },
    )

    assert response.status_code == 200
    stored = (
        await db.execute(select(App).where(App.app_id == "blank_fields"))
    ).scalar_one()
    assert stored.organization is None
    assert stored.description is None


@pytest.mark.db
@pytest.mark.parametrize("blank", ["   ", "\t\n", " ​ "])
async def test_a_blank_organization_or_description_on_edit_clears_it(
    dashboard_client: AsyncClient, db: AsyncSession, sample_app: App, blank: str
) -> None:
    sample_app.description = "Before"
    await db.flush()

    response = await dashboard_client.put(
        f"/apps/{sample_app.app_id}",
        data={
            "name": "Test Application",
            "organization": blank,
            "description": blank,
            "is_active": "on",
        },
    )

    assert response.status_code == 200
    await db.refresh(sample_app)
    assert sample_app.organization is None
    assert sample_app.description is None
