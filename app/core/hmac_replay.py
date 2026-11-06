"""Single use of an HMAC signature inside its timestamp window.

The signature covers the body and the timestamp, so the same signed request verifies until the
timestamp leaves the window. A signature is remembered in Redis (SET NX) for as long as it can
verify; a second use is a replay. Redis being unreachable, or not configured, fails open: every
signature then counts as a first use and ingest keeps working, as the rate limiter does."""

import logging

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)


def _key(signature: str) -> str:
    return f"hmac:sig:{signature}"


def _ttl_seconds() -> int:
    """A timestamp is accepted from the tolerance ahead of the clock until the tolerance behind it,
    so a signature can verify for twice the tolerance."""
    return 2 * settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE + 1


async def claim_signature(signature: str) -> bool:
    """True when this is the signature's first use inside its window, False for a replay."""
    redis_client = await get_redis_client()
    if redis_client is None:
        return True
    try:
        return bool(
            await redis_client.set(_key(signature), "1", nx=True, ex=_ttl_seconds())
        )
    except RedisError, OSError:
        # swallowed-exceptions: handled, not dropped: an unreachable Redis fails open (logged).
        logger.exception("HMAC replay check failed")
        return True


async def release_signature(signature: str) -> None:
    """Forget a claimed signature (the request it covered stored nothing)."""
    redis_client = await get_redis_client()
    if redis_client is None:
        return
    try:
        await redis_client.delete(_key(signature))
    except RedisError, OSError:
        # swallowed-exceptions: handled, not dropped: the claim then expires with its window.
        logger.exception("HMAC replay release failed")
