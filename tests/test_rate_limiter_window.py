"""The sliding-window limiter admits at most its limit, however many requests arrive at once.

The check (ZREMRANGEBYSCORE + ZCARD) and the add (ZADD) were two round trips, so every request of a
concurrent burst read the same count and all were admitted; the member `f"{now}:{cost}"` also made
two requests at the same timestamp count once (adversarial audit 2026-10-27, LUXANALYTI-100). The IP
limit is DSN ingest's only limit, so this is the bound on anyone with the public DSN.
"""

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

import app.core.rate_limiter as rate_limiter
from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.main import create_application

LIMIT = 5


@pytest.fixture
def limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", LIMIT)


async def test_a_concurrent_burst_is_admitted_up_to_the_limit(limited: None) -> None:
    # The Redis path, not the in-memory fallback.
    assert await get_redis_client() is not None
    limiter = rate_limiter.IPRateLimiter()

    results = await asyncio.gather(
        *(limiter.check_ip_limit("192.0.2.10") for _ in range(30))
    )

    assert sum(allowed for allowed, _meta in results) == LIMIT


async def test_requests_at_the_same_instant_each_count(
    limited: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)
    limiter = rate_limiter.IPRateLimiter()

    results = [await limiter.check_ip_limit("192.0.2.11") for _ in range(LIMIT + 2)]

    assert [allowed for allowed, _meta in results] == [True] * LIMIT + [False, False]
    refused = results[-1][1]
    assert refused["remaining"] == 0
    assert refused["retry_after"] >= 1


async def test_one_ips_concurrent_http_burst_is_limited(limited: None) -> None:
    """End to end on the middleware's IP limit (GET /login touches no database)."""
    async with AsyncClient(
        transport=ASGITransport(app=create_application()), base_url="https://localhost"
    ) as ac:

        async def one() -> int:
            r = await ac.get("/login", headers={"X-Forwarded-For": "192.0.2.77"})
            return r.status_code

        codes = await asyncio.gather(*(one() for _ in range(20)))

    assert codes.count(200) == LIMIT
    assert codes.count(429) == 20 - LIMIT
