"""Redis-based distributed rate limiter with sliding window algorithm."""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Distributed rate limiter using Redis with sliding window algorithm.
    Falls back to in-memory rate limiting if Redis is not available.
    """

    def __init__(
        self,
        requests_per_window: int | None = None,
        window_seconds: int | None = None,
        key_prefix: str = "rate_limit",
    ) -> None:
        self.requests_per_window = requests_per_window or settings.RATE_LIMIT_REQUESTS
        self.window_seconds = window_seconds or settings.RATE_LIMIT_WINDOW
        self.key_prefix = key_prefix
        # Fallback in-memory storage
        # identifier -> [(request time, cost)] within the window
        self._memory_storage: dict[str, list[tuple[datetime, int]]] = {}

    async def is_allowed(
        self, identifier: str, cost: int = 1
    ) -> tuple[bool, dict[str, Any]]:
        """
        Check if request is allowed under rate limit.

        Returns:
            Tuple of (is_allowed, metadata)
            metadata contains: remaining, reset_time, retry_after
        """
        redis_client = await get_redis_client()

        if redis_client:
            return await self._check_redis(redis_client, identifier, cost)
        else:
            return self._check_memory(identifier, cost)

    async def _check_redis(
        self, redis_client: redis.Redis, identifier: str, cost: int
    ) -> tuple[bool, dict[str, Any]]:
        """Check rate limit using Redis with sliding window."""
        key = f"{self.key_prefix}:{identifier}"
        now = time.time()
        window_start = now - self.window_seconds

        # Use Redis pipeline for atomic operations
        async with redis_client.pipeline() as pipe:
            try:
                # Remove old entries outside the window
                pipe.zremrangebyscore(key, 0, window_start)

                # Count requests in current window
                pipe.zcard(key)

                # Execute removal and count
                results = await pipe.execute()
                current_requests = results[1]

                # Check if under limit
                if current_requests + cost <= self.requests_per_window:
                    # Add new request(s)
                    pipe.zadd(key, {f"{now}:{cost}": now})
                    pipe.expire(key, self.window_seconds + 1)
                    await pipe.execute()

                    remaining = self.requests_per_window - current_requests - cost
                    reset_time = int(now + self.window_seconds)

                    return True, {
                        "allowed": True,
                        "remaining": max(0, remaining),
                        "reset": reset_time,
                        "retry_after": None,
                    }
                else:
                    # Get oldest request time to calculate retry_after
                    oldest = await redis_client.zrange(key, 0, 0, withscores=True)
                    if oldest:
                        # withscores=True yields (member, score); the score is the float timestamp
                        oldest_time = float(oldest[0][1])
                        retry_after = int(oldest_time + self.window_seconds - now)
                    else:
                        retry_after = self.window_seconds

                    return False, {
                        "allowed": False,
                        "remaining": 0,
                        "reset": int(now + retry_after),
                        "retry_after": retry_after,
                    }

            except RedisError, OSError:
                # swallowed-exceptions: handled, not dropped: Redis being unreachable falls back to
                # the in-memory window, so requests stay limited. Anything else is a bug and raises.
                logger.exception("Redis rate limit check failed")
                return self._check_memory(identifier, cost)

    def _check_memory(self, identifier: str, cost: int) -> tuple[bool, dict[str, Any]]:
        """Fallback in-memory rate limiting."""
        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=self.window_seconds)

        # Clean old requests
        if identifier in self._memory_storage:
            self._memory_storage[identifier] = [
                (req_time, req_cost)
                for req_time, req_cost in self._memory_storage[identifier]
                if req_time > window_start
            ]
        else:
            self._memory_storage[identifier] = []

        # Count current requests
        current_requests = sum(
            req_cost for _, req_cost in self._memory_storage[identifier]
        )

        if current_requests + cost <= self.requests_per_window:
            self._memory_storage[identifier].append((now, cost))
            remaining = self.requests_per_window - current_requests - cost
            reset_time = int((now + timedelta(seconds=self.window_seconds)).timestamp())

            return True, {
                "allowed": True,
                "remaining": max(0, remaining),
                "reset": reset_time,
                "retry_after": None,
            }
        else:
            # Calculate retry_after
            if self._memory_storage[identifier]:
                oldest_time = self._memory_storage[identifier][0][0]
                retry_after = int(
                    (
                        oldest_time + timedelta(seconds=self.window_seconds) - now
                    ).total_seconds()
                )
            else:
                retry_after = self.window_seconds

            return False, {
                "allowed": False,
                "remaining": 0,
                "reset": int((now + timedelta(seconds=retry_after)).timestamp()),
                "retry_after": retry_after,
            }


class AppRateLimiter(RateLimiter):
    """Rate limiter specifically for app_id based limiting."""

    def __init__(self) -> None:
        super().__init__(key_prefix="app_rate_limit")

    async def check_app_limit(
        self, app_id: str, cost: int = 1
    ) -> tuple[bool, dict[str, Any]]:
        """Check rate limit for specific app_id."""
        # Could have different limits per app
        app_limits: dict[str, tuple[int, int]] = {
            # "premium_app": (1000, 60),  # 1000 requests per minute
            # "basic_app": (100, 60),     # 100 requests per minute
        }

        if app_id in app_limits:
            self.requests_per_window, self.window_seconds = app_limits[app_id]

        return await self.is_allowed(app_id, cost)


class IPRateLimiter(RateLimiter):
    """Rate limiter for IP-based limiting."""

    def __init__(self) -> None:
        super().__init__(key_prefix="ip_rate_limit")

    async def check_ip_limit(
        self, ip_address: str, cost: int = 1
    ) -> tuple[bool, dict[str, Any]]:
        """Check rate limit for specific IP address."""
        return await self.is_allowed(ip_address, cost)
