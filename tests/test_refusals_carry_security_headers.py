"""The refusing layers (host check, cross-site guard) sit outside the security headers middleware and
keep their own strict policy; every layer inside it (CORS, rate limit, logging) is covered by the
page policy."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.db

PAGE_POLICY_MARK = "script-src"


async def test_a_page_carries_the_page_policy(client: AsyncClient) -> None:
    response = await client.get("/login")
    assert PAGE_POLICY_MARK in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


async def test_a_cross_site_refusal_keeps_its_strict_policy(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/login", data={}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


async def test_a_refused_host_keeps_its_strict_policy_and_gets_no_hsts(
    client: AsyncClient,
) -> None:
    response = await client.get("/login", headers={"Host": "evil.example"})
    assert response.status_code == 400
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in response.headers


async def test_a_body_the_logging_layer_refuses_carries_the_page_policy(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/events/",
        content=b"not a deflate stream",
        headers={"Content-Encoding": "deflate", "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid compressed data"}
    assert PAGE_POLICY_MARK in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


async def test_a_rate_limit_refusal_carries_the_page_policy(
    client: AsyncClient,
) -> None:
    bad = {"username": "nobody", "password": "wrong"}
    headers = {"X-Forwarded-For": "198.51.100.171"}
    response = await client.post("/login", data=bad, headers=headers)
    for _ in range(20):
        if response.status_code == 429:
            break
        response = await client.post("/login", data=bad, headers=headers)
    assert response.status_code == 429
    assert PAGE_POLICY_MARK in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


async def test_a_preflight_carries_the_page_policy(client: AsyncClient) -> None:
    response = await client.options(
        "/api/v1/events/",
        headers={
            "Origin": "https://webview.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert PAGE_POLICY_MARK in response.headers["content-security-policy"]
