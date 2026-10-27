import logging

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
from app.services.views.auth_view_service import AuthViewService
from app.web.templates import templates

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/login", response_class=HTMLResponse, name="auth.login_page")
async def login_page(
    request: Request,
    next: str = "",
    error: str | None = None,
    message: str | None = None,
) -> Response:
    """Display login page."""
    # Check if already authenticated
    user = await get_current_user(request)
    if user:
        return RedirectResponse(
            url=AuthViewService(request).after_login(next), status_code=303
        )

    return templates.TemplateResponse(
        request,
        "pages/auth/login.html",
        AuthViewService(request).login_context(next, error, message),
    )


@router.post("/login", name="auth.login")
@limiter.limit(settings.LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
) -> RedirectResponse:
    """Process login form."""
    # Verify credentials
    if not verify_credentials(username, password):
        # Never what was typed: a password typed into the username field would land in the logs
        logger.warning("Failed login attempt")
        return RedirectResponse(
            url=AuthViewService(request).login_page(
                next=next, error="Invalid username or password"
            ),
            status_code=303,
        )

    # A session the store cannot check would be refused on the very next request: say so here
    if not await session_store_available():
        return RedirectResponse(
            url=AuthViewService(request).login_page(
                next=next,
                error="Sign-in is unavailable: the session store is down",
            ),
            status_code=303,
        )

    # Create session
    create_session(request, username)

    # Redirect to the requested page — same-site paths only (no open redirect)
    return RedirectResponse(
        url=AuthViewService(request).after_login(next), status_code=303
    )


@router.post("/logout", name="auth.logout")
async def logout(request: Request) -> RedirectResponse:
    """Log out: every session is revoked on the server, not only this browser's cookie.

    A POST, behind the cross-site guard: a link on another site cannot sign the operator out.
    """
    try:
        await revoke_sessions(request)
    except SessionStoreError:
        logger.exception("Logout could not revoke the other sessions")
        return RedirectResponse(
            url=AuthViewService(request).login_page(
                error="Signed out in this browser only: the session store is down, "
                "so other sessions were not revoked",
            ),
            status_code=303,
        )

    return RedirectResponse(
        url=AuthViewService(request).login_page(message="You have been logged out"),
        status_code=303,
    )
