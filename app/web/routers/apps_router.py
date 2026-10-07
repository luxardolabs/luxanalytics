import io
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_auth
from app.db.database import get_db
from app.schemas.app_schema import AppCreate, AppUpdate
from app.services.app_service import AppService
from app.web.templates import templates

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_auth)])


# ── Full Pages ────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def apps_page(request: Request):
    """Full apps management page."""
    return templates.TemplateResponse(request, "pages/apps/index.html")


# ── HTMX Partials ────────────────────────────────────────────────────────────


@router.get("/list", response_class=HTMLResponse)
async def apps_list(request: Request, q: str = "", db: AsyncSession = Depends(get_db)):
    """Apps list partial — loaded by HTMX on page load and search."""
    app_service = AppService(db)
    if q:
        apps = await app_service.search_apps(q)
    else:
        apps = await app_service.get_all_apps(include_inactive=True)

    # Add event counts for display
    app_list = []
    for app in apps:
        stats = await app_service.get_app_stats(app.app_id)
        app_list.append(
            {
                "app_id": app.app_id,
                "name": app.name,
                "organization": app.organization,
                "is_active": app.is_active,
                "public_id": app.public_id,
                "dsn": app.dsn,
                "event_count": stats.get("total_events", 0),
            }
        )

    return templates.TemplateResponse(
        request, "partials/apps/list.html", {"apps": app_list}
    )


# ── Slider Panels ────────────────────────────────────────────────────────────


@router.get("/new", response_class=HTMLResponse)
async def new_app_panel(request: Request):
    """New app form — slider panel."""
    return templates.TemplateResponse(
        request, "partials/apps/form_panel.html", {"app": None}
    )


@router.get("/{app_id}/edit", response_class=HTMLResponse)
async def edit_app_panel(
    request: Request, app_id: str, db: AsyncSession = Depends(get_db)
):
    """Edit app form — slider panel."""
    app_service = AppService(db)
    app = await app_service.get_app_by_app_id(app_id)
    if not app:
        raise HTTPException(404, "App not found")
    return templates.TemplateResponse(
        request, "partials/apps/form_panel.html", {"app": app}
    )


@router.get("/{app_id}/detail", response_class=HTMLResponse)
async def app_detail_panel(
    request: Request, app_id: str, db: AsyncSession = Depends(get_db)
):
    """App detail — slider panel with DSN, stats."""
    app_service = AppService(db)
    app = await app_service.get_app_by_app_id(app_id)
    if not app:
        raise HTTPException(404, "App not found")
    stats = await app_service.get_app_stats(app_id)
    return templates.TemplateResponse(
        request,
        "partials/apps/detail_panel.html",
        {"app": app, "stats": stats},
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
):
    """Create a new app, then refresh the list."""
    app_service = AppService(db)

    existing = await app_service.get_app_by_app_id(app_id)
    if existing:
        return HTMLResponse(
            content=f'<div class="rounded-lg border border-danger-500/30 bg-danger-500/10 px-4 py-3 text-sm text-danger-400">App ID "{app_id}" already exists</div>',
            status_code=400,
        )

    app_data = AppCreate(
        name=name, app_id=app_id, organization=organization, description=description
    )
    await app_service.create_app(app_data)

    # Return updated list + close panel via HX-Trigger
    apps = await app_service.get_all_apps(include_inactive=True)
    app_list = []
    for app in apps:
        stats = await app_service.get_app_stats(app.app_id)
        app_list.append(
            {
                "app_id": app.app_id,
                "name": app.name,
                "organization": app.organization,
                "is_active": app.is_active,
                "public_id": app.public_id,
                "dsn": app.dsn,
                "event_count": stats.get("total_events", 0),
            }
        )

    response = templates.TemplateResponse(
        request, "partials/apps/list.html", {"apps": app_list}
    )
    response.headers["HX-Trigger"] = json.dumps(
        {"showtoast": {"message": f"App '{name}' created", "type": "success"}}
    )
    response.headers["HX-Retarget"] = "#apps-list"
    return response


@router.put("/{app_id}", response_class=HTMLResponse)
async def update_app(
    request: Request,
    app_id: str,
    name: str | None = Form(None),
    organization: str | None = Form(None),
    description: str | None = Form(None),
    is_active: bool | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Update an app."""
    app_service = AppService(db)
    app_update = AppUpdate(
        name=name,
        organization=organization,
        description=description,
        is_active=is_active,
    )
    app = await app_service.update_app(app_id, app_update)
    if not app:
        raise HTTPException(404, "App not found")

    # Refresh list
    apps = await app_service.get_all_apps(include_inactive=True)
    app_list = []
    for a in apps:
        stats = await app_service.get_app_stats(a.app_id)
        app_list.append(
            {
                "app_id": a.app_id,
                "name": a.name,
                "organization": a.organization,
                "is_active": a.is_active,
                "public_id": a.public_id,
                "dsn": a.dsn,
                "event_count": stats.get("total_events", 0),
            }
        )

    response = templates.TemplateResponse(
        request, "partials/apps/list.html", {"apps": app_list}
    )
    response.headers["HX-Trigger"] = json.dumps(
        {"showtoast": {"message": f"App '{app.name}' updated", "type": "success"}}
    )
    response.headers["HX-Retarget"] = "#apps-list"
    return response


@router.delete("/{app_id}", response_class=HTMLResponse)
async def delete_app(request: Request, app_id: str, db: AsyncSession = Depends(get_db)):
    """Delete an app."""
    app_service = AppService(db)
    app = await app_service.get_app_by_app_id(app_id)
    if not app:
        raise HTTPException(404, "App not found")

    await app_service.delete_app(app_id)

    apps = await app_service.get_all_apps(include_inactive=True)
    app_list = []
    for a in apps:
        stats = await app_service.get_app_stats(a.app_id)
        app_list.append(
            {
                "app_id": a.app_id,
                "name": a.name,
                "organization": a.organization,
                "is_active": a.is_active,
                "public_id": a.public_id,
                "dsn": a.dsn,
                "event_count": stats.get("total_events", 0),
            }
        )

    response = templates.TemplateResponse(
        request, "partials/apps/list.html", {"apps": app_list}
    )
    response.headers["HX-Trigger"] = json.dumps(
        {"showtoast": {"message": f"App '{app.name}' deleted", "type": "success"}}
    )
    response.headers["HX-Retarget"] = "#apps-list"
    return response


# ── Export ────────────────────────────────────────────────────────────────────


@router.get("/export/json")
async def export_apps_json(db: AsyncSession = Depends(get_db)):
    app_service = AppService(db)
    apps = await app_service.get_all_apps(include_inactive=True)
    data = [
        {
            "app_id": a.app_id,
            "name": a.name,
            "organization": a.organization,
            "public_id": a.public_id,
            "dsn": a.dsn,
            "is_active": a.is_active,
            "created_at": a.created_at.isoformat(),
            "updated_at": a.updated_at.isoformat(),
        }
        for a in apps
    ]
    return StreamingResponse(
        io.BytesIO(json.dumps(data, indent=2).encode()),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=apps_{datetime.now().strftime('%Y%m%d')}.json"
        },
    )
