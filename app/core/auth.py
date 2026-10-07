import secrets
from datetime import datetime, timezone
from typing import Optional

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
    """Verify username and password against configuration."""
    return (
        username == settings.DASHBOARD_USERNAME
        and password == settings.DASHBOARD_PASSWORD
    )


async def get_current_user(request: Request) -> Optional[str]:
    """Get current user from session."""
    session = request.session

    # Check if user is authenticated
    if not session.get("authenticated"):
        return None

    # Check session timeout
    login_time = session.get("login_time")
    if login_time:
        login_dt = datetime.fromisoformat(login_time)
        now = datetime.now(timezone.utc)
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


def create_session(request: Request, username: str):
    """Create a new authenticated session."""
    request.session.clear()
    request.session["authenticated"] = True
    request.session["username"] = username
    request.session["login_time"] = datetime.now(timezone.utc).isoformat()

    logger.info("User logged in", username=username)


def destroy_session(request: Request):
    """Destroy the current session."""
    username = request.session.get("username", "unknown")
    request.session.clear()

    logger.info("User logged out", username=username)
