"""App-level web routes: the dashboard root redirect and the nav app selector.

Module-level (not defined inside create_application) so every app instance shares these route
objects, like every other router.
"""

from fastapi import APIRouter, Depends, Form, Header
from fastapi.responses import RedirectResponse, Response

from app.core.auth import require_auth
from app.services.views.dashboard_view_service import switch_app_response

router = APIRouter()


@router.get("/")
async def root() -> RedirectResponse:
    """Redirect to dashboard."""
    return RedirectResponse(url="/dashboard/overview", status_code=302)


# Only the dashboard nav (apps_dropdown.html) posts here, from an authenticated page.
@router.post("/set-app-context", dependencies=[Depends(require_auth)])
async def set_app_context(
    app_id: str = Form(""),
    hx_current_url: str = Header("", alias="HX-Current-URL"),
) -> Response:
    """Set the current app context via cookie (used by nav app selector)."""
    return switch_app_response(hx_current_url, app_id)
