"""Redis-based distributed rate limiter with sliding window algorithm."""

import hashlib
import json
import logging
import math
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)


# The sliding window for one or more budgets as one Redis script: for every key, drop what left
# its window and count; only when every key has room for its cost are `cost` members (each
# unique) added to each. All or nothing, one round trip, atomic. ARGV: now, member, then per key
# window, limit, cost. Returns {allowed, refused key index (1-based, 0 when allowed), count of the
# refused key (the first key's count after adding when allowed), score of the entry whose leaving
# makes room (empty when none will)}.
_SLIDING_WINDOW = """
local now, member = tonumber(ARGV[1]), ARGV[2]
for i, key in ipairs(KEYS) do
    local window = tonumber(ARGV[3 * i])
    local limit, cost = tonumber(ARGV[3 * i + 1]), tonumber(ARGV[3 * i + 2])
    redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
    local count = redis.call('ZCARD', key)
    if count + cost > limit then
        local rank = count + cost - limit - 1
        local freeing = redis.call('ZRANGE', key, rank, rank, 'WITHSCORES')
        return {0, i, count, freeing[2] or ''}
    end
end
local first = 0
for i, key in ipairs(KEYS) do
    local window, cost = tonumber(ARGV[3 * i]), tonumber(ARGV[3 * i + 2])
    for j = 1, cost do
        redis.call('ZADD', key, now, member .. ':' .. j)
    end
    redis.call('EXPIRE', key, window + 1)
    if i == 1 then
        first = redis.call('ZCARD', key)
    end
end
return {1, 0, first, ''}
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
        # The in-memory fallback holds at most this many identifiers, evicting the least recently
        # used: an identifier can be client-chosen (a device id), so it must not grow without
        # bound while Redis is down. An evicted identifier starts again with a full budget.
        self.memory_identifiers_max = memory_identifiers_max
        # Fallback in-memory storage
        # identifier -> [(request time, cost)] within the window, least recently used first
        self._memory_storage: OrderedDict[str, list[tuple[datetime, int]]] = (
            OrderedDict()
        )

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
        refused, metadata = await admit([Budget(self, identifier, cost)])
        return refused is None, metadata

    def _memory_entries(
        self, identifier: str, window_start: datetime
    ) -> list[tuple[datetime, int]]:
        """The identifier's requests still in the window, marked most recently used."""
        entries = [
            entry
            for entry in self._memory_storage.pop(identifier, [])
            if entry[0] > window_start
        ]
        while len(self._memory_storage) >= self.memory_identifiers_max:
            self._memory_storage.popitem(last=False)
        self._memory_storage[identifier] = entries
        return entries


@dataclass(frozen=True)
class Budget:
    """One budget a request spends: `cost` against `identifier` in `limiter`'s window."""

    limiter: RateLimiter
    identifier: str
    cost: int


def _allowed(count: int, budget: Budget, now: float) -> dict[str, Any]:
    return {
        "allowed": True,
        "remaining": max(0, budget.limiter.requests_per_window - count),
        "reset": int(now + budget.limiter.window_seconds),
        "retry_after": None,
    }


def _refused(freeing_at: float | None, budget: Budget, now: float) -> dict[str, Any]:
    # Rounded up: an entry leaves the window only once its full age has passed, so a client that
    # waits exactly retry_after must find room. No entry to wait for (the cost exceeds the limit
    # itself): a whole window.
    window = budget.limiter.window_seconds
    retry_after = (
        max(1, math.ceil(freeing_at + window - now))
        if freeing_at is not None
        else window
    )
    return {
        "allowed": False,
        "remaining": 0,
        "reset": int(now + retry_after),
        "retry_after": retry_after,
    }


async def admit(budgets: Sequence[Budget]) -> tuple[int | None, dict[str, Any]]:
    """Spend every budget, or none: (None, the first budget's metadata) when each has room, else
    (the index of a budget without room, its metadata) and nothing is spent. Redis being
    unreachable falls back to each limiter's in-memory window."""
    redis_client = await get_redis_client()
    if redis_client:
        try:
            return await _admit_redis(redis_client, budgets)
        except RedisError, OSError:
            # swallowed-exceptions: handled, not dropped: Redis being unreachable falls back to
            # the in-memory windows, so requests stay limited. Anything else is a bug and raises.
            logger.exception("Redis rate limit check failed")
    return _admit_memory(budgets)


async def _admit_redis(
    redis_client: redis.Redis, budgets: Sequence[Budget]
) -> tuple[int | None, dict[str, Any]]:
    now = time.time()
    args: list[Any] = [repr(now), uuid.uuid4().hex]
    for budget in budgets:
        args += [
            budget.limiter.window_seconds,
            budget.limiter.requests_per_window,
            budget.cost,
        ]
    allowed, refused_key, count, freeing = await redis_client.eval(
        _SLIDING_WINDOW,
        len(budgets),
        *(f"{b.limiter.key_prefix}:{b.identifier}" for b in budgets),
        *args,
    )
    if int(allowed):
        return None, _allowed(int(count), budgets[0], now)
    index = int(refused_key) - 1
    return index, _refused(float(freeing) if freeing else None, budgets[index], now)


def _admit_memory(budgets: Sequence[Budget]) -> tuple[int | None, dict[str, Any]]:
    now = datetime.now(UTC)
    held = []
    for index, budget in enumerate(budgets):
        limiter = budget.limiter
        window_start = now - timedelta(seconds=limiter.window_seconds)
        entries = limiter._memory_entries(budget.identifier, window_start)
        count = sum(cost for _, cost in entries)
        excess = count + budget.cost - limiter.requests_per_window
        if excess > 0:
            # The entry whose leaving frees `excess` units, oldest first.
            freeing_at = None
            freed = 0
            for when, cost in entries:
                freed += cost
                if freed >= excess:
                    freeing_at = when.timestamp()
                    break
            return index, _refused(freeing_at, budget, now.timestamp())
        held.append((entries, count))
    for budget, (entries, _count) in zip(budgets, held, strict=True):
        entries.append((now, budget.cost))
    first_count = held[0][1] + budgets[0].cost
    return None, _allowed(first_count, budgets[0], now.timestamp())


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
    """An authenticated app, one of its devices, or the client's address is over its budget;
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


def _ip_rate_limit_events() -> int:
    return settings.IP_RATE_LIMIT_EVENTS


def _device_rate_limit_events() -> int:
    return settings.DEVICE_RATE_LIMIT_EVENTS


def _app_rate_limit_events() -> int:
    return settings.APP_RATE_LIMIT_EVENTS


def _ingest_rate_limit_window() -> int:
    return settings.INGEST_RATE_LIMIT_WINDOW


# Ingest is limited in events, per client address, per device and per app. One of each per
# process, like _app_limiter.
_ip_event_limiter = RateLimiter(
    key_prefix="ip_event_limit",
    default_requests=_ip_rate_limit_events,
    default_window=_ingest_rate_limit_window,
)
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
    app_id: str, client_ip: str, events_per_device: Mapping[str | None, int]
) -> None:
    """The ingest limits, for a batch that authenticated (HMAC or DSN) and parsed, counted in
    events and spent all or nothing: the batch against its client address's budget, each device's
    events against that device's, and the batch against the app's ceiling.

    An ingest credential (the DSN's public_id, an HMAC key id) ships in the app binary, so a budget
    keyed on the app alone is one anyone can spend. The device limit is the tight one: a runaway
    device is refused without costing the app's other devices. Device ids are client-chosen, so
    the address limit bounds what one client can send under any number of them, and the app
    ceiling, set above several addresses' worth, bounds a flood spread over many. Events with no
    device_id share one budget per app. A refused batch spends nothing."""
    total = sum(events_per_device.values())
    budgets = [
        Budget(_ip_event_limiter, client_ip, total),
        *(
            Budget(_device_limiter, _device_identifier(app_id, device_id), count)
            for device_id, count in events_per_device.items()
        ),
        Budget(_app_event_ceiling, app_id, total),
    ]
    refused, metadata = await admit(budgets)
    if refused is None:
        return
    limiter = budgets[refused].limiter
    if limiter is _ip_event_limiter:
        error = "Rate limit exceeded"
    elif limiter is _device_limiter:
        error = "Device rate limit exceeded"
    else:
        error = "App rate limit exceeded"
    logger.warning(
        error, extra={"app_id": app_id, "retry_after": metadata.get("retry_after")}
    )
    raise AppRateLimitExceeded(metadata, limiter.requests_per_window, error)
