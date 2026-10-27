"""Redis-based distributed rate limiter with sliding window algorithm."""

import hashlib
import json
import logging
import math
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)


# The sliding window as one Redis script: drop what left the window, count, and add `cost` members
# (each unique) only when they fit. One round trip, atomic. Returns {allowed, count after, oldest
# score when refused}.
_SLIDING_WINDOW = """
local key, now, window = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2])
local limit, cost, member = tonumber(ARGV[3]), tonumber(ARGV[4]), ARGV[5]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count + cost <= limit then
    for i = 1, cost do
        redis.call('ZADD', key, now, member .. ':' .. i)
    end
    redis.call('EXPIRE', key, window + 1)
    return {1, count + cost, ''}
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
return {0, count, oldest[2] or ''}
"""


def _rate_limit_requests() -> int:
    return settings.RATE_LIMIT_REQUESTS


def _rate_limit_window() -> int:
    return settings.RATE_LIMIT_WINDOW


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
        default_requests: Callable[[], int] = _rate_limit_requests,
        default_window: Callable[[], int] = _rate_limit_window,
        memory_identifiers_max: int = 10_000,
    ) -> None:
        # Unset, a limit is read from settings when it is used, so one long-lived limiter (whose
        # in-memory fallback window must outlive a request) follows the configured value.
        self._requests_per_window = requests_per_window
        self._window_seconds = window_seconds
        self._default_requests = default_requests
        self._default_window = default_window
        self.key_prefix = key_prefix
        # Past this many identifiers, the in-memory fallback drops the ones with nothing left in
        # their window: an identifier can be client-chosen (a device id), so the fallback must not
        # grow without bound while Redis is down.
        self.memory_identifiers_max = memory_identifiers_max
        # Fallback in-memory storage
        # identifier -> [(request time, cost)] within the window
        self._memory_storage: dict[str, list[tuple[datetime, int]]] = {}

    @property
    def requests_per_window(self) -> int:
        if self._requests_per_window is None:
            return self._default_requests()
        return self._requests_per_window

    @requests_per_window.setter
    def requests_per_window(self, value: int) -> None:
        self._requests_per_window = value

    @property
    def window_seconds(self) -> int:
        if self._window_seconds is None:
            return self._default_window()
        return self._window_seconds

    @window_seconds.setter
    def window_seconds(self, value: int) -> None:
        self._window_seconds = value

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
        """Check rate limit using Redis with sliding window: one atomic script, so a concurrent
        burst cannot read the same count and all be admitted."""
        key = f"{self.key_prefix}:{identifier}"
        now = time.time()
        try:
            allowed, count, oldest = await redis_client.eval(
                _SLIDING_WINDOW,
                1,
                key,
                repr(now),
                self.window_seconds,
                self.requests_per_window,
                cost,
                uuid.uuid4().hex,
            )
        except RedisError, OSError:
            # swallowed-exceptions: handled, not dropped: Redis being unreachable falls back to
            # the in-memory window, so requests stay limited. Anything else is a bug and raises.
            logger.exception("Redis rate limit check failed")
            return self._check_memory(identifier, cost)

        if int(allowed):
            return True, {
                "allowed": True,
                "remaining": max(0, self.requests_per_window - int(count)),
                "reset": int(now + self.window_seconds),
                "retry_after": None,
            }
        # Rounded up: the oldest entry leaves the window only once its full age has passed, so a
        # client that waits exactly retry_after must find room.
        retry_after = (
            max(1, math.ceil(float(oldest) + self.window_seconds - now))
            if oldest
            else self.window_seconds
        )
        return False, {
            "allowed": False,
            "remaining": 0,
            "reset": int(now + retry_after),
            "retry_after": retry_after,
        }

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
            if len(self._memory_storage) >= self.memory_identifiers_max:
                self._prune_memory(window_start)
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

    def _prune_memory(self, window_start: datetime) -> None:
        """Drop every identifier with no request left in the window."""
        self._memory_storage = {
            identifier: requests
            for identifier, requests in self._memory_storage.items()
            if requests and requests[-1][0] > window_start
        }


# Per-app budgets that differ from RATE_LIMIT_*: app_id -> (requests, window seconds). Empty:
# every app has the default.
APP_LIMITS: dict[str, tuple[int, int]] = {}


class AppRateLimiter(RateLimiter):
    """Rate limiter specifically for app_id based limiting."""

    def __init__(self) -> None:
        super().__init__(key_prefix="app_rate_limit")
        # An app with its own budget gets its own limiter, so the shared one is never
        # reconfigured for every app after it.
        self._own_budgets: dict[str, RateLimiter] = {}

    async def check_app_limit(
        self, app_id: str, cost: int = 1
    ) -> tuple[bool, dict[str, Any]]:
        """Check rate limit for specific app_id."""
        if app_id in APP_LIMITS:
            requests, window = APP_LIMITS[app_id]
            own = self._own_budgets.get(app_id)
            if own is None or (own.requests_per_window, own.window_seconds) != (
                requests,
                window,
            ):
                own = RateLimiter(requests, window, key_prefix=self.key_prefix)
                self._own_budgets[app_id] = own
            return await own.is_allowed(app_id, cost)
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


class AppRateLimitExceeded(Exception):
    """An authenticated app, or one of its devices, is over its budget;
    app_rate_limit_exceeded_handler (beside the IP limit, in request_middleware) answers it with
    the same 429, carrying `error`."""

    def __init__(
        self,
        metadata: dict[str, Any],
        limit: int,
        error: str = "App rate limit exceeded",
    ) -> None:
        super().__init__(error)
        self.metadata = metadata
        self.limit = limit
        self.error = error


# One per process: its in-memory fallback window (Redis down) has to outlive a request.
_app_limiter = AppRateLimiter()


async def enforce_app_rate_limit(app_id: str) -> None:
    """The per-app request limit on the signed stats read (verify_hmac_app), counted after the
    signature verifies, so no one without the secret spends the app's budget. Ingest is held to
    enforce_ingest_limits instead. A Redis failure falls back to the process's in-memory window, as
    the IP limit does."""
    allowed, metadata = await _app_limiter.check_app_limit(app_id)
    if allowed:
        return
    logger.warning(
        "App rate limit exceeded",
        extra={"app_id": app_id, "retry_after": metadata.get("retry_after")},
    )
    raise AppRateLimitExceeded(metadata, _app_limiter.requests_per_window)


def _device_rate_limit_events() -> int:
    return settings.DEVICE_RATE_LIMIT_EVENTS


def _app_rate_limit_events() -> int:
    return settings.APP_RATE_LIMIT_EVENTS


def _ingest_rate_limit_window() -> int:
    return settings.INGEST_RATE_LIMIT_WINDOW


# Ingest is limited in events, per device and per app. One of each per process, like _app_limiter.
_device_limiter = RateLimiter(
    key_prefix="device_event_limit",
    default_requests=_device_rate_limit_events,
    default_window=_ingest_rate_limit_window,
)
_app_event_ceiling = RateLimiter(
    key_prefix="app_event_ceiling",
    default_requests=_app_rate_limit_events,
    default_window=_ingest_rate_limit_window,
)


def _device_identifier(app_id: str, device_id: str | None) -> str:
    """One fixed-length key per (app, device): a device id is client-chosen, of any content."""
    return hashlib.sha256(json.dumps([app_id, device_id]).encode()).hexdigest()


async def enforce_ingest_limits(
    app_id: str, events_per_device: Mapping[str | None, int]
) -> None:
    """The ingest limits, for a batch that authenticated (HMAC or DSN) and parsed, counted in
    events: each device's events against its own budget, then the batch against the app's ceiling.

    An ingest credential (the DSN's public_id, an HMAC key id) ships in the app binary, so a budget
    keyed on the app alone is one anyone can spend. The device limit is the tight one: a runaway
    or flooding device is refused without costing the app's other devices. The app ceiling sits
    far above normal traffic and bounds a flood spread over many device ids. Events with no
    device_id share one budget per app."""
    for device_id in sorted(events_per_device, key=lambda d: (d is None, d or "")):
        allowed, metadata = await _device_limiter.is_allowed(
            _device_identifier(app_id, device_id), events_per_device[device_id]
        )
        if not allowed:
            logger.warning(
                "Device rate limit exceeded",
                extra={"app_id": app_id, "retry_after": metadata.get("retry_after")},
            )
            raise AppRateLimitExceeded(
                metadata,
                _device_limiter.requests_per_window,
                "Device rate limit exceeded",
            )
    allowed, metadata = await _app_event_ceiling.is_allowed(
        app_id, sum(events_per_device.values())
    )
    if not allowed:
        logger.warning(
            "App rate limit exceeded",
            extra={"app_id": app_id, "retry_after": metadata.get("retry_after")},
        )
        raise AppRateLimitExceeded(metadata, _app_event_ceiling.requests_per_window)
