"""A limit check whose script was sent but whose reply timed out is decided by the in-memory window:
admitted, and spent there, only when that window has room. The script may have spent in Redis too,
so what it returns carries both spends and a refund gives back both. A check that never reached
Redis (no connection) falls back to the same window."""

from types import TracebackType
from typing import Self

import pytest
import redis.asyncio as redis
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

import app.core.rate_limiter as rate_limiter
from app.core.config import settings
from app.core.redis_client import get_redis_client

pytestmark = pytest.mark.db


class _Session:
    """The one-connection session a check runs in: the stub itself."""

    def client(self) -> Self:
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class _RepliesLost(_Session):
    """Runs each script on the real Redis; while `losing`, its reply is lost and `fault` raised."""

    def __init__(self, real: redis.Redis, fault: Exception) -> None:
        self.real = real
        self.fault = fault
        self.losing = True
        self.scripts_run = 0

    async def eval(
        self, script: str, numkeys: int, *keys_and_args: str | int | float
    ) -> list[object]:
        reply: list[object] = await self.real.eval(script, numkeys, *keys_and_args)
        self.scripts_run += 1
        if self.losing:
            raise self.fault
        return reply


class _NeverAnswers(_Session):
    """Connects, takes every script, and times out on every reply."""

    async def eval(
        self, script: str, numkeys: int, *keys_and_args: str | int | float
    ) -> list[object]:
        raise RedisTimeoutError("Timeout reading from redis:6379")


class _NoConnection(_Session):
    """Cannot connect: the script is never sent."""

    def __init__(self) -> None:
        self.attempts = 0

    async def __aenter__(self) -> Self:
        self.attempts += 1
        raise RedisConnectionError("Error 111 connecting to redis:6379. Refused.")

    async def eval(self, *args: str | int | float) -> list[object]:
        self.attempts += 1
        raise RedisConnectionError("Error 111 connecting to redis:6379. Refused.")


@pytest.fixture(autouse=True)
def _fresh_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limiter, "redis_outage", rate_limiter.RedisOutage())


async def _lossy(monkeypatch: pytest.MonkeyPatch, fault: Exception) -> _RepliesLost:
    real = await get_redis_client()
    assert real is not None
    lossy = _RepliesLost(real, fault)

    async def get_lossy() -> _RepliesLost:
        return lossy

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_lossy)
    return lossy


def _memory_spent(limiter: rate_limiter.RateLimiter, identifier: str) -> int:
    return sum(cost for _, cost in limiter._memory_storage.get(identifier, []))


@pytest.mark.parametrize(
    "fault", [RedisTimeoutError("Timeout reading from redis:6379"), TimeoutError()]
)
async def test_a_reply_timeout_is_decided_and_spent_in_the_memory_window(
    monkeypatch: pytest.MonkeyPatch, fault: Exception
) -> None:
    lossy = await _lossy(monkeypatch, fault)
    limiter = rate_limiter.RateLimiter(5, 60, key_prefix="reply_timeout")

    refused, _metadata, spent = await rate_limiter.admit(
        [rate_limiter.Budget(limiter, "client-a", 2)]
    )

    assert refused is None
    assert lossy.scripts_run == 1
    assert await lossy.real.zcard("reply_timeout:client-a") == 2
    assert _memory_spent(limiter, "client-a") == 2
    assert spent.redis_member is not None
    assert spent.memory_at is not None


async def test_stalls_between_working_checks_admit_an_over_budget_client_only_within_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client over its Redis budget, during stall-then-recover cycles, gets no more than the
    memory window's own limit: a lost reply is not a free admission."""
    lossy = await _lossy(monkeypatch, RedisTimeoutError("Timeout reading from redis"))
    limiter = rate_limiter.RateLimiter(2, 60, key_prefix="reply_timeout_cycles")
    budget = [rate_limiter.Budget(limiter, "client-x", 1)]
    lossy.losing = False
    assert [(await rate_limiter.admit(budget))[0] for _ in range(3)] == [None, None, 0]

    admitted_during_stalls = 0
    for _ in range(5):
        lossy.losing = True
        if (await rate_limiter.admit(budget))[0] is None:
            admitted_during_stalls += 1
        lossy.losing = False
        assert (await rate_limiter.admit(budget))[0] == 0

    assert admitted_during_stalls == 2


async def test_a_reply_timeout_is_counted_as_a_failed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _lossy(monkeypatch, RedisTimeoutError("Timeout reading from redis:6379"))
    limiter = rate_limiter.RateLimiter(100, 60, key_prefix="reply_timeout_count")
    for _ in range(3):
        await rate_limiter.admit([rate_limiter.Budget(limiter, "client-b", 1)])

    assert rate_limiter.redis_outage.failed_checks == 3


async def test_a_connection_failure_before_the_script_is_sent_uses_the_memory_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unreachable = _NoConnection()

    async def get_unreachable() -> _NoConnection:
        return unreachable

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_unreachable)
    limiter = rate_limiter.RateLimiter(3, 60, key_prefix="no_connection")
    budget = rate_limiter.Budget(limiter, "client-c", 1)

    outcomes = [(await rate_limiter.admit([budget]))[0] for _ in range(4)]

    assert outcomes == [None, None, None, 0]
    assert unreachable.attempts == 4
    assert _memory_spent(limiter, "client-c") == 3


async def test_a_redis_that_keeps_timing_out_is_held_to_the_memory_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hung = _NeverAnswers()

    async def get_hung() -> _NeverAnswers:
        return hung

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_hung)
    limiter = rate_limiter.RateLimiter(3, 60, key_prefix="hung_redis")
    budget = rate_limiter.Budget(limiter, "client-d", 1)

    outcomes = [(await rate_limiter.admit([budget]))[0] for _ in range(6)]

    assert outcomes == [None, None, None, 0, 0, 0]
    assert _memory_spent(limiter, "client-d") == 3


async def test_a_redis_that_keeps_timing_out_still_refuses_past_the_request_limit(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    hung = _NeverAnswers()

    async def get_hung() -> _NeverAnswers:
        return hung

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_hung)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", 2)
    headers = {"X-Forwarded-For": "203.0.113.200"}

    codes = [
        (await client.get("/login", headers=headers)).status_code for _ in range(5)
    ]

    assert codes == [200, 200, 429, 429, 429]


async def test_a_refund_after_a_reply_timeout_gives_back_redis_and_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lossy = await _lossy(monkeypatch, RedisTimeoutError("Timeout reading from redis"))
    # Redis already failing: the check that times out now is the one that falls back.
    rate_limiter.redis_outage.failed_checks = 1
    limiter = rate_limiter.RateLimiter(100, 60, key_prefix="reply_timeout_refund")

    refused, _metadata, spent = await rate_limiter.admit(
        [rate_limiter.Budget(limiter, "client-r", 2)]
    )
    assert refused is None
    assert await lossy.real.zcard("reply_timeout_refund:client-r") == 2
    assert _memory_spent(limiter, "client-r") == 2

    await rate_limiter.refund(spent)

    assert await lossy.real.zcard("reply_timeout_refund:client-r") == 0
    assert _memory_spent(limiter, "client-r") == 0
