"""A Redis slower than the replay check's wait fails the check open without cancelling the command
(its connection goes back to the pool when the reply arrives) and logs one warning, without a
traceback, per interval rather than per request."""

import asyncio
import logging
import time
from collections.abc import Iterator

import pytest

import app.core.hmac_replay as hmac_replay
import app.core.rate_limiter as rate_limiter
import app.core.redis_client as redis_client
from app.core.config import settings
from app.core.redis_client import OUTAGE_LOG_INTERVAL
from tests.redis_proxy import StallingProxy, stalling_proxy

SLOW = hmac_replay.REDIS_WAIT_SECONDS + 0.05


class _SlowRedis:
    """Answers each command after SLOW seconds, recording how each one ended."""

    def __init__(self) -> None:
        self.answered = 0
        self.cancelled = 0

    async def _answer(self, value: int) -> int:
        try:
            await asyncio.sleep(SLOW)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.answered += 1
        return value

    async def exists(self, key: str) -> int:
        return await self._answer(1)

    async def set(self, key: str, value: str, ex: int) -> int:
        return await self._answer(1)


@pytest.fixture
def slow(monkeypatch: pytest.MonkeyPatch) -> _SlowRedis:
    client = _SlowRedis()

    async def get_slow() -> _SlowRedis:
        return client

    monkeypatch.setattr(hmac_replay, "get_redis_client", get_slow)
    return client


@pytest.fixture
def fresh_log(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """No slow reply logged yet, and a clock the test sets (the list's one number, in seconds)."""
    now = [1000.0]
    monkeypatch.setattr(hmac_replay.slow_replies, "clock", lambda: now[0])
    monkeypatch.setattr(hmac_replay.slow_replies, "last_logged", None)
    return now


def _replay_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "app.core.hmac_replay"]


async def test_a_slow_reply_fails_open_and_the_command_is_not_cancelled(
    slow: _SlowRedis,
) -> None:
    # Fails open: a slow Redis is treated as no mark.
    assert await hmac_replay.signature_is_marked("k", "sig") is False
    await hmac_replay.mark_signature("k", "sig")
    await asyncio.sleep(SLOW + 0.1)

    assert slow.cancelled == 0
    assert slow.answered == 2


async def test_slow_replies_log_at_most_one_line_without_a_traceback(
    slow: _SlowRedis, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.core.hmac_replay"):
        for i in range(3):
            await hmac_replay.signature_is_marked("k", f"sig-{i}")
            await hmac_replay.mark_signature("k", f"sig-{i}")
        await asyncio.sleep(SLOW + 0.1)

    logged = _replay_logs(caplog)
    assert len(logged) <= 1
    assert all(r.exc_info is None for r in logged)


async def test_slow_replies_log_again_once_the_interval_has_passed(
    slow: _SlowRedis, caplog: pytest.LogCaptureFixture, fresh_log: list[float]
) -> None:
    now = fresh_log
    with caplog.at_level(logging.INFO, logger="app.core.hmac_replay"):
        await hmac_replay.signature_is_marked("k", "a")
        await hmac_replay.signature_is_marked("k", "b")
        now[0] += OUTAGE_LOG_INTERVAL
        await hmac_replay.signature_is_marked("k", "c")
        await asyncio.sleep(SLOW + 0.1)

    logged = _replay_logs(caplog)
    assert len(logged) == 2
    assert {r.levelno for r in logged} == {logging.WARNING}
    assert all(r.exc_info is None for r in logged)


class _Counting:
    """Answers after SLOW seconds, counting the commands it was sent and the most at once."""

    def __init__(self) -> None:
        self.sent = 0
        self.running = 0
        self.most_at_once = 0

    async def exists(self, key: str) -> int:
        self.sent += 1
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        try:
            await asyncio.sleep(SLOW)
        finally:
            self.running -= 1
        return 0


async def test_replay_commands_in_flight_are_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the cap a check fails open at once and sends nothing, so a slow Redis cannot fill the
    pool the rate limits share."""
    counting = _Counting()

    async def get_counting() -> _Counting:
        return counting

    monkeypatch.setattr(hmac_replay, "get_redis_client", get_counting)

    results = await asyncio.gather(
        *(hmac_replay.signature_is_marked("k", f"burst-{i}") for i in range(100))
    )
    await asyncio.sleep(SLOW + 0.1)

    assert results == [False] * 100
    # A handful, a small share of the 50-connection pool, never one per request.
    assert counting.sent <= 16
    assert counting.most_at_once <= hmac_replay.MAX_COMMANDS_IN_FLIGHT


@pytest.fixture
def stalled_app_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[StallingProxy]:
    """The app's own Redis client, through a proxy the test stalls, with a 1 s reply timeout."""
    with stalling_proxy(settings.REDIS_URL or "") as proxy:
        monkeypatch.setattr(redis_client, "REDIS_SOCKET_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(settings, "REDIS_URL", proxy.url)
        monkeypatch.setattr(rate_limiter, "redis_outage", rate_limiter.RedisOutage())
        yield proxy


@pytest.mark.db
async def test_a_limit_check_beside_a_replay_burst_on_a_stalled_redis_takes_its_own_timeout(
    stalled_app_redis: StallingProxy,
) -> None:
    await redis_client.close_redis_client()
    try:
        assert await redis_client.get_redis_client() is not None
        stalled_app_redis.stall()
        await asyncio.gather(
            *(hmac_replay.signature_is_marked("k", f"stall-{i}") for i in range(300))
        )
        limiter = rate_limiter.RateLimiter(1000, 60, key_prefix="beside_replays")
        started = time.monotonic()
        await rate_limiter.admit([rate_limiter.Budget(limiter, "y", 1)])
        took = time.monotonic() - started
        stalled_app_redis.resume()
    finally:
        await asyncio.sleep(1.2)
        await redis_client.close_redis_client()

    # Its own 1 s reply timeout, not a wait behind replay commands for a pool connection.
    assert took < 1.6
