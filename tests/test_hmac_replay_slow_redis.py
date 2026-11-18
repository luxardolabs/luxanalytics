"""A Redis slower than the replay check's wait fails the check open without cancelling the command
(its connection goes back to the pool when the reply arrives) and logs one warning, without a
traceback, per interval rather than per request."""

import asyncio
import logging

import pytest

import app.core.hmac_replay as hmac_replay
from app.core.redis_client import OUTAGE_LOG_INTERVAL

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
