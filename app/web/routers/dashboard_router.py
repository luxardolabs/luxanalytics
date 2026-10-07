"""Dashboard pages + HTMX partials. HTTP only: each route renders the context its view method builds."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_auth
from app.db.database import get_db
from app.services.views.dashboard_view_service import (
    DashboardViewService,
    selected_app_id,
)
from app.web.templates import templates

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/apps-dropdown", response_class=HTMLResponse)
async def apps_dropdown(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Response:
    """Apps dropdown for nav — loaded via HTMX on every page."""
    context = await DashboardViewService(db).apps_dropdown_context(request)
    return templates.TemplateResponse(
        request, "partials/dashboard/apps_dropdown.html", context
    )


@router.get("/overview", response_class=HTMLResponse)
async def overview_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).overview_context(app_id, hours)
    return templates.TemplateResponse(request, "pages/dashboard/overview.html", context)


@router.get("/overview/content", response_class=HTMLResponse)
async def overview_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).overview_context(app_id, hours)
    return templates.TemplateResponse(
        request, "partials/dashboard/overview_content.html", context
    )


@router.get("/overview/timeline", response_class=HTMLResponse)
async def overview_timeline(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).timeline_context(app_id, hours)
    return templates.TemplateResponse(
        request, "partials/dashboard/timeline_chart.html", context
    )


@router.get("/overview/event-types", response_class=HTMLResponse)
async def overview_event_types(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).event_types_context(app_id, hours)
    return templates.TemplateResponse(
        request, "partials/dashboard/event_types_chart.html", context
    )


@router.get("/overview/top-screens", response_class=HTMLResponse)
async def overview_top_screens(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).top_screens_context(app_id, hours)
    return templates.TemplateResponse(
        request, "partials/dashboard/top_screens_chart.html", context
    )


@router.get("/events", response_class=HTMLResponse)
async def events_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    event_name: str | None = Query(None),
    hours: int = Query(24),
    page: int = Query(1),
) -> Response:
    context = await DashboardViewService(db).events_context(
        app_id, event_name, hours, page
    )
    return templates.TemplateResponse(request, "pages/dashboard/events.html", context)


@router.get("/events/content", response_class=HTMLResponse)
async def events_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    event_name: str | None = Query(None),
    search_q: str | None = Query(None),
    hours: int = Query(24),
    page: int = Query(1),
) -> Response:
    context = await DashboardViewService(db).events_context(
        app_id, event_name, hours, page, search_q
    )
    return templates.TemplateResponse(
        request, "partials/dashboard/events_content.html", context
    )


@router.get("/devices", response_class=HTMLResponse)
async def devices_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).devices_context(app_id, hours)
    return templates.TemplateResponse(request, "pages/dashboard/devices.html", context)


@router.get("/devices/content", response_class=HTMLResponse)
async def devices_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).devices_context(app_id, hours)
    return templates.TemplateResponse(
        request, "partials/dashboard/devices_content.html", context
    )


@router.get("/errors", response_class=HTMLResponse)
async def errors_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).errors_context(app_id, hours)
    return templates.TemplateResponse(request, "pages/dashboard/errors.html", context)


@router.get("/errors/content", response_class=HTMLResponse)
async def errors_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).errors_context(app_id, hours)
    return templates.TemplateResponse(
        request, "partials/dashboard/errors_content.html", context
    )


@router.get("/performance", response_class=HTMLResponse)
async def performance_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).performance_context(app_id, hours)
    return templates.TemplateResponse(
        request, "pages/dashboard/performance.html", context
    )


@router.get("/performance/content", response_class=HTMLResponse)
async def performance_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).performance_context(app_id, hours)
    return templates.TemplateResponse(
        request, "partials/dashboard/performance_content.html", context
    )


@router.get("/features", response_class=HTMLResponse)
async def features_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).features_context(app_id, hours)
    return templates.TemplateResponse(request, "pages/dashboard/features.html", context)


@router.get("/features/content", response_class=HTMLResponse)
async def features_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).features_context(app_id, hours)
    return templates.TemplateResponse(
        request, "partials/dashboard/features_content.html", context
    )


@router.get("/journey", response_class=HTMLResponse)
async def journey_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).journey_context(app_id, hours)
    return templates.TemplateResponse(request, "pages/dashboard/journey.html", context)


@router.get("/journey/content", response_class=HTMLResponse)
async def journey_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).journey_context(app_id, hours)
    return templates.TemplateResponse(
        request, "partials/dashboard/journey_content.html", context
    )


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).feedback_context(app_id, hours)
    return templates.TemplateResponse(request, "pages/dashboard/feedback.html", context)


@router.get("/feedback/content", response_class=HTMLResponse)
async def feedback_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).feedback_context(app_id, hours)
    return templates.TemplateResponse(
        request, "partials/dashboard/feedback_content.html", context
    )


@router.get("/explorer", response_class=HTMLResponse)
async def explorer_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
    event_name: str | None = Query(None),
) -> Response:
    context = await DashboardViewService(db).explorer_context(app_id, hours, event_name)
    return templates.TemplateResponse(request, "pages/dashboard/explorer.html", context)


@router.get("/explorer/content", response_class=HTMLResponse)
async def explorer_content(
    request: Request,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
    event_name: str | None = Query(None),
) -> Response:
    context = await DashboardViewService(db).explorer_context(app_id, hours, event_name)
    return templates.TemplateResponse(
        request, "partials/dashboard/explorer_content.html", context
    )


@router.get("/events/detail/{event_id}", response_class=HTMLResponse)
async def event_detail_panel(
    request: Request, event_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    """Event detail slider panel."""
    context = await DashboardViewService(db).event_detail_context(event_id)
    return templates.TemplateResponse(
        request, "partials/dashboard/event_detail_panel.html", context
    )


@router.get("/explorer/key/{key_name}", response_class=HTMLResponse)
async def key_deep_dive_panel(
    request: Request,
    key_name: str,
    db: AsyncSession = Depends(get_db),
    app_id: str | None = Depends(selected_app_id),
    hours: int = Query(24),
) -> Response:
    context = await DashboardViewService(db).key_deep_dive_context(
        key_name, app_id, hours
    )
    return templates.TemplateResponse(
        request, "partials/dashboard/key_deep_dive_panel.html", context
    )


@router.get("/user/{user_id}", response_class=HTMLResponse)
async def user_profile_panel(
    request: Request, user_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    """User profile slider panel."""
    context = await DashboardViewService(db).user_profile_context(user_id)
    return templates.TemplateResponse(
        request, "partials/dashboard/user_profile_panel.html", context
    )


@router.get("/session/{session_id}", response_class=HTMLResponse)
async def session_detail_panel(
    request: Request, session_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    """Session detail slider panel."""
    context = await DashboardViewService(db).session_detail_context(session_id)
    return templates.TemplateResponse(
        request, "partials/dashboard/session_detail_panel.html", context
    )
