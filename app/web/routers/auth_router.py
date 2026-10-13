import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.auth import (
    SessionStoreError,
    create_session,
    get_current_user,
    revoke_sessions,
    session_store_available,
    verify_credentials,
)
from app.core.config import settings
from app.core.limiter import limiter
from app.core.safe_redirect import safe_redirect_target
from app.web.templates import templates

logger = logging.getLogger(__name__)
router = APIRouter()

# Where a sign-in lands when `next` is absent or not a path on this site
AFTER_LOGIN = "/dashboard/overview"


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = AFTER_LOGIN,
    error: str | None = None,
    message: str | None = None,
) -> Response:
    """Display login page."""
    # Check if already authenticated
    user = await get_current_user(request)
    if user:
        return RedirectResponse(
            url=safe_redirect_target(next, AFTER_LOGIN), status_code=303
        )

    return templates.TemplateResponse(
        request,
        "pages/auth/login.html",
        {
            "next": next,
            "error": error,
            "message": message,
            "version": settings.APP_VERSION,
            "build_timestamp": settings.BUILD_TIMESTAMP,
        },
    )


@router.post("/login")
@limiter.limit(settings.LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(AFTER_LOGIN),
) -> RedirectResponse:
    """Process login form."""
    # Verify credentials
    if not verify_credentials(username, password):
        logger.warning("Failed login attempt", extra={"username": username})
        query = urlencode({"next": next, "error": "Invalid username or password"})
        return RedirectResponse(url=f"/login?{query}", status_code=303)

    # A session the store cannot check would be refused on the very next request: say so here
    if not await session_store_available():
        query = urlencode(
            {"next": next, "error": "Sign-in is unavailable: the session store is down"}
        )
        return RedirectResponse(url=f"/login?{query}", status_code=303)

    # Create session
    create_session(request, username)

    # Redirect to the requested page — same-site paths only (no open redirect)
    return RedirectResponse(
        url=safe_redirect_target(next, AFTER_LOGIN), status_code=303
    )


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Log out: every session is revoked on the server, not only this browser's cookie.

    A POST, behind the cross-site guard: a link on another site cannot sign the operator out.
    """
    try:
        await revoke_sessions(request)
    except SessionStoreError:
        logger.exception("Logout could not revoke the other sessions")
        query = urlencode(
            {
                "error": "Signed out in this browser only: the session store is down, "
                "so other sessions were not revoked"
            }
        )
        return RedirectResponse(url=f"/login?{query}", status_code=303)

    return RedirectResponse(
        url="/login?message=You have been logged out", status_code=303
    )
