"""DashboardViewService — the web seam for the dashboard pages and HTMX partials.

Each method assembles one template's whole context from AnalyticsCoreService (the core business
logic): the filter values the template echoes back, the branch a page takes (the journey needs an
app, search replaces paging), and the 404 for a missing event. Routers render; they compose
nothing. Architecture: Router -> DashboardViewService -> AnalyticsCoreService -> CRUD -> DB
"""

from typing import Any

from fastapi import HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core.analytics_core_service import AnalyticsCoreService

# The cookie the nav app selector sets (POST /set-app-context).
APP_CONTEXT_COOKIE = "analytics_app_id"

# A text search shows one page of this many matches instead of the paged list.
SEARCH_LIMIT = 50

Context = dict[str, Any]


def selected_app_id(request: Request, app_id: str | None = Query(None)) -> str | None:
    """The app a dashboard page is scoped to: ?app_id= first, then the nav selector's cookie."""
    if app_id:
        return app_id
    return request.cookies.get(APP_CONTEXT_COOKIE) or None


class DashboardViewService:
    def __init__(self, db: AsyncSession) -> None:
        self._core = AnalyticsCoreService(db)

    @staticmethod
    def _filtered(app_id: str | None, hours: int, data: Context) -> Context:
        """A page's data plus the filters it echoes back into its time pills and app scope."""
        return {"app_id": app_id, "hours": hours, **data}

    # ── Nav ──────────────────────────────────────────────────────────────

    async def apps_dropdown_context(self, request: Request) -> Context:
        return {
            "apps": await self._core.get_apps_for_dropdown(),
            "current_app_id": request.cookies.get(APP_CONTEXT_COOKIE, ""),
        }

    # ── Overview ─────────────────────────────────────────────────────────

    async def overview_context(self, app_id: str | None, hours: int) -> Context:
        stats = await self._core.get_stats_overview(app_id=app_id, hours=hours)
        return self._filtered(app_id, hours, stats)

    async def timeline_context(self, app_id: str | None, hours: int) -> Context:
        return {
            "timeline_data": await self._core.get_timeline_data(
                app_id=app_id, hours=hours
            )
        }

    async def event_types_context(self, app_id: str | None, hours: int) -> Context:
        stats = await self._core.get_stats_overview(app_id=app_id, hours=hours)
        return {"top_events": stats.get("top_events", [])}

    async def top_screens_context(self, app_id: str | None, hours: int) -> Context:
        features = await self._core.get_feature_analytics(app_id=app_id, hours=hours)
        return {"screens": features.get("screens", [])}

    # ── Events ───────────────────────────────────────────────────────────

    async def events_context(
        self,
        app_id: str | None,
        event_name: str | None,
        hours: int,
        page: int,
        search_q: str | None = None,
    ) -> Context:
        """The events list: a text search shows one page of matches, otherwise the paged list."""
        if search_q:
            events = await self._core.search_events(
                query=search_q, search_type="event_name", limit=SEARCH_LIMIT
            )
            result: Context = {
                "events": events,
                "pagination": {
                    "page": 1,
                    "total_pages": 1,
                    "total": len(events),
                    "start": 1,
                    "end": len(events),
                    "per_page": SEARCH_LIMIT,
                },
            }
        else:
            result = await self._core.get_filtered_events(
                app_id=app_id, event_name=event_name, hours=hours, page=page
            )
        return {**self._filtered(app_id, hours, result), "event_name": event_name}

    async def event_detail_context(self, event_id: str) -> Context:
        event = await self._core.get_event_by_id(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        return {"event": event}

    # ── Analytics pages (page and its swappable content share one context) ───

    async def devices_context(self, app_id: str | None, hours: int) -> Context:
        data = await self._core.get_device_analytics(app_id=app_id, hours=hours)
        return self._filtered(app_id, hours, data)

    async def errors_context(self, app_id: str | None, hours: int) -> Context:
        data = await self._core.get_error_analytics(app_id=app_id, hours=hours)
        return self._filtered(app_id, hours, data)

    async def performance_context(self, app_id: str | None, hours: int) -> Context:
        data = await self._core.get_performance_analytics(app_id=app_id, hours=hours)
        return self._filtered(app_id, hours, data)

    async def features_context(self, app_id: str | None, hours: int) -> Context:
        data = await self._core.get_feature_analytics(app_id=app_id, hours=hours)
        return self._filtered(app_id, hours, data)

    async def feedback_context(self, app_id: str | None, hours: int) -> Context:
        data = await self._core.get_feedback_analytics(app_id=app_id, hours=hours)
        return self._filtered(app_id, hours, data)

    async def journey_context(self, app_id: str | None, hours: int) -> Context:
        """Journeys need one app: a cross-app Sankey flow is meaningless."""
        if not app_id:
            return self._filtered(app_id, hours, {"requires_app": True})
        data = await self._core.get_user_journey_analytics(app_id=app_id, hours=hours)
        return self._filtered(app_id, hours, data)

    # ── Explorer ─────────────────────────────────────────────────────────

    async def explorer_context(
        self, app_id: str | None, hours: int, event_name: str | None
    ) -> Context:
        data = await self._core.get_metadata_analysis(
            app_id=app_id, hours=hours, event_name=event_name
        )
        return {**self._filtered(app_id, hours, data), "event_name": event_name}

    async def key_deep_dive_context(
        self, key_name: str, app_id: str | None, hours: int
    ) -> Context:
        data = await self._core.get_key_deep_dive(key_name, app_id=app_id, hours=hours)
        return self._filtered(app_id, hours, data)

    # ── Slider panels ────────────────────────────────────────────────────

    async def user_profile_context(self, user_id: str) -> Context:
        return {**await self._core.get_user_profile(user_id)}

    async def session_detail_context(self, session_id: str) -> Context:
        return {**await self._core.get_session_detail(session_id)}
