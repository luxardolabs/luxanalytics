"""Apps admin (HTMX). HTTP only: every route asks AppsViewService for its context."""

import io

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_auth
from app.db.database import get_db
from app.services.views.apps_view_service import AppsViewService, DuplicateAppIdError
from app.web.templates import templates

router = APIRouter(dependencies=[Depends(require_auth)])


# ── Full Pages ────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def apps_page(request: Request) -> Response:
    """Full apps management page."""
    return templates.TemplateResponse(request, "pages/apps/index.html")


# ── HTMX Partials ────────────────────────────────────────────────────────────


@router.get("/list", response_class=HTMLResponse)
async def apps_list(
    request: Request, q: str = "", db: AsyncSession = Depends(get_db)
) -> Response:
    """Apps list partial — loaded by HTMX on page load and search."""
    context = await AppsViewService(db, request).list_context(q)
    return templates.TemplateResponse(request, "partials/apps/list.html", context)


# ── Slider Panels ────────────────────────────────────────────────────────────


@router.get("/new", response_class=HTMLResponse)
async def new_app_panel(request: Request) -> Response:
    """New app form — slider panel."""
    return templates.TemplateResponse(
        request, "partials/apps/form_panel.html", {"app": None}
    )


@router.get("/{app_id}/edit", response_class=HTMLResponse)
async def edit_app_panel(
    request: Request, app_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    """Edit app form — slider panel."""
    context = await AppsViewService(db, request).edit_panel_context(app_id)
    return templates.TemplateResponse(request, "partials/apps/form_panel.html", context)


@router.get("/{app_id}/detail", response_class=HTMLResponse)
async def app_detail_panel(
    request: Request, app_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    """App detail — slider panel with DSN, stats."""
    context = await AppsViewService(db, request).detail_panel_context(app_id)
    return templates.TemplateResponse(
        request, "partials/apps/detail_panel.html", context
    )


# ── Actions ───────────────────────────────────────────────────────────────────


@router.post("/", response_class=HTMLResponse)
async def create_app(
    request: Request,
    name: str = Form(...),
    app_id: str = Form(...),
    organization: str | None = Form(None),
    description: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Create a new app, then refresh the list."""
    view = AppsViewService(db, request)
    try:
        message = await view.create(name, app_id, organization, description)
    except DuplicateAppIdError as e:
        # Shown in the form's own error box (the emitted htmx-error-swap honours HX-Error-Swap).
        response = templates.TemplateResponse(
            request,
            "partials/apps/form_error.html",
            {"message": str(e)},
            status_code=400,
        )
        response.headers["HX-Retarget"] = "#form-errors"
        response.headers["HX-Error-Swap"] = "true"
        return response
    return await view.list_with_toast(message)


@router.put("/{app_id}", response_class=HTMLResponse)
async def update_app(
    request: Request,
    app_id: str,
    name: str | None = Form(None),
    organization: str | None = Form(None),
    description: str | None = Form(None),
    is_active: bool = Form(False),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Update an app."""
    view = AppsViewService(db, request)
    message = await view.update(app_id, name, organization, description, is_active)
    return await view.list_with_toast(message)


@router.delete("/{app_id}", response_class=HTMLResponse)
async def delete_app(
    request: Request, app_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    """Delete an app."""
    view = AppsViewService(db, request)
    message = await view.delete(app_id)
    return await view.list_with_toast(message)


# ── Export ────────────────────────────────────────────────────────────────────


@router.get("/export/json")
async def export_apps_json(
    request: Request, db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    body, filename = await AppsViewService(db, request).export_json()
    return StreamingResponse(
        io.BytesIO(body),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
