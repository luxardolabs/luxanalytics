"""Redis client configuration and connection management."""

import logging
import time
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

# How long a connection to Redis may take, TLS handshake included, when REDIS_URL sets no
# socket_connect_timeout (a value in the URL wins). Unbounded, a rediss:// URL at a plain port
# waits on every request that tries to connect.
REDIS_CONNECT_TIMEOUT_SECONDS = 1.0

# How long a command may wait on its reply when REDIS_URL sets no socket_timeout (a value in the
# URL wins). Unbounded, a hung Redis holds every command and its pool connection until it answers.
# Above the replay check's quarter-second wait (which leaves a slow command to finish), and a few
# seconds at most for a rate-limit check that then falls back to its in-memory window.
REDIS_SOCKET_TIMEOUT_SECONDS = 2.0


def with_default_timeouts(
    url: str, connect: float | None = None, read: float | None = None
) -> str:
    """The URL a Redis client opens: each of socket_connect_timeout and socket_timeout as the URL
    sets it, else `connect` and `read` (REDIS_CONNECT_TIMEOUT_SECONDS and
    REDIS_SOCKET_TIMEOUT_SECONDS when not given) added to the query. A blank value counts as unset:
    redis-py ignores it, which would leave the client unbounded."""
    parts = urlsplit(url)
    defaults = {
        "socket_connect_timeout": REDIS_CONNECT_TIMEOUT_SECONDS
        if connect is None
        else connect,
        "socket_timeout": REDIS_SOCKET_TIMEOUT_SECONDS if read is None else read,
    }
    given = parts.query.split("&") if parts.query else []
    kept = [
        part
        for part in given
        if not (
            part.partition("=")[0] in defaults and not part.partition("=")[2].strip()
        )
    ]
    named = {part.partition("=")[0] for part in kept}
    options = [
        f"{name}={value:g}" for name, value in defaults.items() if name not in named
    ]
    if not options and kept == given:
        return url
    return urlunsplit(parts._replace(query="&".join([*kept, *options])))


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
                with_default_timeouts(settings.REDIS_URL),
                encoding="utf-8",
                decode_responses=True,
                max_connections=50,
                timeout=5,
                health_check_interval=30,
                socket_keepalive=True,
            )
            # from_pool: the client owns the pool, so closing the client closes its connections.
            # Set before the ping, so a request arriving meanwhile shares it rather than opening
            # another pool.
            client = _redis_client = redis.Redis.from_pool(pool)
            try:
                await client.ping()
            except BaseException:
                # The next request opens a new pool: this one is closed, not left behind.
                _redis_client = None
                await client.aclose()
                raise
            logger.info("Redis client initialized successfully")
        except RedisError, OSError:
            # Handled: without Redis the rate limiter uses its in-memory window. The traceback is
            # logged so an unreachable Redis is visible, not just a one-line message, once per
            # interval: every request tries again while Redis stays down.
            if init_failures.due():
                logger.exception("Failed to initialize Redis client")

    return _redis_client


async def close_redis_client() -> None:
    """Close Redis client connection."""
    global _redis_client

    global _replay_redis

    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis client closed")
    if _replay_redis is not None:
        await _replay_redis.aclose()
        _replay_redis = None


# How long the HMAC replay check waits on Redis, a free connection included, before failing open.
REPLAY_WAIT_SECONDS = 0.25

# The replay check's and the replay mark's own connections, apart from the 50 the rate limits use:
# a stalled Redis then holds at most these, and a limit check never waits behind a replay command.
REPLAY_CHECK_CONNECTIONS = 5
REPLAY_MARK_CONNECTIONS = 5


class ReplayRedis:
    """The two clients the HMAC replay marks use, each over its own small pool. A check waits for
    a free connection no longer than REPLAY_WAIT_SECONDS; a mark waits as long as the pool's own
    timeout (5 s), so a burst of marks is queued, not dropped."""

    def __init__(self, check: redis.Redis, mark: redis.Redis) -> None:
        self.check = check
        self.mark = mark

    async def exists(self, key: str) -> int:
        return int(await self.check.exists(key))

    async def set(self, key: str, value: str, ex: int) -> object:
        reply: object = await self.mark.set(key, value, ex=ex)
        return reply

    async def aclose(self) -> None:
        await self.check.aclose()
        await self.mark.aclose()


_replay_redis: ReplayRedis | None = None


def _replay_client(url: str, connections: int, checkout_seconds: float) -> redis.Redis:
    pool = redis.BlockingConnectionPool.from_url(
        with_default_timeouts(url),
        encoding="utf-8",
        decode_responses=True,
        max_connections=connections,
        timeout=checkout_seconds,
        health_check_interval=30,
        socket_keepalive=True,
    )
    return redis.Redis.from_pool(pool)


async def get_replay_redis() -> ReplayRedis | None:
    """The replay marks' clients (created on first use, connecting on first command), or None when
    REDIS_URL is unset."""
    global _replay_redis

    if not settings.REDIS_URL:
        return None
    if _replay_redis is None:
        _replay_redis = ReplayRedis(
            check=_replay_client(
                settings.REDIS_URL, REPLAY_CHECK_CONNECTIONS, REPLAY_WAIT_SECONDS
            ),
            mark=_replay_client(settings.REDIS_URL, REPLAY_MARK_CONNECTIONS, 5),
        )
    return _replay_redis
