import asyncio
import logging
import time
import zlib
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message

from app.core.client_ip import client_ip_from_request
from app.core.config import settings
from app.core.rate_limiter import AppRateLimiter, IPRateLimiter
from app.core.security import get_app_id_from_headers

logger = logging.getLogger(__name__)


def _inflate(body: bytes) -> tuple[bytes, bool] | None:
    """(decoded body, whether it was the legacy raw format), or None when it is neither."""
    try:
        return zlib.decompress(body), False
    except zlib.error:
        pass
    try:
        return zlib.decompress(body, -15), True
    except zlib.error:
        return None


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests and capture raw body for HMAC verification."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip body processing for non-API routes (form POSTs need the stream intact)
        if not request.url.path.startswith("/api/"):
            start_time = time.time()
            response = await call_next(request)
            process_time = time.time() - start_time
            logger.info(
                "Request completed",
                extra={
                    "method": request.method,
                    "url": str(request.url),
                    "process_time": round(process_time, 4),
                    "status": response.status_code,
                },
            )
            return response

        start_time = time.time()

        # Capture raw body for HMAC verification (API routes only)
        # Check if RequestSizeLimitMiddleware already read the body
        body = getattr(request.state, "body", None)
        content_encoding = request.headers.get("content-encoding", "").lower()

        if request.method in ["POST", "PUT", "PATCH"] and body is None:
            body = await request.body()
            # Store raw body in request state for HMAC verification
            request.state.raw_body = body

            # `Content-Encoding: deflate` is zlib (RFC 1950) per RFC 9110 §8.4.1.2, and that is what
            # the Swift SDK >= 1.1.0 sends. Raw DEFLATE (RFC 1951) is legacy-client support: SDK
            # <= 1.0.2 sent NSData.compressed(using: .zlib), which is raw despite its name, and
            # installed apps keep sending it until their users update (LUXANALYTI-69).
            if content_encoding == "deflate":
                decoded = _inflate(body)
                if decoded is None:
                    # A client error, answered as one: an HTTPException raised from a
                    # middleware reaches no exception handler and becomes a 500.
                    logger.warning(
                        "Undecodable deflate body", extra={"body_size": len(body)}
                    )
                    return JSONResponse(
                        status_code=400,
                        content={"error": "Invalid compressed data"},
                    )
                body_for_processing, legacy = decoded
                logger.debug(
                    "Request decompressed",
                    extra={
                        "format": "raw deflate (legacy SDK)" if legacy else "zlib",
                        "original_size": len(body),
                        "decompressed_size": len(body_for_processing),
                    },
                )
            else:
                # Use original body if not compressed
                body_for_processing = body

            # Store the processed body for easy access
            request.state.processed_body = body_for_processing

            # Re-create request stream for downstream processing
            async def receive() -> Message:
                return {"type": "http.request", "body": body_for_processing}

            request._receive = receive

        # Named fields only: the full header map carried Authorization, Cookie and the HMAC
        # signature into the logs (repo.log_fields_explicit).
        logger.debug(
            "Request started",
            extra={
                "method": request.method,
                "path": request.url.path,
                "content_type": request.headers.get("content-type"),
                "user_agent": request.headers.get("user-agent"),
                "key_id": request.headers.get("x-key-id"),
                "body_size": len(body) if body else 0,
                "compressed": content_encoding == "deflate",
                "client_ip": request.client.host if request.client else None,
            },
        )

        # Process request
        try:
            response = await call_next(request)
            process_time = time.time() - start_time

            logger.debug(
                "Request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "process_time": round(process_time, 4),
                },
            )

            response.headers["X-Process-Time"] = str(process_time)
            return response

        except Exception:
            process_time = time.time() - start_time
            logger.exception(
                "Request failed",
                extra={
                    "method": request.method,
                    "url": str(request.url),
                    "process_time": round(process_time, 4),
                },
            )
            raise


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-based distributed rate limiting with in-memory fallback."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.ip_limiter = IPRateLimiter()
        self.app_limiter = AppRateLimiter()
        # Fallback in-memory storage for backwards compatibility
        self.requests: defaultdict[str, list[datetime]] = defaultdict(list)
        self.lock = asyncio.Lock()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip rate limiting for health check and other system endpoints
        if request.url.path in ["/health", "/metrics", "/docs", "/openapi.json"]:
            return await call_next(request)

        # The real client behind the proxy; request.client is nginx for every caller.
        client_ip = client_ip_from_request(request)

        # Decide FIRST, then run the request exactly once. The try covers only the limit
        # check: wrapping call_next in it routed every exception the app raised into the
        # Redis fallback, which swallowed it and ran the request a second time.
        try:
            rejection, ip_metadata = await self._redis_decision(request, client_ip)
        except Exception:
            logger.debug(
                "Redis rate limiting failed, using in-memory fallback", exc_info=True
            )
            rejection, ip_metadata = await self._memory_decision(client_ip), {}

        if rejection is not None:
            return rejection

        try:
            response = await call_next(request)
        except ClientDisconnect:
            # Client disconnected during processing
            logger.debug(
                "Client disconnected during request processing",
                extra={"client_ip": client_ip, "path": request.url.path},
            )
            return Response(status_code=499)

        if ip_metadata.get("allowed"):
            response.headers["X-RateLimit-Limit"] = str(
                self.ip_limiter.requests_per_window
            )
            response.headers["X-RateLimit-Remaining"] = str(
                ip_metadata.get("remaining", 0)
            )
            response.headers["X-RateLimit-Reset"] = str(ip_metadata.get("reset", 0))

        return response

    async def _redis_decision(
        self, request: Request, client_ip: str
    ) -> tuple[Response | None, dict[str, Any]]:
        """The IP limit, then (for API routes) the app limit: a 429 response, or None to proceed."""
        ip_allowed, ip_metadata = await self.ip_limiter.check_ip_limit(client_ip)

        if not ip_allowed:
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "client_ip": client_ip,
                    "retry_after": ip_metadata.get("retry_after"),
                },
            )
            return _too_many_requests(
                "Rate limit exceeded",
                ip_metadata,
                limit=self.ip_limiter.requests_per_window,
            ), ip_metadata

        # For API endpoints, also check app_id rate limit
        if request.url.path.startswith("/api/"):
            app_id = get_app_id_from_headers(dict(request.headers))
            if app_id:
                try:
                    app_allowed, app_metadata = await self.app_limiter.check_app_limit(
                        app_id
                    )
                except RedisError, OSError:
                    # The per-app limit is a second line behind the IP limit that already
                    # passed: fail open, but never silently.
                    logger.warning(
                        "App rate limit check failed; request allowed",
                        extra={"app_id": app_id},
                        exc_info=True,
                    )
                    return None, ip_metadata
                if not app_allowed:
                    logger.warning(
                        "App rate limit exceeded",
                        extra={
                            "app_id": app_id,
                            "retry_after": app_metadata.get("retry_after"),
                        },
                    )
                    return _too_many_requests(
                        "App rate limit exceeded",
                        app_metadata,
                        limit=self.app_limiter.requests_per_window,
                    ), ip_metadata

        return None, ip_metadata

    async def _memory_decision(self, client_ip: str) -> Response | None:
        """In-memory IP limit for when Redis is unavailable: a 429 response, or None to proceed."""
        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=settings.RATE_LIMIT_WINDOW)

        async with self.lock:
            # Clean old requests
            self.requests[client_ip] = [
                req_time
                for req_time in self.requests[client_ip]
                if req_time > window_start
            ]

            # Check rate limit
            if len(self.requests[client_ip]) >= settings.RATE_LIMIT_REQUESTS:
                logger.warning(
                    "Rate limit exceeded (in-memory)",
                    extra={
                        "client_ip": client_ip,
                        "requests_count": len(self.requests[client_ip]),
                    },
                )
                # A response, not HTTPException: raised from a middleware it reaches no
                # exception handler and the client gets a 500.
                return _too_many_requests(
                    "Rate limit exceeded",
                    {"retry_after": settings.RATE_LIMIT_WINDOW},
                    limit=settings.RATE_LIMIT_REQUESTS,
                )

            # Add current request
            self.requests[client_ip].append(now)

        return None


def _too_many_requests(
    error: str, metadata: dict[str, Any], limit: int
) -> JSONResponse:
    retry_after = metadata.get("retry_after") or 60
    return JSONResponse(
        status_code=429,
        content={"error": error, "retry_after": retry_after},
        headers={
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(metadata.get("reset", 0)),
            "Retry-After": str(retry_after),
        },
    )


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce request body size limits."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.max_size = settings.MAX_REQUEST_SIZE

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Only check size for API routes — web form POSTs need the stream intact
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        # Check Content-Length header
        content_length = request.headers.get("content-length")

        if content_length:
            content_size = int(content_length)
            if content_size > self.max_size:
                logger.warning(
                    "Request body too large",
                    extra={
                        "content_length": content_length,
                        "max_size": self.max_size,
                        "path": request.url.path,
                    },
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "Request body too large",
                        "max_size": self.max_size,
                        "your_size": content_length,
                    },
                )

        # For requests without Content-Length, check during streaming
        if request.method in ["POST", "PUT", "PATCH"]:
            body_size = 0
            body_chunks = []

            try:
                async for chunk in request.stream():
                    body_size += len(chunk)
                    if body_size > self.max_size:
                        logger.warning(
                            "Request body exceeded limit during streaming",
                            extra={
                                "size_read": body_size,
                                "max_size": self.max_size,
                                "path": request.url.path,
                            },
                        )
                        return JSONResponse(
                            status_code=413,
                            content={
                                "error": "Request body too large",
                                "max_size": self.max_size,
                            },
                        )
                    body_chunks.append(chunk)
            except ClientDisconnect:
                # Client disconnected while streaming - this is normal
                logger.debug(
                    "Client disconnected during request streaming",
                    extra={"path": request.url.path, "bytes_read": body_size},
                )
                # Return early with a 499 Client Closed Request status
                return Response(status_code=499)

            # Store the body for later use
            body = b"".join(body_chunks)
            request.state.body = body

            # Recreate the request with the stored body
            async def receive() -> Message:
                return {"type": "http.request", "body": body}

            request._receive = receive

        return await call_next(request)
