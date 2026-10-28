import logging
import secrets
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.request_route import route_template
from app.core.safe_redirect import safe_redirect_target
from app.crud.dashboard_session_crud import (
    read_sessions_valid_after,
    write_sessions_valid_after,
)

logger = logging.getLogger(__name__)


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


# The server-side half of logout (FLEET-AUTH-SESSION-STANDARD §2.1). The session is a signed cookie,
# so clearing it in one browser leaves a copied cookie valid until it expires. Logout stamps the
# database's session-state row with the time, and a session that logged in before the stamp is
# refused: one dashboard account, so a logout signs out every session.


class SessionStoreError(Exception):
    """The logout stamp cannot be read or written: no session is accepted, no logout is complete."""


async def _sessions_valid_after() -> datetime | None:
    """The time before which every session is refused: the last logout's, or None when no logout
    is recorded (the row is written by migration 011 and by every logout, so it is missing only
    from a table emptied by hand).

    Login asks this before it creates its session (session_store_available), so a sign-in the
    store cannot check is refused up front.

    Raises SessionStoreError when the database is unreachable, so the caller fails closed: a
    session that cannot be checked against the last logout is refused.
    """
    try:
        return await read_sessions_valid_after()
    except (SQLAlchemyError, OSError) as exc:
        raise SessionStoreError("the logout stamp cannot be read") from exc


async def session_store_available() -> bool:
    """Whether a session can be checked against the last logout right now (login asks first)."""
    try:
        await _sessions_valid_after()
    except SessionStoreError:
        logger.exception("The session store is unavailable")
        return False
    return True


async def _authenticated_user(
    request: Request, check_logout: bool = True
) -> str | None:
    """The signed-in username, or None: no session, expired, or revoked by a logout.

    Raises SessionStoreError when the last logout cannot be checked. ``check_logout=False`` asks
    the cookie alone (in date, signed in), for a logout that cannot read the stamp.
    """
    # A refused cookie is left as it is: it is refused on every request, and login replaces it
    session = request.session
    username = session.get("username")
    login_time = session.get("login_time")
    # A session with no login time cannot be checked against a timeout or a logout
    if not session.get("authenticated") or not username or not login_time:
        return None
    if not isinstance(username, str) or not isinstance(login_time, str):
        return None
    login_dt = datetime.fromisoformat(login_time)
    # Absolute session maximum, counted from login
    if (
        datetime.now(UTC) - login_dt
    ).total_seconds() > settings.DASHBOARD_SESSION_TIMEOUT:
        return None

    if not check_logout:
        return username

    # Revoked by a logout after this session signed in
    valid_after = await _sessions_valid_after()
    if valid_after is not None and login_dt <= valid_after:
        return None

    return username


async def get_current_user(request: Request) -> str | None:
    """The signed-in username, or None: no session, expired, revoked, or uncheckable (fail closed)."""
    try:
        return await _authenticated_user(request)
    except SessionStoreError:
        logger.exception("Cannot read the logout stamp; refusing the session")
        return None


async def require_auth(request: Request) -> str:
    """Dependency to require authentication for protected routes."""
    user = await get_current_user(request)

    if not user:
        # Store the requested URL to redirect after login
        next_url = str(request.url.path)
        if request.url.query:
            next_url += f"?{request.url.query}"

        # Redirect to login page
        logger.info(
            "Unauthenticated access attempt", extra={"route": route_template(request)}
        )
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={
                "Location": safe_redirect_target(
                    f"{request.app.url_path_for('auth.login_page')}"
                    f"?next={quote(next_url, safe='/')}"
                )
            },
        )

    return user


def create_session(request: Request, username: str) -> None:
    """Create a new authenticated session."""
    request.session.clear()
    request.session["authenticated"] = True
    request.session["username"] = username
    request.session["login_time"] = datetime.now(UTC).isoformat()

    logger.info("User logged in", extra={"username": username})


async def revoke_sessions(request: Request) -> None:
    """Log out: clear this browser's cookie, and revoke every session on the server.

    Only a caller whose signed cookie is in date stamps the revocation, so an anonymous request
    cannot sign the operator out. When the last logout cannot be checked, the cookie alone decides,
    because a session store that is down must fail this logout loudly, never skip the stamp.

    Raises SessionStoreError when the stamp cannot be written: the cookie is cleared, but other
    sessions are NOT revoked, and the caller must say so.
    """
    try:
        username = await _authenticated_user(request)
    except SessionStoreError:
        # The stamp cannot be read (the database is down): the cookie alone decides whether this
        # caller was signed in, and the write below fails loudly while the database is down
        username = await _authenticated_user(request, check_logout=False)
    request.session.clear()
    if username is not None:
        try:
            await write_sessions_valid_after(datetime.now(UTC))
        except (SQLAlchemyError, OSError) as exc:
            raise SessionStoreError("the logout stamp cannot be written") from exc
        logger.info("User logged out", extra={"username": username})
