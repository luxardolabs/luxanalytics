"""Every HTML surface must RENDER, not just route: a template-engine break (Starlette 1.x dropped the
legacy TemplateResponse(name, context) form) 500s every page while API tests stay green."""

import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.mark.db
async def test_login_page_renders(client: AsyncClient) -> None:
    response = await client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'action="/login"' in response.text


@pytest.mark.db
async def test_dashboard_renders_after_real_login(client: AsyncClient) -> None:
    # Log in through the REAL route (never a forged session), then render an authenticated page.
    # The session cookie is https_only (LUXANALYTI-1), so it only round-trips over https, as in prod.
    client.base_url = "https://test"
    login = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
            "next": "/dashboard/overview",
        },
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/dashboard/overview"

    page = await client.get("/dashboard/overview")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
