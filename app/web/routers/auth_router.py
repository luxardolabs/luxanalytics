import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.auth import (
    create_session,
    destroy_session,
    get_current_user,
    safe_redirect_target,
    verify_credentials,
)
from app.core.config import settings
from app.core.limiter import limiter
from app.web.templates import templates

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = "/dashboard/overview",
    error: str | None = None,
    message: str | None = None,
) -> Response:
    """Display login page."""
    # Check if already authenticated
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url=safe_redirect_target(next), status_code=303)

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
    next: str = Form("/"),
) -> RedirectResponse:
    """Process login form."""
    # Verify credentials
    if not verify_credentials(username, password):
        logger.warning("Failed login attempt", extra={"username": username})
        return RedirectResponse(
            url=f"/login?next={next}&error=Invalid username or password",
            status_code=303,
        )

    # Create session
    create_session(request, username)

    # Redirect to the requested page — same-site paths only (no open redirect)
    return RedirectResponse(url=safe_redirect_target(next), status_code=303)


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Log out and clear session."""
    destroy_session(request)

    return RedirectResponse(
        url="/login?message=You have been logged out", status_code=303
    )
