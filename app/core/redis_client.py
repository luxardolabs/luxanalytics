"""Redis client configuration and connection management."""

import asyncio
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
    """Close both Redis clients, the replay one first so its pending marks get their short drain.
    Both are dropped and closed even when one close raises."""
    global _redis_client, _replay_redis

    client, replay_redis = _redis_client, _replay_redis
    _redis_client, _replay_redis = None, None
    try:
        if replay_redis is not None:
            await replay_redis.aclose()
    finally:
        if client:
            await client.aclose()
            logger.info("Redis client closed")


# How long the HMAC replay check waits on Redis, a free connection included, before failing open.
REPLAY_WAIT_SECONDS = 0.25

# The replay check's own connections, apart from the 50 the rate limits use: a stalled Redis then
# holds at most these, and a limit check never waits behind a replay command. Marks are written by
# one drainer on one connection of its own.
REPLAY_CHECK_CONNECTIONS = 5
REPLAY_MARK_CONNECTIONS = 1

# On close, pending marks get this long to be written; the rest are cancelled.
REPLAY_CLOSE_DRAIN_SECONDS = 2.0


class ReplayRedis:
    """The clients the HMAC replay marks use, apart from the rate limits'. A check waits for one
    of its connections no longer than REPLAY_WAIT_SECONDS. Marks are queued and written by one
    drainer, all pending ones in one pipelined round trip (SET NX EX each), so how fast they are
    written does not depend on how many connections there are: a mark fails only when Redis does."""

    def __init__(self, check: redis.Redis, mark: redis.Redis) -> None:
        self.check = check
        self.mark_client = mark
        self._pending: list[tuple[str, int, asyncio.Future[None]]] = []
        self._drainer: asyncio.Task[None] | None = None

    async def exists(self, key: str) -> int:
        return int(await self.check.exists(key))

    async def mark(self, key: str, ex: int) -> None:
        """Returns once `key` is written (expiring in `ex` seconds); raises what Redis raised."""
        written: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending.append((key, ex, written))
        if self._drainer is None or self._drainer.done():
            self._drainer = asyncio.ensure_future(self._drain())
        await written

    async def _drain(self) -> None:
        while self._pending:
            batch, self._pending = self._pending, []
            try:
                async with self.mark_client.pipeline(transaction=False) as pipe:
                    for key, ex, _written in batch:
                        pipe.set(key, "1", ex=ex, nx=True)
                    await pipe.execute()
            except (RedisError, OSError) as error:
                # swallowed-exceptions: handled, not dropped: each waiting mark raises it.
                for _key, _ex, written in batch:
                    if not written.done():
                        written.set_exception(error)
            else:
                for _key, _ex, written in batch:
                    if not written.done():
                        written.set_result(None)

    async def aclose(self, drain_seconds: float = REPLAY_CLOSE_DRAIN_SECONDS) -> None:
        """Give pending marks `drain_seconds` to be written, cancel the rest, close both clients
        (the second even when the first close raises)."""
        try:
            drainer = self._drainer
            if drainer is not None and not drainer.done():
                await asyncio.wait({drainer}, timeout=drain_seconds)
                drainer.cancel()
                await asyncio.gather(drainer, return_exceptions=True)
            for _key, _ex, written in self._pending:
                written.cancel()
            self._pending = []
        finally:
            try:
                await self.check.aclose()
            finally:
                await self.mark_client.aclose()


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
