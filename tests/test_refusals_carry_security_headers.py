"""Security headers are set by the outermost middleware, so a refusal from any layer inside it (the
host check, the cross-site guard) carries the same CSP and frame policy as a page."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.db


async def test_a_page_carries_the_headers_control(client: AsyncClient) -> None:
    response = await client.get("/login")
    assert "content-security-policy" in response.headers
    assert response.headers["x-frame-options"] == "DENY"


async def test_a_cross_site_refusal_carries_the_headers(client: AsyncClient) -> None:
    response = await client.post(
        "/login", data={}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert "content-security-policy" in response.headers
    assert response.headers["x-frame-options"] == "DENY"


async def test_a_body_the_logging_layer_refuses_carries_the_headers(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/events/",
        content=b"not a deflate stream",
        headers={"Content-Encoding": "deflate", "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid compressed data"}
    assert "content-security-policy" in response.headers
    assert response.headers["x-frame-options"] == "DENY"


async def test_a_refused_host_carries_the_headers(client: AsyncClient) -> None:
    response = await client.get("/login", headers={"Host": "evil.example"})
    assert response.status_code == 400
    assert "content-security-policy" in response.headers
    assert response.headers["x-frame-options"] == "DENY"
