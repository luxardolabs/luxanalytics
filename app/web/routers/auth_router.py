import structlog
from typing import Optional
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.auth import (create_session, destroy_session, get_current_user,
                           verify_credentials)
from app.core.config import settings
from app.web.templates import templates

logger = structlog.get_logger()
router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request, next: str = "/dashboard/overview", error: Optional[str] = None, message: Optional[str] = None
):
    """Display login page."""
    # Check if already authenticated
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url=next, status_code=303)

    return templates.TemplateResponse(
        "pages/auth/login.html",
        {
            "request": request,
            "next": next,
            "error": error,
            "message": message,
            "version": settings.APP_VERSION,
            "build_timestamp": settings.BUILD_TIMESTAMP,
        },
    )


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    """Process login form."""
    # Verify credentials
    if not verify_credentials(username, password):
        logger.warning("Failed login attempt", username=username)
        return RedirectResponse(
            url=f"/login?next={next}&error=Invalid username or password",
            status_code=303,
        )

    # Create session
    create_session(request, username)

    # Redirect to requested page
    return RedirectResponse(url=next, status_code=303)


@router.get("/logout")
async def logout(request: Request):
    """Log out and clear session."""
    destroy_session(request)

    return RedirectResponse(
        url="/login?message=You have been logged out", status_code=303
    )
