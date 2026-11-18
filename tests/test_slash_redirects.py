"""A path that differs from a route only by its trailing slash: a GET or HEAD is redirected to the
route by a relative Location (behind the TLS-ending proxy the request reads http, so an absolute
one would send the browser to http://), and an unsafe method is never redirected (a 307 repeats
the method, so it would act on whatever the other path names)."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.db


@pytest.mark.parametrize(
    ("path", "location"),
    [
        ("/dashboard/events/", "/dashboard/events"),
        ("/dashboard/events/?hours=24&app_id=a%2Fb", "/dashboard/events?hours=24&app_id=a%2Fb"),
        ("/apps", "/apps/"),
        ("/dashboard/overview/?app_id=a%3Fb", "/dashboard/overview?app_id=a%3Fb"),
    ],
)
async def test_a_get_is_redirected_by_a_relative_location(
    dashboard_client: AsyncClient, path: str, location: str
) -> None:
    response = await dashboard_client.get(path)

    assert response.status_code in (307, 308), response.status_code
    assert response.headers["location"] == location


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/logout/"),
        ("POST", "/login/"),
        ("POST", "/set-app-context/"),
        ("POST", "/apps"),
        ("PUT", "/apps/test_app%2F"),
        ("DELETE", "/apps/test_app%2F"),
        ("POST", "/api/v1/events/public/"),
    ],
)
async def test_an_unsafe_method_is_never_redirected(
    dashboard_client: AsyncClient, method: str, path: str
) -> None:
    response = await dashboard_client.request(method, path)

    assert response.status_code not in (301, 302, 303, 307, 308), (
        response.status_code,
        response.headers.get("location"),
    )


@pytest.mark.parametrize("path", ["//evil.example/", "//evil.example", "/\\evil.example/"])
async def test_a_slash_redirect_never_leaves_the_site(
    dashboard_client: AsyncClient, path: str
) -> None:
    response = await dashboard_client.get(path)

    location = response.headers.get("location", "")
    assert not location.startswith(("//", "/\\")), location
    assert "://" not in location, location
