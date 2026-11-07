"""Marks for HMAC-signed requests that were stored, so a replay inside the timestamp window is
recognised.

The signature covers the body and the timestamp, so the same signed request verifies until the
timestamp leaves the window. Once a request has been stored its signature is marked in Redis for as
long as it can verify; a request whose signature is marked is a replay. The mark is written after
the events are committed, so a request that failed before that leaves none and its retry is stored.
The worst case is a duplicate row (a crash between the commit and the mark, or two identical
requests in flight together), never a lost event. Redis being unreachable, not configured, or not
answering within REDIS_WAIT_SECONDS (a slow Redis or a busy worker) fails open on both the read and
the write: every request is processed, as the rate limiter does."""

import asyncio
import logging

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# How long the check and the mark each wait on Redis. A hung Redis then fails open at this cost per
# signed request, rather than the client's 5 s, and asyncio.timeout's TimeoutError is an OSError.
REDIS_WAIT_SECONDS = 0.25


def _key(key_id: str, signature: str) -> str:
    """The key id is part of the mark: two key ids may share a secret, and one signed body then
    verifies under both."""
    return f"hmac:sig:{key_id}:{signature}"


def _ttl_seconds() -> int:
    """A timestamp is accepted from the tolerance ahead of the clock until the tolerance behind it,
    so a signature can verify for twice the tolerance."""
    return 2 * settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE + 1


async def signature_is_marked(key_id: str, signature: str) -> bool:
    """True when a request with this signature was already stored inside its window."""
    try:
        async with asyncio.timeout(REDIS_WAIT_SECONDS):
            redis_client = await get_redis_client()
            if redis_client is None:
                return False
            return bool(await redis_client.exists(_key(key_id, signature)))
    except RedisError, OSError:
        # swallowed-exceptions: handled, not dropped: an unreachable Redis fails open (logged).
        logger.exception("HMAC replay check failed")
        return False


async def mark_signature(key_id: str, signature: str) -> None:
    """Remember that the request with this signature was stored."""
    try:
        async with asyncio.timeout(REDIS_WAIT_SECONDS):
            redis_client = await get_redis_client()
            if redis_client is None:
                return
            await redis_client.set(_key(key_id, signature), "1", ex=_ttl_seconds())
    except RedisError, OSError:
        # swallowed-exceptions: handled, not dropped: the request was stored; its replay will be too.
        logger.exception("HMAC replay mark failed")
