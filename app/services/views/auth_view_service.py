"""AuthViewService — the web seam for sign-in and sign-out: where each one lands, by route name.

Every path is resolved from its route's name (luxarch --playbook url-in-view), and a `next` the
request supplies goes through safe_redirect_target (FLEET-AUTH-SESSION-STANDARD §6).
"""

from typing import Any
from urllib.parse import urlencode

from fastapi import Request

from app.core.config import settings
from app.core.safe_redirect import safe_redirect_target
from app.core.tracing import create_service_span

# The login page's banners. The query string carries a code; the text is fixed here, so no text a
# URL supplies is ever rendered on the page.
LOGIN_ERRORS = {
    "invalid": "Invalid username or password",
    "store_down": "Sign-in is unavailable: the session store is down",
    "logout_partial": "Signed out in this browser only: the session store is down, "
    "so other sessions were not revoked",
}
LOGIN_MESSAGES = {"logged_out": "You have been logged out"}


class AuthViewService:
    def __init__(self, request: Request) -> None:
        self._request = request

    def after_login(self, next: str = "") -> str:
        """Where a sign-in lands: `next` when it is a path on this site, else the overview."""
        with create_service_span("AuthViewService", "after_login"):
            overview = str(self._request.app.url_path_for("dashboard.overview.page"))
            return safe_redirect_target(next, overview) if next else overview

    def login_page(self, **query: str) -> str:
        """The login page's path, with `query` as its query string."""
        with create_service_span("AuthViewService", "login_page"):
            path = self._request.app.url_path_for("auth.login_page")
            return f"{path}?{urlencode(query)}"

    def login_context(
        self, next: str, error: str | None, message: str | None
    ) -> dict[str, Any]:
        with create_service_span("AuthViewService", "login_context"):
            error_text = LOGIN_ERRORS.get(error or "")
            return {
                "next": next or self.after_login(),
                "login_url": str(self._request.app.url_path_for("auth.login")),
                "error": error_text,
                # One banner: an error outranks a confirmation.
                "message": None if error_text else LOGIN_MESSAGES.get(message or ""),
                "version": settings.APP_VERSION,
                "build_timestamp": settings.BUILD_TIMESTAMP,
            }
