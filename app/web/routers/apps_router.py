"""Apps admin (HTMX). HTTP only: every route asks AppsViewService for its context."""

import io
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_auth
from app.core.query_params import PathText, TextFilter
from app.core.route_convertors import APP_ID_PARAM
from app.db.database import get_db
from app.schemas.app_schema import AppEditForm
from app.services.views.apps_view_service import (
    AppFormError,
    AppsViewService,
    new_panel_context,
    page_context,
)
from app.web.templates import templates

router = APIRouter(dependencies=[Depends(require_auth)])


# ── Full Pages ────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse, name="apps.page")
async def apps_page(request: Request) -> Response:
    """Full apps management page."""
    return templates.TemplateResponse(
        request, "pages/apps/index.html", page_context(request)
    )


# ── HTMX Partials ────────────────────────────────────────────────────────────


@router.get("/list", response_class=HTMLResponse, name="apps.list")
async def apps_list(
    request: Request,
    q: TextFilter = None,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Apps list partial — loaded by HTMX on page load and search."""
    context = await AppsViewService(db, request).list_context(q or "")
    return templates.TemplateResponse(request, "partials/apps/list.html", context)


# ── Slider Panels ────────────────────────────────────────────────────────────


@router.get("/new", response_class=HTMLResponse, name="apps.new_panel")
async def new_app_panel(request: Request) -> Response:
    """New app form — slider panel."""
    return templates.TemplateResponse(
        request, "partials/apps/form_panel.html", new_panel_context(request)
    )


@router.get(
    f"/{APP_ID_PARAM}/edit", response_class=HTMLResponse, name="apps.edit_panel"
)
async def edit_app_panel(
    request: Request,
    app_id: PathText,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Edit app form — slider panel."""
    context = await AppsViewService(db, request).edit_panel_context(app_id)
    return templates.TemplateResponse(request, "partials/apps/form_panel.html", context)


@router.get(
    f"/{APP_ID_PARAM}/detail", response_class=HTMLResponse, name="apps.detail_panel"
)
async def app_detail_panel(
    request: Request,
    app_id: PathText,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """App detail — slider panel with DSN, stats."""
    context = await AppsViewService(db, request).detail_panel_context(app_id)
    return templates.TemplateResponse(
        request, "partials/apps/detail_panel.html", context
    )


# ── Actions ───────────────────────────────────────────────────────────────────


@router.post("/", response_class=HTMLResponse, name="apps.create")
async def create_app(
    request: Request,
    name: str = Form(""),
    app_id: str = Form(""),
    organization: str | None = Form(None),
    description: str | None = Form(None),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Create a new app, then refresh the list."""
    view = AppsViewService(db, request)
    try:
        message = await view.create(name, app_id, organization, description)
    except AppFormError as e:
        return view.form_error(str(e))
    return await view.list_with_toast(message)


@router.put(f"/{APP_ID_PARAM}", response_class=HTMLResponse, name="apps.update")
async def update_app(
    request: Request,
    app_id: PathText,
    form: Annotated[AppEditForm, Form()],
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Update an app."""
    view = AppsViewService(db, request)
    try:
        message = await view.update(app_id, form)
    except AppFormError as e:
        return view.form_error(str(e))
    return await view.list_with_toast(message)


@router.delete(f"/{APP_ID_PARAM}", response_class=HTMLResponse, name="apps.delete")
async def delete_app(
    request: Request,
    app_id: PathText,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Delete an app."""
    view = AppsViewService(db, request)
    message = await view.delete(app_id)
    return await view.list_with_toast(message)


# ── Export ────────────────────────────────────────────────────────────────────


@router.get("/export/json", name="apps.export_json")
async def export_apps_json(
    request: Request, db: AsyncSession = Depends(get_db, scope="function")
) -> StreamingResponse:
    body, filename = await AppsViewService(db, request).export_json()
    return StreamingResponse(
        io.BytesIO(body),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
