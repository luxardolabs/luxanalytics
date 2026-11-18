"""Marks for HMAC-signed requests that were stored, so a replay inside the timestamp window is
recognised.

The signature covers the body and the timestamp, so the same signed request verifies until the
timestamp leaves the window. Once a request has been stored its signature is marked in Redis for as
long as it can verify; a request whose signature is marked is a replay. The mark is written after
the events are committed, so a request that failed before that leaves none and its retry is stored.
The worst case is a duplicate row (a crash between the commit and the mark, or two identical
requests in flight together), never a lost event. Redis being unreachable, not configured, or not
answering within REDIS_WAIT_SECONDS (a slow Redis or a busy worker) fails open on both the read and
the write: every request is processed, as the rate limiter does.

A command that outlives the wait is not cancelled: cancelling one closes its connection, and the
next command pays the reconnect. It finishes on its own (the client's read timeout bounds it) and
its connection goes back to the pool. At most MAX_COMMANDS_IN_FLIGHT run at once; past that a
check or mark fails open without sending anything.
A slow reply logs one warning, without a traceback, per OUTAGE_LOG_INTERVAL; any other Redis
failure logs its traceback."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import settings
from app.core.redis_client import LogThrottle, get_redis_client

logger = logging.getLogger(__name__)

# How long the check and the mark each wait on Redis before failing open. A hung Redis then costs
# a signed request this much here, rather than holding it until the command answers.
REDIS_WAIT_SECONDS = 0.25

# When a slow reply was last logged: once per interval, not per signed request.
slow_replies = LogThrottle()

# Commands in flight, held until they finish (the loop keeps only weak references to tasks, and a
# collected task would never return its connection).
_unanswered: set[asyncio.Task[object]] = set()

# At most this many replay commands in flight at once: past it, a check or mark fails open at once
# and sends nothing. A slow Redis then holds no more than these of the 50 pool connections the
# rate limits share, however many signed requests arrive while it is slow.
MAX_COMMANDS_IN_FLIGHT = 8


def _key(key_id: str, signature: str) -> str:
    """The key id is part of the mark: two key ids may share a secret, and one signed body then
    verifies under both."""
    return f"hmac:sig:{key_id}:{signature}"


def _ttl_seconds() -> int:
    """A timestamp is accepted from the tolerance ahead of the clock until the tolerance behind it,
    so a signature can verify for twice the tolerance."""
    return 2 * settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE + 1


def _finished(task: asyncio.Task[object]) -> None:
    _unanswered.discard(task)
    if not task.cancelled():
        # Retrieved, so a late failure is not reported again as never retrieved: the request it
        # served already failed open, and the slow reply was logged.
        task.exception()


async def _within_wait(command: Callable[[], Awaitable[object]]) -> tuple[bool, object]:
    """(True, the command's result) when it answers within REDIS_WAIT_SECONDS; (False, None) when
    it does not, and it is left to finish, or when MAX_COMMANDS_IN_FLIGHT are already in flight,
    and it is not sent. Its RedisError or OSError raises here."""
    loop = asyncio.get_running_loop()
    if sum(task.get_loop() is loop for task in _unanswered) >= MAX_COMMANDS_IN_FLIGHT:
        return False, None
    task: asyncio.Task[object] = asyncio.ensure_future(command())
    _unanswered.add(task)
    task.add_done_callback(_finished)
    done, _pending = await asyncio.wait({task}, timeout=REDIS_WAIT_SECONDS)
    if task not in done:
        return False, None
    return True, task.result()


def _slow(message: str) -> None:
    if slow_replies.due():
        logger.warning(message)


async def _exists(key: str) -> bool:
    redis_client = await get_redis_client()
    if redis_client is None:
        return False
    return bool(await redis_client.exists(key))


async def _set(key: str) -> None:
    redis_client = await get_redis_client()
    if redis_client is None:
        return
    await redis_client.set(key, "1", ex=_ttl_seconds())


async def signature_is_marked(key_id: str, signature: str) -> bool:
    """True when a request with this signature was already stored inside its window."""
    try:
        answered, marked = await _within_wait(lambda: _exists(_key(key_id, signature)))
    except RedisTimeoutError, TimeoutError:
        answered, marked = False, False
    except RedisError, OSError:
        # swallowed-exceptions: handled, not dropped: an unreachable Redis fails open (logged).
        logger.exception("HMAC replay check failed")
        return False
    if not answered:
        _slow("HMAC replay check: Redis did not answer in time, failing open")
        return False
    return marked is True


async def mark_signature(key_id: str, signature: str) -> None:
    """Remember that the request with this signature was stored."""
    try:
        answered, _ = await _within_wait(lambda: _set(_key(key_id, signature)))
    except RedisTimeoutError, TimeoutError:
        answered = False
    except RedisError, OSError:
        # swallowed-exceptions: handled, not dropped: the request was stored; its replay will be too.
        logger.exception("HMAC replay mark failed")
        return
    if not answered:
        _slow("HMAC replay mark: Redis did not answer in time, failing open")
