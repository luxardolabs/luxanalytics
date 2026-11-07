"""An app's name has a visible character: a name of only spaces is refused when it is written, and
an app already stored with one stays listed and editable."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_model import App


@pytest.mark.db
@pytest.mark.parametrize("name", ["   ", "\t", " \n "])
async def test_creating_an_app_with_a_blank_name_is_a_form_error(
    dashboard_client: AsyncClient, db: AsyncSession, name: str
) -> None:
    response = await dashboard_client.post(
        "/apps/", data={"app_id": "ws_name", "name": name}
    )
    assert response.status_code == 400
    assert response.headers["hx-retarget"] == "#form-errors"
    assert (
        await db.execute(select(App).where(App.app_id == "ws_name"))
    ).first() is None


@pytest.mark.db
async def test_a_name_is_stored_without_its_surrounding_spaces(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    response = await dashboard_client.post(
        "/apps/", data={"app_id": "padded", "name": "  Padded  "}
    )
    assert response.status_code == 200
    stored = (await db.execute(select(App).where(App.app_id == "padded"))).scalar_one()
    assert stored.name == "Padded"


@pytest.mark.db
async def test_renaming_an_app_to_blank_is_a_form_error(
    dashboard_client: AsyncClient, sample_app: App
) -> None:
    response = await dashboard_client.put(
        f"/apps/{sample_app.app_id}", data={"name": "   ", "is_active": "on"}
    )
    assert response.status_code == 400


@pytest.mark.db
async def test_an_app_already_stored_with_a_blank_name_stays_listed_and_editable(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    blank = App(
        id=uuid4(),
        public_id=uuid4().hex,
        project_id=str(uuid4().int)[:16],
        app_id="blank_name_row",
        name="   ",
        is_active=True,
        app_metadata={},
    )
    db.add(blank)
    await db.flush()

    assert (await dashboard_client.get("/apps/list")).status_code == 200
    assert (await dashboard_client.get("/apps/blank_name_row/edit")).status_code == 200
    assert (
        await dashboard_client.get("/apps/blank_name_row/detail")
    ).status_code == 200

    # Saving the form as it was shown gives a clear form error, naming the field.
    unchanged = await dashboard_client.put(
        "/apps/blank_name_row", data={"name": "   ", "is_active": "on"}
    )
    assert unchanged.status_code == 400
    assert "name" in unchanged.text

    named = await dashboard_client.put(
        "/apps/blank_name_row", data={"name": "Now named", "is_active": "on"}
    )
    assert named.status_code == 200
    await db.refresh(blank)
    assert blank.name == "Now named"
