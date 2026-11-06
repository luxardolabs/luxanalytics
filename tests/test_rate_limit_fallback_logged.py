"""A Redis failure in the request limit falls back to memory, at a level production keeps."""

import logging

import pytest
from httpx import AsyncClient

from app.core.rate_limiter import IPRateLimiter


async def test_a_redis_failure_is_logged_and_the_request_still_runs(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fails(self: IPRateLimiter, client_ip: str) -> None:
        raise ConnectionError("redis is down")

    monkeypatch.setattr(IPRateLimiter, "check_ip_limit", fails)

    with caplog.at_level(logging.WARNING):
        r = await client.get("/login")

    assert r.status_code == 200
    fallback = [r for r in caplog.records if "in-memory fallback" in r.getMessage()]
    assert [record.levelno for record in fallback] == [logging.WARNING]
    assert fallback[0].exc_info is not None
