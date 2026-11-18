"""A limit check whose script reached Redis but whose reply timed out has spent its budget there: it
is admitted, not checked again in the in-memory window. A check that never reached Redis (no
connection) still falls back to that window."""

from types import TracebackType
from typing import Self

import pytest
import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

import app.core.rate_limiter as rate_limiter
from app.core.redis_client import get_redis_client

pytestmark = pytest.mark.db


class _RepliesLost:
    """Runs each script on the real Redis, then loses its reply: `fault` is raised instead."""

    def __init__(self, real: redis.Redis, fault: Exception) -> None:
        self.real = real
        self.fault = fault
        self.scripts_run = 0

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

    async def eval(
        self, script: str, numkeys: int, *keys_and_args: str | int | float
    ) -> list[object]:
        await self.real.eval(script, numkeys, *keys_and_args)
        self.scripts_run += 1
        raise self.fault


class _NoConnection:
    """Cannot connect: the script is never sent."""

    def __init__(self) -> None:
        self.attempts = 0

    def client(self) -> Self:
        return self

    async def __aenter__(self) -> Self:
        self.attempts += 1
        raise RedisConnectionError("Error 111 connecting to redis:6379. Refused.")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def eval(self, *args: str | int | float) -> list[object]:
        self.attempts += 1
        raise RedisConnectionError("Error 111 connecting to redis:6379. Refused.")


@pytest.fixture(autouse=True)
def _fresh_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limiter, "redis_outage", rate_limiter.RedisOutage())


@pytest.mark.parametrize(
    "fault",
    [
        RedisTimeoutError("Timeout reading from redis:6379"),
        TimeoutError(),
    ],
)
async def test_a_reply_timeout_after_the_script_ran_is_admitted_without_a_memory_spend(
    monkeypatch: pytest.MonkeyPatch, fault: Exception
) -> None:
    real = await get_redis_client()
    assert real is not None
    lossy = _RepliesLost(real, fault)

    async def get_lossy() -> _RepliesLost:
        return lossy

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_lossy)
    limiter = rate_limiter.RateLimiter(5, 60, key_prefix="reply_timeout")

    refused, _metadata, spent = await rate_limiter.admit(
        [rate_limiter.Budget(limiter, "client-a", 2)]
    )

    assert refused is None
    assert lossy.scripts_run == 1
    # Spent once, in Redis, where the script ran: the memory window holds nothing.
    assert await real.zcard("reply_timeout:client-a") == 2
    assert limiter._memory_storage == {}
    assert spent.memory_at is None
    # A refund can still take back what the script added.
    assert spent.redis_member is not None
    await rate_limiter.refund(spent)
    assert await real.zcard("reply_timeout:client-a") == 0


async def test_a_reply_timeout_is_counted_as_a_failed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = await get_redis_client()
    assert real is not None
    lossy = _RepliesLost(real, RedisTimeoutError("Timeout reading from redis:6379"))

    async def get_lossy() -> _RepliesLost:
        return lossy

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_lossy)
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
    assert sum(cost for _, cost in limiter._memory_storage["client-c"]) == 3
