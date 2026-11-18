"""A Redis slower than the replay check's wait fails the check open without cancelling the command
(its connection goes back to the pool when the reply arrives) and logs one warning, without a
traceback, per interval rather than per request."""

import asyncio
import logging
import time
from collections.abc import Iterator

import pytest
import redis.asyncio as redis
from httpx import ASGITransport, AsyncClient
from redis.asyncio import BlockingConnectionPool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.hmac_replay as hmac_replay
import app.core.rate_limiter as rate_limiter
import app.core.redis_client as redis_client
import app.core.security as security
from app.core.config import settings
from app.core.redis_client import OUTAGE_LOG_INTERVAL
from app.main import create_application
from app.models.app_model import App
from app.models.event_model import Event
from tests.redis_proxy import StallingProxy, stalling_proxy
from tests.test_app_rate_limit import _event, _signed

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

    async def mark(self, key: str, ex: int) -> None:
        await self._answer(1)


@pytest.fixture
def slow(monkeypatch: pytest.MonkeyPatch) -> _SlowRedis:
    client = _SlowRedis()

    async def get_slow() -> _SlowRedis:
        return client

    monkeypatch.setattr(hmac_replay, "get_replay_redis", get_slow)
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


@pytest.mark.db
async def test_a_redis_refusing_connections_logs_at_most_one_replay_traceback(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    refusing = redis.from_url("redis://127.0.0.1:1/0")
    replay_redis = redis_client.ReplayRedis(check=refusing, mark=refusing)

    async def get_refusing() -> redis_client.ReplayRedis:
        return replay_redis

    monkeypatch.setattr(hmac_replay, "get_replay_redis", get_refusing)
    with caplog.at_level(logging.WARNING, logger="app.core.hmac_replay"):
        for i in range(50):
            await hmac_replay.signature_is_marked("k", f"refused-{i}")
            await hmac_replay.mark_signature("k", f"refused-{i}")
    await refusing.aclose()

    tracebacks = [r for r in _replay_logs(caplog) if r.exc_info is not None]
    assert len(tracebacks) <= 1


@pytest.mark.db
async def test_replay_commands_on_a_stalled_redis_hold_only_their_own_small_pool(
    stalled_app_redis: StallingProxy,
) -> None:
    """A burst of checks on a stalled Redis holds at most the replay check's own connections, and
    none of the pool the rate limits use."""
    await redis_client.close_redis_client()
    try:
        limits_client = await redis_client.get_redis_client()
        replay_redis = await redis_client.get_replay_redis()
        assert limits_client is not None and replay_redis is not None
        stalled_app_redis.stall()
        results = await asyncio.gather(
            *(hmac_replay.signature_is_marked("k", f"own-pool-{i}") for i in range(100))
        )
        check_pool = replay_redis.check.connection_pool
        limits_pool = limits_client.connection_pool
        assert isinstance(check_pool, BlockingConnectionPool)
        assert isinstance(limits_pool, BlockingConnectionPool)
        held = len(check_pool._in_use_connections)
        held_from_limits = len(limits_pool._in_use_connections)
        stalled_app_redis.resume()
    finally:
        await asyncio.sleep(1.2)
        await redis_client.close_redis_client()

    assert results == [False] * 100
    assert held <= redis_client.REPLAY_CHECK_CONNECTIONS
    assert held_from_limits == 0


async def _keys(pattern: str) -> int:
    """Marks in Redis, counted on a client of its own (no proxy, no delay)."""
    client = redis.from_url(REAL_REDIS_URL)
    try:
        return len([key async for key in client.scan_iter(pattern)])
    finally:
        await client.aclose()


async def _keys_within(pattern: str, count: int, seconds: float) -> int:
    """The marks matching `pattern` once `count` are there, or after `seconds`."""
    deadline = time.monotonic() + seconds
    found = await _keys(pattern)
    while found < count and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        found = await _keys(pattern)
    return found


REAL_REDIS_URL = settings.REDIS_URL or ""


@pytest.fixture
def slow_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[StallingProxy]:
    """The app's Redis clients through a proxy whose replies the test can slow (under the 2 s read
    timeout: a slow Redis that still answers)."""
    with stalling_proxy(REAL_REDIS_URL) as proxy:
        monkeypatch.setattr(settings, "REDIS_URL", proxy.url)
        yield proxy
        proxy.delay = 0.0


async def _warm() -> None:
    """Connections opened before the delay: the test measures commands, not connects."""
    await redis_client.close_redis_client()
    replay_redis = await redis_client.get_replay_redis()
    assert replay_redis is not None
    await asyncio.gather(*(replay_redis.exists(f"warm:{i}") for i in range(5)))
    await hmac_replay.mark_signature("warm", "warm")


@pytest.mark.db
@pytest.mark.parametrize("count", [5, 40])
async def test_a_burst_of_marks_on_a_slow_but_answering_redis_all_land(
    slow_redis: StallingProxy, count: int
) -> None:
    """Redis answers every command in 1.2 s: 40 marks waiting together are all written."""
    try:
        await _warm()
        slow_redis.delay = 1.2
        await asyncio.gather(
            *(
                hmac_replay.mark_signature("k", f"burst{count}-{i}")
                for i in range(count)
            )
        )
        landed = await _keys_within(f"hmac:sig:k:burst{count}-*", count, 15)
    finally:
        slow_redis.delay = 0.0
        await redis_client.close_redis_client()

    assert landed == count


@pytest.mark.db
async def test_a_request_stored_during_a_burst_of_marks_is_not_replayable_after_recovery(
    slow_redis: StallingProxy,
    db: AsyncSession,
    sample_app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signed request answered on a slow Redis while 35 other marks wait: once Redis is fast
    again, its replay inside the window is recognised."""
    body = _event("burst-recovery")
    headers = _signed(body, sample_app.app_id)
    real_mark = security.mark_signature
    others: list[asyncio.Future[None]] = []

    async def mark_behind_others(key_id: str, signature: str) -> None:
        others.extend(
            asyncio.ensure_future(hmac_replay.mark_signature("other", f"bg-{i}"))
            for i in range(35)
        )
        await asyncio.sleep(0)
        await real_mark(key_id, signature)

    monkeypatch.setattr(security, "mark_signature", mark_behind_others)
    try:
        await _warm()
        async with AsyncClient(
            transport=ASGITransport(
                app=create_application(), raise_app_exceptions=False
            ),
            base_url="https://localhost",
        ) as server:
            slow_redis.delay = 1.2
            first = await server.post("/api/v1/events/", content=body, headers=headers)
            await asyncio.gather(*others)
            await _keys_within(f"hmac:sig:{sample_app.app_id}:*", 1, 15)
            slow_redis.delay = 0.0
            replay = await server.post("/api/v1/events/", content=body, headers=headers)
    finally:
        slow_redis.delay = 0.0
        await redis_client.close_redis_client()

    assert first.json()["events_received"] == 1
    assert replay.json()["message"] == "Duplicate request ignored"
    stored = await db.execute(
        select(func.count()).select_from(Event).where(Event.app_id == sample_app.app_id)
    )
    assert stored.scalar_one() == 1
