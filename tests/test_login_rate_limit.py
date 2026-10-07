"""POST /login is rate limited per REAL client address (FLEET-RATE-LIMIT-STANDARD).

The test client's peer is 127.0.0.1, a trusted proxy, so each test names its client through
X-Forwarded-For, exactly as nginx does. That also gives every test its own bucket.
"""

import pytest
from httpx import AsyncClient
from starlette.requests import Request

from app.core.client_ip import client_ip_from_request
from app.core.config import settings

_LIMIT = int(settings.LOGIN_RATE_LIMIT.split("/")[0])


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


@pytest.mark.db
async def test_login_refuses_past_the_limit_with_retry_after(
    client: AsyncClient,
) -> None:
    bad = {"username": "nobody", "password": "wrong", "next": "/"}
    headers = {"X-Forwarded-For": "203.0.113.7"}
    for _ in range(_LIMIT):
        response = await client.post(
            "/login", data=bad, headers=headers, follow_redirects=False
        )
        assert response.status_code == 303

    refused = await client.post(
        "/login", data=bad, headers=headers, follow_redirects=False
    )
    assert refused.status_code == 429
    assert "retry-after" in refused.headers


@pytest.mark.db
async def test_one_client_exhausting_its_limit_does_not_lock_out_another(
    client: AsyncClient,
) -> None:
    bad = {"username": "nobody", "password": "wrong", "next": "/"}
    for _ in range(_LIMIT + 1):
        await client.post(
            "/login",
            data=bad,
            headers={"X-Forwarded-For": "203.0.113.8"},
            follow_redirects=False,
        )
    other = await client.post(
        "/login",
        data=bad,
        headers={"X-Forwarded-For": "203.0.113.9"},
        follow_redirects=False,
    )
    assert other.status_code == 303


def test_forwarded_for_is_honoured_from_a_trusted_proxy() -> None:
    assert (
        client_ip_from_request(_request("172.18.0.5", "198.51.100.4")) == "198.51.100.4"
    )


def test_forwarded_for_from_an_untrusted_peer_is_ignored() -> None:
    # A client talking to the app directly cannot pick its own bucket.
    assert (
        client_ip_from_request(_request("198.51.100.4", "203.0.113.1"))
        == "198.51.100.4"
    )


def test_a_spoofed_leftmost_entry_is_skipped() -> None:
    # nginx appends the real client on the right; whatever the client sent sits to its left.
    assert (
        client_ip_from_request(_request("172.18.0.5", "203.0.113.1, 198.51.100.4"))
        == "198.51.100.4"
    )


def test_a_trusted_peer_without_forwarded_for_is_the_client() -> None:
    assert client_ip_from_request(_request("10.0.0.3")) == "10.0.0.3"
