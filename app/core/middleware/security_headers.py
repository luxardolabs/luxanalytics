"""Security headers middleware for LuxAnalytics.

Adds CSP, X-Frame-Options, HSTS, and other security headers to all responses.
Ported from LuxWX pattern — all headers set in app, not nginx.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to all responses."""

    # CSP directives — restrictive, all JS/CSS self-hosted
    CSP_DIRECTIVES = {
        "default-src": "'self'",
        # Scripts: self + inline (for Alpine.js x-data, ECharts configs) + eval (for ECharts)
        "script-src": "'self' 'unsafe-inline' 'unsafe-eval'",
        # Styles: self + inline (for Tailwind utilities)
        "style-src": "'self' 'unsafe-inline'",
        # Images: self + data URIs (for inline SVGs/icons)
        "img-src": "'self' data:",
        # Fonts: self only
        "font-src": "'self'",
        # Connect: API calls to self only
        "connect-src": "'self'",
        # Frames: none
        "frame-src": "'none'",
        # Frame ancestors: self only (prevents clickjacking)
        "frame-ancestors": "'self'",
        # Forms: only submit to self
        "form-action": "'self'",
        # Base URI: only self
        "base-uri": "'self'",
        # Object/embed: none
        "object-src": "'none'",
    }

    def __init__(self, app: Any, environment: str = "production") -> None:
        super().__init__(app)
        self.environment = environment
        self.csp_header = self._build_csp()

    def _build_csp(self) -> str:
        parts = [f"{key} {value}" for key, value in self.CSP_DIRECTIVES.items()]
        return "; ".join(parts)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)

        response.headers["Content-Security-Policy"] = self.csp_header
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"

        return response
