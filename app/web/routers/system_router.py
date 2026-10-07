"""App-level web routes: the dashboard root redirect and the nav app selector.

Module-level (not defined inside create_application) so every app instance shares these route
objects, like every other router.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from app.core.auth import require_auth

router = APIRouter()

# The cookie the nav app selector sets; the dashboard view reads it (selected_app_id).
_APP_CONTEXT_COOKIE = "analytics_app_id"


@router.get("/")
async def root() -> RedirectResponse:
    """Redirect to dashboard."""
    return RedirectResponse(url="/dashboard/overview", status_code=302)


# Only the dashboard nav (apps_dropdown.html) posts here, from an authenticated page.
@router.post("/set-app-context", dependencies=[Depends(require_auth)])
async def set_app_context(request: Request) -> Response:
    """Set the current app context via cookie (used by nav app selector)."""
    form = await request.form()
    app_id = str(form.get("app_id", ""))
    response = Response(status_code=204)
    if app_id:
        response.set_cookie(
            _APP_CONTEXT_COOKIE,
            app_id,
            max_age=86400 * 30,
            secure=True,
            httponly=True,
            samesite="lax",
        )
    else:
        response.delete_cookie(_APP_CONTEXT_COOKIE)
    return response
