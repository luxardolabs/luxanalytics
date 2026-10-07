import asyncio
import logging
import time
import zlib
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import ClientDisconnect

from app.core.config import settings
from app.core.rate_limiter import AppRateLimiter, IPRateLimiter
from app.core.security import get_app_id_from_headers

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests and capture raw body for HMAC verification."""

    async def dispatch(self, request: Request, call_next):
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
        decompressed_body = None
        content_encoding = request.headers.get("content-encoding", "").lower()

        if request.method in ["POST", "PUT", "PATCH"] and body is None:
            body = await request.body()
            # Store raw body in request state for HMAC verification
            request.state.raw_body = body

            # Check if body is compressed
            if content_encoding == "deflate":
                try:
                    # NSData.compressed(using: .zlib) produces standard zlib format
                    # which should have header bytes 0x78 0x9C (default) or 0x78 0xDA (max compression)
                    decompressed_body = zlib.decompress(body)

                    logger.info(
                        "Request decompressed",
                        extra={
                            "original_size": len(body),
                            "decompressed_size": len(decompressed_body),
                            "compression_ratio": round(
                                len(body) / len(decompressed_body), 2
                            ),
                        },
                    )
                    # Use decompressed body for downstream processing
                    body_for_processing = decompressed_body
                except zlib.error as e:
                    # Log detailed error information for debugging
                    logger.debug(
                        "Standard zlib decompression failed, trying raw deflate",
                        extra={
                            "error": str(e),
                            "content_encoding": content_encoding,
                            "body_length": len(body),
                            "body_start": body[:20].hex()
                            if len(body) >= 20
                            else body.hex(),
                            "expected_headers": "78 9C or 78 DA for zlib",
                        },
                    )
                    # Try raw deflate as fallback
                    try:
                        decompressed_body = zlib.decompress(body, -15)
                        logger.info(
                            "Request decompressed (raw deflate)",
                            extra={
                                "original_size": len(body),
                                "decompressed_size": len(decompressed_body),
                                "compression_ratio": round(
                                    len(body) / len(decompressed_body), 2
                                ),
                            },
                        )
                        body_for_processing = decompressed_body
                    except zlib.error as e2:
                        logger.error(
                            "Raw deflate decompression also failed",
                            extra={"error": str(e2)},
                        )
                        raise HTTPException(
                            status_code=400, detail="Invalid compressed data"
                        )
            else:
                # Use original body if not compressed
                body_for_processing = body

            # Store the processed body for easy access
            request.state.processed_body = body_for_processing

            # Re-create request stream for downstream processing
            async def receive():
                return {"type": "http.request", "body": body_for_processing}

            request._receive = receive

        # Log at debug level for full headers, info level for summary
        logger.debug("Request details", extra={"headers": dict(request.headers)})

        logger.debug(
            "Request started",
            extra={
                "method": request.method,
                "path": request.url.path,
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

        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                "Request failed",
                extra={
                    "method": request.method,
                    "url": str(request.url),
                    "error": str(e),
                    "process_time": round(process_time, 4),
                },
            )
            raise


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-based distributed rate limiting with in-memory fallback."""

    def __init__(self, app):
        super().__init__(app)
        self.ip_limiter = IPRateLimiter()
        self.app_limiter = AppRateLimiter()
        # Fallback in-memory storage for backwards compatibility
        self.requests = defaultdict(list)
        self.lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health check and other system endpoints
        if request.url.path in ["/health", "/metrics", "/docs", "/openapi.json"]:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"

        # Try Redis-based rate limiting first
        try:
            ip_allowed, ip_metadata = await self.ip_limiter.check_ip_limit(client_ip)

            if not ip_allowed:
                logger.warning(
                    "Rate limit exceeded",
                    extra={
                        "client_ip": client_ip,
                        "retry_after": ip_metadata.get("retry_after"),
                    },
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "Rate limit exceeded",
                        "retry_after": ip_metadata.get("retry_after", 60),
                    },
                    headers={
                        "X-RateLimit-Limit": str(self.ip_limiter.requests_per_window),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(ip_metadata.get("reset", 0)),
                        "Retry-After": str(ip_metadata.get("retry_after", 60)),
                    },
                )

            # For API endpoints, also check app_id rate limit
            if request.url.path.startswith("/api/"):
                try:
                    app_id = get_app_id_from_headers(dict(request.headers))
                    if app_id:
                        (
                            app_allowed,
                            app_metadata,
                        ) = await self.app_limiter.check_app_limit(app_id)
                        if not app_allowed:
                            logger.warning(
                                "App rate limit exceeded",
                                extra={
                                    "app_id": app_id,
                                    "retry_after": app_metadata.get("retry_after"),
                                },
                            )
                            return JSONResponse(
                                status_code=429,
                                content={
                                    "error": "App rate limit exceeded",
                                    "retry_after": app_metadata.get("retry_after", 60),
                                },
                                headers={
                                    "X-RateLimit-Limit": str(
                                        self.app_limiter.requests_per_window
                                    ),
                                    "X-RateLimit-Remaining": "0",
                                    "X-RateLimit-Reset": str(
                                        app_metadata.get("reset", 0)
                                    ),
                                    "Retry-After": str(
                                        app_metadata.get("retry_after", 60)
                                    ),
                                },
                            )
                except Exception:
                    pass  # Don't block if we can't determine app_id

            # Process request and add rate limit headers
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

        except Exception as e:
            # Fall back to in-memory rate limiting if Redis fails
            logger.debug(
                "Redis rate limiting failed, using in-memory fallback",
                extra={"error": str(e)},
            )

            now = datetime.now()
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
                    raise HTTPException(status_code=429, detail="Rate limit exceeded")

                # Add current request
                self.requests[client_ip].append(now)

            return await call_next(request)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce request body size limits."""

    def __init__(self, app):
        super().__init__(app)
        self.max_size = settings.MAX_REQUEST_SIZE

    async def dispatch(self, request: Request, call_next):
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
            async def receive():
                return {"type": "http.request", "body": body}

            request._receive = receive

        return await call_next(request)
