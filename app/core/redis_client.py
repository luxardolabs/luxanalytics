"""Redis client configuration and connection management."""

import logging
import time
from collections.abc import Callable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

# How long a connection to Redis may take, TLS handshake included, when REDIS_URL sets no
# socket_connect_timeout (a value in the URL wins). Unbounded, a rediss:// URL at a plain port
# waits on every request that tries to connect.
REDIS_CONNECT_TIMEOUT_SECONDS = 1.0


def with_connect_timeout(url: str) -> str:
    """The URL both Redis clients open: its own socket_connect_timeout, else
    REDIS_CONNECT_TIMEOUT_SECONDS added to its query."""
    parts = urlsplit(url)
    names = {name for name, _ in parse_qsl(parts.query, keep_blank_values=True)}
    if "socket_connect_timeout" in names:
        return url
    option = f"socket_connect_timeout={REDIS_CONNECT_TIMEOUT_SECONDS:g}"
    query = f"{parts.query}&{option}" if parts.query else option
    return urlunsplit(parts._replace(query=query))


# A Redis failure that repeats on every request logs at most once per this many seconds.
OUTAGE_LOG_INTERVAL = 60.0


class LogThrottle:
    """When a repeated failure was last logged (None: not yet), so it logs at most once per
    OUTAGE_LOG_INTERVAL instead of once per request."""

    def __init__(self) -> None:
        self.clock: Callable[[], float] = time.monotonic
        self.last_logged: float | None = None

    def due(self) -> bool:
        """True, and the interval restarts, when this failure may be logged now."""
        now = self.clock()
        if (
            self.last_logged is not None
            and now - self.last_logged < OUTAGE_LOG_INTERVAL
        ):
            return False
        self.last_logged = now
        return True


# The client could not be created: each request tries again, and logs it once per interval.
init_failures = LogThrottle()

# Global Redis client instance
_redis_client: redis.Redis | None = None


async def get_redis_client() -> redis.Redis | None:
    """Get or create Redis client instance."""
    global _redis_client

    if not settings.REDIS_URL:
        return None

    if _redis_client is None:
        try:
            # A blocking pool: a burst wider than the pool waits (up to `timeout`) for a free
            # connection. A failed checkout would send each rate-limit check to the in-memory window
            # (a second budget).
            pool = redis.BlockingConnectionPool.from_url(
                with_connect_timeout(settings.REDIS_URL),
                encoding="utf-8",
                decode_responses=True,
                max_connections=50,
                timeout=5,
                health_check_interval=30,
                socket_keepalive=True,
            )
            # from_pool: the client owns the pool, so close_redis_client() closes its connections
            _redis_client = redis.Redis.from_pool(pool)
            # Test connection
            await _redis_client.ping()
            logger.info("Redis client initialized successfully")
        except RedisError, OSError:
            # Handled: without Redis the rate limiter uses its in-memory window. The traceback is
            # logged so an unreachable Redis is visible, not just a one-line message, once per
            # interval: every request tries again while Redis stays down.
            if init_failures.due():
                logger.exception("Failed to initialize Redis client")
            _redis_client = None

    return _redis_client


async def close_redis_client() -> None:
    """Close Redis client connection."""
    global _redis_client

    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis client closed")
