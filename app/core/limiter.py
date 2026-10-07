"""Per-route request limits: slowapi, the fleet's sanctioned limiter.

luxarch --doc FLEET-RATE-LIMIT-STANDARD. Keyed on the real client behind the proxy
(app.core.client_ip). State lives in Redis when REDIS_URL is set, so every worker shares one
count; without it (dev, tests) it is in-process.

Store-unavailable posture (§1.5): this is a REQUEST limiter, so it FAILS OPEN. If Redis is
unreachable, slowapi falls back to an in-process count (in_memory_fallback_enabled), and a storage
error never turns into a refused request (swallow_errors). Redis being down must not take the
dashboard down. Nothing here is a spend or quota ceiling, which would have to fail closed.
"""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import Response

from app.core.client_ip import client_ip_from_request
from app.core.config import settings

limiter = Limiter(
    key_func=client_ip_from_request,
    storage_uri=settings.REDIS_URL or "memory://",
    in_memory_fallback_enabled=True,
    swallow_errors=True,
    headers_enabled=True,
)


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> Response:
    """slowapi's 429 (with Retry-After), registered for RateLimitExceeded only."""
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return _rate_limit_exceeded_handler(request, exc)
