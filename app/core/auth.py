import logging
import secrets
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.core.request_route import route_template
from app.core.safe_redirect import safe_redirect_target

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
# so clearing it in one browser leaves a copied cookie valid until it expires. Logout stamps this
# key with the time, and a session that logged in before the stamp is refused: one dashboard
# account, so a logout signs out every session.
SESSIONS_VALID_AFTER_KEY = "dashboard:sessions_valid_after"


class SessionStoreError(Exception):
    """The logout stamp cannot be read or written: no session is accepted, no logout is complete."""


async def _sessions_valid_after() -> datetime:
    """The time before which every session is refused: the last logout's.

    A missing stamp is not "never logged out": prod Redis keeps no AOF, so a crash can bring it back
    from a snapshot older than the last logout, and reading the gap as no logout revived every
    copied cookie still in date. The first reader writes the stamp as now (SET NX) instead, so a
    lost stamp signs everyone out once. Login asks this before it creates its session
    (session_store_available), so its own login time lands after the stamp.

    Raises SessionStoreError when Redis is unreachable or the stamp is not an aware ISO time, so
    the caller fails closed: a session that cannot be checked against the last logout is refused.
    """
    redis_client = await get_redis_client()
    if redis_client is None:
        raise SessionStoreError("Redis is unavailable; logouts cannot be checked")
    try:
        stamp = await redis_client.get(SESSIONS_VALID_AFTER_KEY)
        if stamp is None:
            await redis_client.set(
                SESSIONS_VALID_AFTER_KEY, datetime.now(UTC).isoformat(), nx=True
            )
            # NX: a concurrent reader may have written first; every reader uses the one stored
            stamp = await redis_client.get(SESSIONS_VALID_AFTER_KEY)
    except RedisError as exc:
        raise SessionStoreError("the logout stamp cannot be read") from exc
    if stamp is None:
        raise SessionStoreError("the logout stamp could not be written")
    # The client decodes responses (redis_client.py); bytes would mean it was built without that
    text = stamp.decode() if isinstance(stamp, bytes) else stamp
    try:
        valid_after = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SessionStoreError("the logout stamp is not an ISO time") from exc
    if valid_after.tzinfo is None:
        raise SessionStoreError("the logout stamp has no timezone")
    return valid_after


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
    if login_dt <= await _sessions_valid_after():
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
        # The stamp cannot be read (Redis down, or a malformed stamp): the cookie alone decides
        # whether this caller was signed in, and the write below fails loudly if Redis is down
        username = await _authenticated_user(request, check_logout=False)
    request.session.clear()
    if username is not None:
        redis_client = await get_redis_client()
        if redis_client is None:
            raise SessionStoreError(
                "Redis is unavailable; the logout cannot be recorded"
            )
        try:
            await redis_client.set(
                SESSIONS_VALID_AFTER_KEY, datetime.now(UTC).isoformat()
            )
        except RedisError as exc:
            raise SessionStoreError("the logout stamp cannot be written") from exc
        logger.info("User logged out", extra={"username": username})
