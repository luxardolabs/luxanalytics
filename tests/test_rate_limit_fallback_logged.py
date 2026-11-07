"""A failed request limit check falls back to memory, serves the request and logs at ERROR."""

import logging

import pytest
import redis.asyncio as redis
from httpx import AsyncClient

import app.core.rate_limiter as rate_limiter
from app.core.config import settings
from app.core.rate_limiter import IPRateLimiter
from app.core.redis_client import get_redis_client

RATE_LIMIT_CHECK_FAILED = "Redis rate limit check failed"
RATE_LIMIT_RECOVERED = "Redis rate limit check recovered"


async def test_a_redis_outage_is_logged_and_the_request_still_runs(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    fresh_outage: None,
) -> None:
    unreachable = redis.from_url("redis://127.0.0.1:1/0")

    async def get_unreachable_client() -> redis.Redis:
        return unreachable

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_unreachable_client)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", 2)

    with caplog.at_level(logging.ERROR):
        codes = [(await client.get("/login")).status_code for _ in range(3)]
    await unreachable.aclose()

    assert codes == [200, 200, 429]
    failures = [
        record
        for record in caplog.records
        if record.getMessage() == "Redis rate limit check failed"
    ]
    assert {record.levelno for record in failures} == {logging.ERROR}
    assert failures[0].exc_info is not None


async def test_an_unexpected_limit_failure_is_logged_and_the_request_still_runs(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fails(self: IPRateLimiter, client_ip: str) -> None:
        raise RuntimeError("the limiter has a bug")

    monkeypatch.setattr(IPRateLimiter, "check_ip_limit", fails)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", 2)

    with caplog.at_level(logging.ERROR):
        codes = [(await client.get("/login")).status_code for _ in range(3)]

    assert codes == [200, 200, 429]
    fallback = [r for r in caplog.records if "in-memory fallback" in r.getMessage()]
    assert {record.levelno for record in fallback} == {logging.ERROR}
    assert fallback[0].exc_info is not None


@pytest.fixture
def fresh_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """No outage in progress when the test starts, whatever an earlier test left."""
    monkeypatch.setattr(rate_limiter, "redis_outage", rate_limiter.RedisOutage())


async def test_a_redis_outage_logs_its_traceback_once_and_its_recovery(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    fresh_outage: None,
) -> None:
    unreachable = redis.from_url("redis://127.0.0.1:1/0")
    reachable = await get_redis_client()
    assert reachable is not None
    current = unreachable

    async def get_current_client() -> redis.Redis:
        return current

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_current_client)

    with caplog.at_level(logging.INFO, logger="app.core.rate_limiter"):
        for _ in range(4):
            assert (await client.get("/login")).status_code == 200
        current = reachable
        assert (await client.get("/login")).status_code == 200
        assert (await client.get("/login")).status_code == 200
    await unreachable.aclose()

    failed = [r for r in caplog.records if r.getMessage() == RATE_LIMIT_CHECK_FAILED]
    assert len(failed) == 1
    assert failed[0].levelno == logging.ERROR
    assert failed[0].exc_info is not None
    recovered = [r for r in caplog.records if r.getMessage() == RATE_LIMIT_RECOVERED]
    assert len(recovered) == 1
    assert vars(recovered[0])["failed_checks"] == 4


async def test_a_second_redis_outage_logs_its_own_traceback(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    fresh_outage: None,
) -> None:
    unreachable = redis.from_url("redis://127.0.0.1:1/0")
    reachable = await get_redis_client()
    assert reachable is not None
    current = unreachable

    async def get_current_client() -> redis.Redis:
        return current

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_current_client)

    with caplog.at_level(logging.INFO, logger="app.core.rate_limiter"):
        await client.get("/login")
        current = reachable
        await client.get("/login")
        current = unreachable
        await client.get("/login")
    await unreachable.aclose()

    failed = [r for r in caplog.records if r.getMessage() == RATE_LIMIT_CHECK_FAILED]
    assert len(failed) == 2
    assert all(r.exc_info is not None for r in failed)
