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

The check and the mark use their own small connection pools (app.core.redis_client.ReplayRedis),
apart from the rate limits', so a slow Redis cannot make a limit check wait behind them. The check
waits REDIS_WAIT_SECONDS, a free connection included, then fails open; a command that outlives the
wait is not cancelled (cancelling one closes its connection) and finishes on its own, bounded by the
client's read timeout. A mark is never dropped for want of a connection: it runs as a task that
waits for its pool as long as the pool allows, and the request waits on it no longer than
REDIS_WAIT_SECONDS. A slow reply logs one warning, without a traceback, per OUTAGE_LOG_INTERVAL;
any other Redis failure logs one traceback per OUTAGE_LOG_INTERVAL."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import settings
from app.core.redis_client import REPLAY_WAIT_SECONDS, LogThrottle, get_replay_redis

logger = logging.getLogger(__name__)

# How long the check, and the request on its mark, wait on Redis before failing open. A hung
# Redis then costs a signed request this much here, rather than holding it until Redis answers.
REDIS_WAIT_SECONDS = REPLAY_WAIT_SECONDS

# When a slow reply, and any other Redis failure, was last logged: each once per interval, not per
# signed request.
slow_replies = LogThrottle()
failures = LogThrottle()

# Commands in flight, held until they finish (the loop keeps only weak references to tasks, and a
# collected task would never write its mark or return its connection).
_unanswered: set[asyncio.Task[object]] = set()


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


def _mark_finished(task: asyncio.Task[object]) -> None:
    """A mark's outcome, whenever it comes: a failure is logged here (throttled), since the request
    it belongs to may already have been answered."""
    _unanswered.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if isinstance(error, (RedisTimeoutError, TimeoutError)):
        _slow("HMAC replay mark: Redis did not answer in time")
    elif error is not None and failures.due():
        logger.error("HMAC replay mark failed", exc_info=error)


def _started(
    command: Awaitable[object], done: Callable[[asyncio.Task[object]], None]
) -> asyncio.Task[object]:
    task: asyncio.Task[object] = asyncio.ensure_future(command)
    _unanswered.add(task)
    task.add_done_callback(done)
    return task


async def _within_wait(command: Awaitable[object]) -> tuple[bool, object]:
    """(True, the command's result) when it answers within REDIS_WAIT_SECONDS; (False, None) when
    it does not, and it is left to finish. Its RedisError or OSError raises here."""
    task = _started(command, _finished)
    done, _pending = await asyncio.wait({task}, timeout=REDIS_WAIT_SECONDS)
    if task not in done:
        return False, None
    return True, task.result()


def _slow(message: str) -> None:
    if slow_replies.due():
        logger.warning(message)


async def _exists(key: str) -> bool:
    replay_redis = await get_replay_redis()
    if replay_redis is None:
        return False
    return bool(await replay_redis.exists(key))


async def _set(key: str) -> None:
    replay_redis = await get_replay_redis()
    if replay_redis is None:
        return
    await replay_redis.set(key, "1", ex=_ttl_seconds())


async def signature_is_marked(key_id: str, signature: str) -> bool:
    """True when a request with this signature was already stored inside its window."""
    try:
        answered, marked = await _within_wait(_exists(_key(key_id, signature)))
    except RedisTimeoutError, TimeoutError:
        answered, marked = False, False
    except RedisError, OSError:
        # swallowed-exceptions: handled, not dropped: an unreachable Redis fails open (logged).
        if failures.due():
            logger.exception("HMAC replay check failed")
        return False
    if not answered:
        _slow("HMAC replay check: Redis did not answer in time, failing open")
        return False
    return marked is True


async def mark_signature(key_id: str, signature: str) -> None:
    """Remember that the request with this signature was stored. The mark is written by a task
    that outlives this call when Redis is slow; its failure is logged when it ends."""
    task = _started(_set(_key(key_id, signature)), _mark_finished)
    await asyncio.wait({task}, timeout=REDIS_WAIT_SECONDS)
