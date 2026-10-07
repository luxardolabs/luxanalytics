# app/routers/dashboard.py
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_auth
from app.db.database import get_db
from app.services.dashboard import DashboardService
from app.web.templates import templates

router = APIRouter(dependencies=[Depends(require_auth)])


def get_app_id(request: Request, app_id: Optional[str] = Query(None)) -> Optional[str]:
    """Read app_id from query param first, then cookie."""
    if app_id:
        return app_id
    return request.cookies.get("analytics_app_id") or None


# ── Nav Dropdown ───────────────────────────────────────────────────────────

@router.get("/apps-dropdown", response_class=HTMLResponse)
async def apps_dropdown(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Apps dropdown for nav — loaded via HTMX on every page."""
    svc = DashboardService(db)
    apps = await svc.get_apps_for_dropdown()
    current_app_id = request.cookies.get("analytics_app_id", "")
    return templates.TemplateResponse(
        "partials/dashboard/apps_dropdown.html",
        {"request": request, "apps": apps, "current_app_id": current_app_id},
    )


# ── Overview ──────────────────────────────────────────────────────────────────

@router.get("/overview", response_class=HTMLResponse)
async def overview_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Full overview page."""
    svc = DashboardService(db)
    stats = await svc.get_stats_overview(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "pages/dashboard/overview.html",
        {"request": request, "app_id": app_id, "hours": hours, **stats},
    )


@router.get("/overview/content", response_class=HTMLResponse)
async def overview_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Overview content partial — swapped by time filter."""
    svc = DashboardService(db)
    stats = await svc.get_stats_overview(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/overview_content.html",
        {"request": request, "app_id": app_id, "hours": hours, **stats},
    )


@router.get("/overview/timeline", response_class=HTMLResponse)
async def overview_timeline(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Timeline chart partial — lazy loaded."""
    svc = DashboardService(db)
    timeline_data = await svc.get_timeline_data(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/timeline_chart.html",
        {"request": request, "timeline_data": timeline_data},
    )


@router.get("/overview/event-types", response_class=HTMLResponse)
async def overview_event_types(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Event type breakdown chart — lazy loaded."""
    svc = DashboardService(db)
    stats = await svc.get_stats_overview(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/event_types_chart.html",
        {"request": request, "top_events": stats.get("top_events", [])},
    )


@router.get("/overview/top-screens", response_class=HTMLResponse)
async def overview_top_screens(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Top screens chart — lazy loaded."""
    svc = DashboardService(db)
    features = await svc.get_feature_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/top_screens_chart.html",
        {"request": request, "screens": features.get("screens", [])},
    )


# ── Events ────────────────────────────────────────────────────────────────────

@router.get("/events", response_class=HTMLResponse)
async def events_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    event_name: Optional[str] = Query(None),
    hours: int = Query(24),
    page: int = Query(1),
):
    """Full events page."""
    svc = DashboardService(db)
    result = await svc.get_filtered_events(
        app_id=app_id, event_name=event_name, hours=hours, page=page,
    )
    return templates.TemplateResponse(
        "pages/dashboard/events.html",
        {
            "request": request, **result,
            "app_id": app_id, "event_name": event_name, "hours": hours,
        },
    )


@router.get("/events/content", response_class=HTMLResponse)
async def events_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    event_name: Optional[str] = Query(None),
    search_q: Optional[str] = Query(None),
    hours: int = Query(24),
    page: int = Query(1),
):
    """Events list partial — swapped by filters."""
    svc = DashboardService(db)
    if search_q:
        events = await svc.search_events(query=search_q, search_type="event_name", limit=50)
        result = {"events": events, "pagination": {"page": 1, "total_pages": 1, "total": len(events), "start": 1, "end": len(events), "per_page": 50}}
    else:
        result = await svc.get_filtered_events(
            app_id=app_id, event_name=event_name, hours=hours, page=page,
        )
    return templates.TemplateResponse(
        "partials/dashboard/events_content.html",
        {"request": request, **result, "app_id": app_id, "hours": hours, "event_name": event_name},
    )


@router.get("/events/detail/{event_id}", response_class=HTMLResponse)
async def event_detail_panel(
    request: Request,
    event_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Event detail slider panel."""
    svc = DashboardService(db)
    event = await svc.get_event_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return templates.TemplateResponse(
        "partials/dashboard/event_detail_panel.html",
        {"request": request, "event": event},
    )


# ── Devices ───────────────────────────────────────────────────────────────────

@router.get("/devices", response_class=HTMLResponse)
async def devices_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Full devices page."""
    svc = DashboardService(db)
    analytics = await svc.get_device_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "pages/dashboard/devices.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


@router.get("/devices/content", response_class=HTMLResponse)
async def devices_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Devices content partial."""
    svc = DashboardService(db)
    analytics = await svc.get_device_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/devices_content.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


# ── Errors ────────────────────────────────────────────────────────────────────

@router.get("/errors", response_class=HTMLResponse)
async def errors_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Full errors page."""
    svc = DashboardService(db)
    analytics = await svc.get_error_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "pages/dashboard/errors.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


@router.get("/errors/content", response_class=HTMLResponse)
async def errors_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Errors content partial."""
    svc = DashboardService(db)
    analytics = await svc.get_error_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/errors_content.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


# ── Performance ───────────────────────────────────────────────────────────────

@router.get("/performance", response_class=HTMLResponse)
async def performance_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Full performance page."""
    svc = DashboardService(db)
    analytics = await svc.get_performance_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "pages/dashboard/performance.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


@router.get("/performance/content", response_class=HTMLResponse)
async def performance_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    """Performance content partial."""
    svc = DashboardService(db)
    analytics = await svc.get_performance_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/performance_content.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


# ── Features ──────────────────────────────────────────────────────────────────

@router.get("/features", response_class=HTMLResponse)
async def features_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    svc = DashboardService(db)
    analytics = await svc.get_feature_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "pages/dashboard/features.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


@router.get("/features/content", response_class=HTMLResponse)
async def features_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    svc = DashboardService(db)
    analytics = await svc.get_feature_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/features_content.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


# ── Journey ───────────────────────────────────────────────────────────────────

@router.get("/journey", response_class=HTMLResponse)
async def journey_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    if not app_id:
        return templates.TemplateResponse(
            "pages/dashboard/journey.html",
            {"request": request, "app_id": app_id, "hours": hours, "requires_app": True},
        )
    svc = DashboardService(db)
    analytics = await svc.get_user_journey_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "pages/dashboard/journey.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


@router.get("/journey/content", response_class=HTMLResponse)
async def journey_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    if not app_id:
        return templates.TemplateResponse(
            "partials/dashboard/journey_content.html",
            {"request": request, "app_id": app_id, "hours": hours, "requires_app": True},
        )
    svc = DashboardService(db)
    analytics = await svc.get_user_journey_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/journey_content.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


# ── Feedback ──────────────────────────────────────────────────────────────────

@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    svc = DashboardService(db)
    analytics = await svc.get_feedback_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "pages/dashboard/feedback.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


@router.get("/feedback/content", response_class=HTMLResponse)
async def feedback_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    svc = DashboardService(db)
    analytics = await svc.get_feedback_analytics(app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/feedback_content.html",
        {"request": request, "app_id": app_id, "hours": hours, **analytics},
    )


# ── User Profile ──────────────────────────────────────────────────────────

# ── Explorer ──────────────────────────────────────────────────────────────

@router.get("/explorer", response_class=HTMLResponse)
async def explorer_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
    event_name: Optional[str] = Query(None),
):
    svc = DashboardService(db)
    analysis = await svc.get_metadata_analysis(app_id=app_id, hours=hours, event_name=event_name)
    return templates.TemplateResponse(
        "pages/dashboard/explorer.html",
        {"request": request, "app_id": app_id, "hours": hours, "event_name": event_name, **analysis},
    )


@router.get("/explorer/content", response_class=HTMLResponse)
async def explorer_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
    event_name: Optional[str] = Query(None),
):
    svc = DashboardService(db)
    analysis = await svc.get_metadata_analysis(app_id=app_id, hours=hours, event_name=event_name)
    return templates.TemplateResponse(
        "partials/dashboard/explorer_content.html",
        {"request": request, "app_id": app_id, "hours": hours, "event_name": event_name, **analysis},
    )


@router.get("/explorer/key/{key_name}", response_class=HTMLResponse)
async def key_deep_dive_panel(
    request: Request,
    key_name: str,
    db: AsyncSession = Depends(get_db),
    app_id: Optional[str] = Depends(get_app_id),
    hours: int = Query(24),
):
    svc = DashboardService(db)
    analysis = await svc.get_key_deep_dive(key_name, app_id=app_id, hours=hours)
    return templates.TemplateResponse(
        "partials/dashboard/key_deep_dive_panel.html",
        {"request": request, "app_id": app_id, "hours": hours, **analysis},
    )


# ── User Profile ──────────────────────────────────────────────────────────

@router.get("/user/{user_id}", response_class=HTMLResponse)
async def user_profile_panel(
    request: Request,
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    """User profile slider panel."""
    svc = DashboardService(db)
    profile = await svc.get_user_profile(user_id)
    return templates.TemplateResponse(
        "partials/dashboard/user_profile_panel.html",
        {"request": request, **profile},
    )


# ── Session Detail ────────────────────────────────────────────────────────

@router.get("/session/{session_id}", response_class=HTMLResponse)
async def session_detail_panel(
    request: Request,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Session detail slider panel."""
    svc = DashboardService(db)
    detail = await svc.get_session_detail(session_id)
    return templates.TemplateResponse(
        "partials/dashboard/session_detail_panel.html",
        {"request": request, **detail},
    )
