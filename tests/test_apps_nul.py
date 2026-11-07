"""A NUL byte on the apps surface is refused where it is parsed: a 422 for the query and the path,
the form's own error box (a 400) for a form field. The database cannot store one."""

import pytest
from httpx import AsyncClient

from app.models.app_model import App


@pytest.mark.db
@pytest.mark.parametrize(
    "method,url",
    [
        ("GET", "/apps/list?q=a%00b"),
        ("GET", "/apps/a%00b/edit"),
        ("GET", "/apps/a%00b/detail"),
        ("PUT", "/apps/a%00b"),
        ("DELETE", "/apps/a%00b"),
    ],
)
async def test_a_nul_byte_in_an_apps_path_or_query_is_a_422(
    dashboard_client: AsyncClient, method: str, url: str
) -> None:
    response = await dashboard_client.request(method, url, data={"name": "x"})
    assert response.status_code == 422


@pytest.mark.db
@pytest.mark.parametrize("field", ["name", "organization", "description"])
async def test_a_nul_byte_in_a_create_form_field_lands_in_the_form_box(
    dashboard_client: AsyncClient, field: str
) -> None:
    data = {"name": "ok", "app_id": "nul_probe", field: "a\x00b"}
    response = await dashboard_client.post("/apps/", data=data)
    assert response.status_code == 400
    assert response.headers["hx-retarget"] == "#form-errors"
    assert "NUL" in response.text


@pytest.mark.db
@pytest.mark.parametrize("field", ["name", "organization", "description"])
async def test_a_nul_byte_in_an_update_form_field_lands_in_the_form_box(
    dashboard_client: AsyncClient, sample_app: App, field: str
) -> None:
    data = {"name": "ok", field: "a\x00b"}
    response = await dashboard_client.put(f"/apps/{sample_app.app_id}", data=data)
    assert response.status_code == 400
    assert response.headers["hx-retarget"] == "#form-errors"
    assert "NUL" in response.text


@pytest.mark.db
async def test_the_apps_list_search_still_filters(
    dashboard_client: AsyncClient, sample_app: App
) -> None:
    assert (await dashboard_client.get("/apps/list?q=test_app")).status_code == 200
    assert (await dashboard_client.get("/apps/list?q=")).status_code == 200
