"""A failed request limit check falls back to memory, serves the request and logs at ERROR."""

import logging

import pytest
import redis.asyncio as redis
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError as RedisResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

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
    fresh_outage: list[float],
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
def fresh_outage(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """No outage in progress when the test starts, whatever an earlier test left, and a clock the
    test sets (the returned list's one number, in seconds)."""
    now = [1000.0]
    outage = rate_limiter.RedisOutage()
    outage.clock = lambda: now[0]
    monkeypatch.setattr(rate_limiter, "redis_outage", outage)
    return now


async def test_a_redis_outage_logs_its_traceback_once_and_its_recovery(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    fresh_outage: list[float],
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
    fresh_outage: list[float],
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
        fresh_outage[0] += rate_limiter.OUTAGE_LOG_INTERVAL
        current = unreachable
        await client.get("/login")
    await unreachable.aclose()

    failed = [r for r in caplog.records if r.getMessage() == RATE_LIMIT_CHECK_FAILED]
    assert len(failed) == 2
    assert all(r.exc_info is not None for r in failed)


class _Client:
    """A Redis client whose script call raises each of `faults` in turn (None: it works)."""

    def __init__(self, faults: list[Exception | None]) -> None:
        self.faults = faults
        self.calls = 0

    async def eval(self, *args: object) -> list[object]:
        fault = self.faults[self.calls % len(self.faults)]
        self.calls += 1
        if fault is not None:
            raise fault
        return [1, 0, 1, ""]


async def _check_with(
    monkeypatch: pytest.MonkeyPatch, faults: list[Exception | None], checks: int
) -> None:
    client = _Client(faults)

    async def get_client() -> _Client:
        return client

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_client)
    limiter = rate_limiter.RateLimiter(10**6, 60, key_prefix="outage_logging")
    for _ in range(checks):
        await rate_limiter.admit([rate_limiter.Budget(limiter, "a", 1)])


def _tracebacks(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == RATE_LIMIT_CHECK_FAILED]


def _recoveries(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == RATE_LIMIT_RECOVERED]


async def test_a_solid_outage_logs_one_traceback_per_interval(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    fresh_outage: list[float],
) -> None:
    fault = RedisConnectionError("No connection available.")
    with caplog.at_level(logging.INFO, logger="app.core.rate_limiter"):
        await _check_with(monkeypatch, [fault], 500)
        assert len(_tracebacks(caplog)) == 1
        fresh_outage[0] += rate_limiter.OUTAGE_LOG_INTERVAL
        await _check_with(monkeypatch, [fault], 500)

    assert len(_tracebacks(caplog)) == 2
    assert not _recoveries(caplog)


async def test_an_intermittent_redis_logs_a_traceback_per_interval_not_per_flap(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    fresh_outage: list[float],
) -> None:
    fault = RedisTimeoutError("Timeout reading from the socket")
    with caplog.at_level(logging.INFO, logger="app.core.rate_limiter"):
        await _check_with(monkeypatch, [None, fault], 1000)

    assert len(_tracebacks(caplog)) == 1
    assert len(_recoveries(caplog)) == 1  # the success after the one logged failure


async def test_a_recovery_is_logged_only_after_an_outage_that_logged(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    fresh_outage: list[float],
) -> None:
    fault = RedisConnectionError("No connection available.")
    with caplog.at_level(logging.INFO, logger="app.core.rate_limiter"):
        await _check_with(monkeypatch, [fault, None, fault, None, fault, None], 6)

    assert len(_tracebacks(caplog)) == 1
    assert len(_recoveries(caplog)) == 1


async def test_a_different_fault_after_an_outage_is_logged_when_the_interval_has_passed(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    fresh_outage: list[float],
) -> None:
    """A Redis that came back read-only keeps the limiter on per-process memory: that is logged
    even though the connection outage before it already was."""
    refused = RedisConnectionError("No connection available.")
    readonly = RedisResponseError(
        "READONLY You can't write against a read only replica."
    )
    with caplog.at_level(logging.INFO, logger="app.core.rate_limiter"):
        await _check_with(monkeypatch, [refused], 3)
        fresh_outage[0] += rate_limiter.OUTAGE_LOG_INTERVAL
        await _check_with(monkeypatch, [readonly], 3)

    logged = _tracebacks(caplog)
    assert len(logged) == 2
    assert logged[1].exc_info is not None
    assert "READONLY" in str(logged[1].exc_info[1])
    assert not _recoveries(caplog)


async def test_a_refund_that_fails_logs_its_traceback(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    spent = await rate_limiter.enforce_ingest_limits(
        "app_refund_tb", "10.9.9.9", {"d": 1}
    )
    client = _Client([RedisConnectionError("No connection available.")])

    async def get_client() -> _Client:
        return client

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_client)
    with caplog.at_level(logging.WARNING, logger="app.core.rate_limiter"):
        await rate_limiter.refund(spent)

    failed = [
        r for r in caplog.records if r.getMessage() == "Redis rate limit refund failed"
    ]
    assert len(failed) == 1
    assert failed[0].exc_info is not None
