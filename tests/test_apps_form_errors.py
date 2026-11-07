"""A refused app form answers in the form's own error box (a 400), never a 500."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_model import App
from app.services.core.app_core_service import AppCoreService

OVERLONG = "o" * 300


def _assert_form_error(response: Response) -> None:
    assert response.status_code == 400
    assert response.headers["hx-retarget"] == "#form-errors"
    assert response.headers["hx-error-swap"] == "true"


@pytest.mark.db
async def test_update_with_an_overlong_organization_is_a_form_error(
    dashboard_client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    response = await dashboard_client.put(
        f"/apps/{sample_app.app_id}",
        data={"name": "ok", "organization": OVERLONG, "is_active": "on"},
    )
    _assert_form_error(response)
    await db.refresh(sample_app)
    assert sample_app.name == "Test Application"


@pytest.mark.db
async def test_create_with_an_overlong_organization_is_a_form_error(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    response = await dashboard_client.post(
        "/apps/", data={"name": "ok", "app_id": "long_org", "organization": OVERLONG}
    )
    _assert_form_error(response)
    assert (
        await db.execute(select(App).where(App.app_id == "long_org"))
    ).first() is None


@pytest.mark.db
@pytest.mark.parametrize(
    "data",
    [
        {"name": "", "app_id": "blank_name"},
        {"name": "ok", "app_id": ""},
        {"name": "", "app_id": ""},
        {"app_id": "no_name"},
        {"name": "no_app_id"},
    ],
)
async def test_a_blank_or_missing_required_create_field_lands_in_the_form_box(
    dashboard_client: AsyncClient, data: dict[str, str]
) -> None:
    response = await dashboard_client.post("/apps/", data=data)
    _assert_form_error(response)


@pytest.mark.db
async def test_the_edit_form_clears_an_optional_field_left_empty(
    dashboard_client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    sample_app.description = "to be cleared"
    await db.flush()

    response = await dashboard_client.put(
        f"/apps/{sample_app.app_id}",
        data={
            "name": "Kept",
            "organization": "",
            "description": "",
            "is_active": "on",
        },
    )
    assert response.status_code == 200
    await db.refresh(sample_app)
    assert sample_app.name == "Kept"
    assert sample_app.organization is None
    assert sample_app.description is None


@pytest.mark.db
async def test_the_edit_form_leaves_a_field_it_does_not_send_unchanged(
    dashboard_client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    response = await dashboard_client.put(
        f"/apps/{sample_app.app_id}", data={"name": "Only name", "is_active": "on"}
    )
    assert response.status_code == 200
    await db.refresh(sample_app)
    assert sample_app.name == "Only name"
    assert sample_app.organization == "Test Org"


@pytest.mark.db
async def test_the_edit_form_with_an_empty_name_is_a_form_error(
    dashboard_client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    response = await dashboard_client.put(
        f"/apps/{sample_app.app_id}", data={"name": "", "is_active": "on"}
    )
    _assert_form_error(response)
    await db.refresh(sample_app)
    assert sample_app.name == "Test Application"


@pytest.mark.db
async def test_losing_the_duplicate_app_id_race_is_the_already_exists_error(
    dashboard_client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    # Both creates pass the existence read before either inserts: the unique index decides.
    with patch.object(AppCoreService, "get_app_by_app_id", return_value=None):
        response = await dashboard_client.post(
            "/apps/", data={"name": "Dup", "app_id": sample_app.app_id}
        )
    _assert_form_error(response)
    assert "already exists" in response.text
    # The request's transaction is still usable after the refused insert.
    assert (await dashboard_client.get("/apps/list")).status_code == 200
