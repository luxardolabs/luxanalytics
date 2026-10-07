import secrets
from datetime import UTC, datetime
from urllib.parse import urlsplit

import structlog
from fastapi import HTTPException, Request, status

from app.core.config import settings

logger = structlog.get_logger()


def get_session_secret() -> str:
    """Get or generate session secret."""
    if settings.DASHBOARD_SESSION_SECRET:
        return settings.DASHBOARD_SESSION_SECRET

    # Generate a random secret if not configured
    return secrets.token_urlsafe(32)


def verify_credentials(username: str, password: str) -> bool:
    """Check dashboard credentials in constant time, so response timing leaks nothing about them.

    Both comparisons always run (no short-circuit) for the same reason.
    """
    user_ok = secrets.compare_digest(
        username.encode(), settings.DASHBOARD_USERNAME.encode()
    )
    pass_ok = secrets.compare_digest(
        password.encode(), settings.DASHBOARD_PASSWORD.encode()
    )
    return user_ok and pass_ok


async def get_current_user(request: Request) -> str | None:
    """Get current user from session."""
    session = request.session

    # Check if user is authenticated
    if not session.get("authenticated"):
        return None

    # Check session timeout
    login_time = session.get("login_time")
    if login_time:
        login_dt = datetime.fromisoformat(login_time)
        now = datetime.now(UTC)
        if (now - login_dt).total_seconds() > settings.DASHBOARD_SESSION_TIMEOUT:
            # Session expired
            session.clear()
            return None

    return session.get("username")


async def require_auth(request: Request):
    """Dependency to require authentication for protected routes."""
    user = await get_current_user(request)

    if not user:
        # Store the requested URL to redirect after login
        next_url = str(request.url.path)
        if request.url.query:
            next_url += f"?{request.url.query}"

        # Redirect to login page
        logger.info("Unauthenticated access attempt", path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/login?next={next_url}"},
        )

    return user


DEFAULT_AFTER_LOGIN = "/dashboard/overview"


def safe_redirect_target(target: str) -> str:
    """Return `target` only if it is a path on THIS site, else the dashboard home.

    The login flow redirects to a caller-supplied `next`; accepting an absolute or
    scheme-relative URL there is an open redirect (a phishing link that bounces a freshly
    logged-in user to another site). Rejects `//host`, `/\\host`, any backslash or control
    character (browsers normalise those into `//host`), and anything with a scheme or netloc.
    """
    if not target.startswith("/") or target.startswith("//"):
        return DEFAULT_AFTER_LOGIN
    if "\\" in target or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
        return DEFAULT_AFTER_LOGIN
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return DEFAULT_AFTER_LOGIN
    return target


def create_session(request: Request, username: str):
    """Create a new authenticated session."""
    request.session.clear()
    request.session["authenticated"] = True
    request.session["username"] = username
    request.session["login_time"] = datetime.now(UTC).isoformat()

    logger.info("User logged in", username=username)


def destroy_session(request: Request):
    """Destroy the current session."""
    username = request.session.get("username", "unknown")
    request.session.clear()

    logger.info("User logged out", username=username)
