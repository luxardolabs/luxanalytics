"""The login page's banners come from a closed set of codes in the query string, each mapped to
fixed text on the server: text the URL carries is never rendered."""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from tests.test_apps_web import _login

pytestmark = pytest.mark.db

SPOOF = "Your password has expired. Email it to security@attacker.example"


@pytest.mark.parametrize("parameter", ["error", "message"])
async def test_login_page_does_not_render_text_the_url_carries(
    client: AsyncClient, parameter: str
) -> None:
    response = await client.get("/login", params={parameter: SPOOF})
    assert response.status_code == 200
    assert "attacker.example" not in response.text
    assert "text-red-300" not in response.text
    assert "text-green-300" not in response.text


async def test_a_known_error_code_shows_its_fixed_text(client: AsyncClient) -> None:
    response = await client.get("/login", params={"error": "invalid"})
    assert "Invalid username or password" in response.text
    assert "text-red-300" in response.text


async def test_a_failed_login_lands_on_the_invalid_code(client: AsyncClient) -> None:
    response = await client.post(
        "/login",
        data={"username": settings.DASHBOARD_USERNAME, "password": "wrong"},
        headers={"X-Forwarded-For": "198.51.100.151"},
        follow_redirects=True,
    )
    assert "Invalid username or password" in response.text


async def test_an_error_and_a_message_together_show_only_the_error(
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/login", params={"error": "invalid", "message": "logged_out"}
    )
    assert "Invalid username or password" in response.text
    assert "You have been logged out" not in response.text
    assert "text-green-300" not in response.text


async def test_logout_confirmation_is_shown(client: AsyncClient) -> None:
    await _login(client, "198.51.100.152")
    response = await client.post("/logout", follow_redirects=True)
    assert response.status_code == 200
    assert "You have been logged out" in response.text
    assert "text-green-300" in response.text
