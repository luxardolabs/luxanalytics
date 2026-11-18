"""Per-route request limits: slowapi, the fleet's sanctioned limiter.

luxarch --doc FLEET-RATE-LIMIT-STANDARD. Keyed on the real client behind the proxy
(app.core.client_ip), one budget per IPv4 address or IPv6 /64. State lives in Redis when REDIS_URL
is set, so every worker shares one count; without it (dev, tests) it is in-process.

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
from app.core.rate_limiter import address_identifier
from app.core.redis_client import with_default_timeouts

# slowapi's storage is redis-py's synchronous client, called in the request path: each call blocks
# the event loop, every request with it, until Redis answers. Its connect and reply bounds are
# this, unless REDIS_URL sets its own. A timeout marks the storage dead and sends slowapi to its
# in-process count, which starts every client at zero: the fail-open posture above. So the bound
# sits above ordinary latency spikes, which must not reopen an exhausted login budget, and below
# the async pool's 2 s. The request that finds Redis failing also makes one health check, so a
# stalled Redis can block the loop for up to twice this, once per backoff interval.
REQUEST_LIMITER_TIMEOUT_SECONDS = 1.0


def client_key(request: Request) -> str:
    """One budget per client, as the ingest and request limits key it: an IPv4 address, or a
    native IPv6 address's /64, which one host or subscriber holds whole."""
    return address_identifier(client_ip_from_request(request))


limiter = Limiter(
    key_func=client_key,
    storage_uri=with_default_timeouts(
        settings.REDIS_URL,
        connect=REQUEST_LIMITER_TIMEOUT_SECONDS,
        read=REQUEST_LIMITER_TIMEOUT_SECONDS,
    )
    if settings.REDIS_URL
    else "memory://",
    in_memory_fallback_enabled=True,
    swallow_errors=True,
    headers_enabled=True,
)


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> Response:
    """slowapi's 429 (with Retry-After), registered for RateLimitExceeded only."""
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return _rate_limit_exceeded_handler(request, exc)
