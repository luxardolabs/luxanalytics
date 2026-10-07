"""DashboardViewService — the web seam for the dashboard pages and HTMX partials.

Each method assembles one template's whole context from AnalyticsCoreService (the core business
logic): the filter values the template echoes back, the branch a page takes (the journey needs an
app, search replaces paging), and the 404 for a missing event. Routers render; they compose
nothing. Architecture: Router -> DashboardViewService -> AnalyticsCoreService -> CRUD -> DB
"""

from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import build_pagination, skip_limit
from app.core.tracing import create_service_span
from app.services.core.analytics_core_service import AnalyticsCoreService

# The cookie the nav app selector sets (POST /set-app-context).
APP_CONTEXT_COOKIE = "analytics_app_id"

# A text search shows one page of this many matches instead of the paged list.
SEARCH_LIMIT = 50

# Rows per page of the events list.
EVENTS_PER_PAGE = 50

Context = dict[str, Any]


def selected_app_id(request: Request, app_id: str | None = Query(None)) -> str | None:
    """The app a dashboard page is scoped to: ?app_id= first, then the nav selector's cookie."""
    if app_id:
        return app_id
    return request.cookies.get(APP_CONTEXT_COOKIE) or None


def switch_app_response(current_url: str, app_id: str) -> Response:
    """The nav app selector's answer: set (or clear) the app cookie, and send htmx back to the page
    it was on (HX-Current-URL) minus any ?app_id=, which would override the new cookie.
    Server-driven; no reload in the client (luxarch --playbook hypermedia)."""
    current = urlsplit(current_url)
    path = current.path if current.path.startswith("/") else "/dashboard/overview"
    query = [(k, v) for k, v in parse_qsl(current.query) if k != "app_id"]
    target = f"{path}?{urlencode(query)}" if query else path
    response = Response(status_code=204, headers={"HX-Redirect": target})
    if app_id:
        response.set_cookie(
            APP_CONTEXT_COOKIE,
            app_id,
            max_age=86400 * 30,
            secure=True,
            httponly=True,
            samesite="lax",
        )
    else:
        response.delete_cookie(APP_CONTEXT_COOKIE)
    return response


class DashboardViewService:
    def __init__(self, db: AsyncSession, request: Request) -> None:
        self._core = AnalyticsCoreService(db)
        self._request = request

    def _path(
        self, route: str, query: dict[str, Any] | None = None, **params: str
    ) -> str:
        """A route's PATH resolved by name, plus an encoded query with empty values dropped
        (luxarch --playbook url-in-view). A path, not an absolute URL: behind the proxy the request
        scheme can read http, and an http:// link inside the https dashboard is blocked."""
        path = self._request.app.url_path_for(route, **params)
        kept = {k: v for k, v in (query or {}).items() if v not in (None, "")}
        return f"{path}?{urlencode(kept)}" if kept else path

    def _detail_urls(self, ids: Iterable[object]) -> dict[str, str]:
        return {str(i): self._path("event_detail_panel", event_id=str(i)) for i in ids}

    def _user_urls(self, user_ids: Iterable[str | None]) -> dict[str, str]:
        return {u: self._path("user_profile_panel", user_id=u) for u in user_ids if u}

    def _session_urls(self, session_ids: Iterable[str | None]) -> dict[str, str]:
        return {
            s: self._path("session_detail_panel", session_id=s)
            for s in session_ids
            if s
        }

    @staticmethod
    def _filtered(app_id: str | None, hours: int, data: Context) -> Context:
        """A page's data plus the filters it echoes back into its time pills and app scope."""
        return {"app_id": app_id, "hours": hours, **data}

    # ── Nav ──────────────────────────────────────────────────────────────

    async def apps_dropdown_context(self, request: Request) -> Context:
        with create_service_span("DashboardViewService", "apps_dropdown_context"):
            apps = await self._core.get_apps_for_dropdown()
            return {
                "options": [{"value": "", "label": "All Apps"}]
                + [{"value": a["app_id"], "label": a["app_id"]} for a in apps],
                "current_app_id": request.cookies.get(APP_CONTEXT_COOKIE, ""),
                "set_context_url": self._path("set_app_context"),
            }

    # ── Overview ─────────────────────────────────────────────────────────

    async def overview_context(self, app_id: str | None, hours: int) -> Context:
        with create_service_span(
            "DashboardViewService", "overview_context", app_id=app_id
        ):
            stats = await self._core.get_stats_overview(app_id=app_id, hours=hours)
            scope = {"hours": hours, "app_id": app_id}
            urls = {
                "timeline": self._path("overview_timeline", scope),
                "event_types": self._path("overview_event_types", scope),
                "top_screens": self._path("overview_top_screens", scope),
                "recent_events": self._path("events_content", scope),
            }
            event_urls = {
                e["name"]: self._path("events_page", {"event_name": e["name"], **scope})
                for e in stats.get("top_events", [])
            }
            return self._filtered(
                app_id, hours, {**stats, "urls": urls, "event_urls": event_urls}
            )

    async def timeline_context(self, app_id: str | None, hours: int) -> Context:
        with create_service_span(
            "DashboardViewService", "timeline_context", app_id=app_id
        ):
            return {
                "timeline_data": await self._core.get_timeline_data(
                    app_id=app_id, hours=hours
                )
            }

    async def event_types_context(self, app_id: str | None, hours: int) -> Context:
        with create_service_span(
            "DashboardViewService", "event_types_context", app_id=app_id
        ):
            stats = await self._core.get_stats_overview(app_id=app_id, hours=hours)
            return {"top_events": stats.get("top_events", [])}

    async def top_screens_context(self, app_id: str | None, hours: int) -> Context:
        with create_service_span(
            "DashboardViewService", "top_screens_context", app_id=app_id
        ):
            features = await self._core.get_feature_analytics(
                app_id=app_id, hours=hours
            )
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
        """The events list: a text search shows one page of matches, otherwise the paged list.
        Page state is built ONCE here (build_pagination); the template only renders it."""
        with create_service_span(
            "DashboardViewService", "events_context", app_id=app_id
        ):
            if search_q:
                events = await self._core.search_events(
                    query=search_q, search_type="event_name", limit=SEARCH_LIMIT
                )
                pagination = build_pagination(
                    page=1, total=len(events), per_page=SEARCH_LIMIT
                )
            else:
                skip, limit = skip_limit(page, EVENTS_PER_PAGE)
                events, total = await self._core.get_filtered_events(
                    skip=skip,
                    limit=limit,
                    app_id=app_id,
                    event_name=event_name,
                    hours=hours,
                )
                pagination = build_pagination(
                    page=page, total=total, per_page=EVENTS_PER_PAGE
                )
            # The App column only when the list spans every app.
            columns: list[str | dict[str, str]] = [] if app_id else ["App"]
            columns += ["Event", "User", "Session", "Device", "OS", "Key Data", "Time"]
            columns.append({"label": "", "class": "w-12"})
            return {
                **self._filtered(
                    app_id, hours, {"events": events, "pagination": pagination}
                ),
                "columns": columns,
                "event_name": event_name,
                # Page links go to the full events PAGE (it takes ?page= and the same filters); the
                # content route is an HTMX fragment and must not be navigated to.
                "pagination_base_url": self._path("events_page"),
                "query_params": {
                    k: str(v)
                    for k, v in {
                        "hours": hours,
                        "app_id": app_id,
                        "event_name": event_name,
                    }.items()
                    if v not in (None, "")
                },
                "user_urls": self._user_urls(e.user_id for e in events),
                "session_urls": self._session_urls(e.session_id for e in events),
                "detail_urls": self._detail_urls(e.id for e in events),
            }

    async def event_detail_context(self, event_id: str) -> Context:
        with create_service_span(
            "DashboardViewService", "event_detail_context", event_id=event_id
        ):
            event = await self._core.get_event_by_id(event_id)
            if not event:
                raise HTTPException(status_code=404, detail="Event not found")
            return {"event": event}

    # ── Analytics pages (page and its swappable content share one context) ───

    async def devices_context(self, app_id: str | None, hours: int) -> Context:
        with create_service_span(
            "DashboardViewService", "devices_context", app_id=app_id
        ):
            data = await self._core.get_device_analytics(app_id=app_id, hours=hours)
            return self._filtered(app_id, hours, data)

    async def errors_context(self, app_id: str | None, hours: int) -> Context:
        with create_service_span(
            "DashboardViewService", "errors_context", app_id=app_id
        ):
            data = await self._core.get_error_analytics(app_id=app_id, hours=hours)
            return self._filtered(app_id, hours, data)

    async def performance_context(self, app_id: str | None, hours: int) -> Context:
        with create_service_span(
            "DashboardViewService", "performance_context", app_id=app_id
        ):
            data = await self._core.get_performance_analytics(
                app_id=app_id, hours=hours
            )
            return self._filtered(app_id, hours, data)

    async def features_context(self, app_id: str | None, hours: int) -> Context:
        with create_service_span(
            "DashboardViewService", "features_context", app_id=app_id
        ):
            data = await self._core.get_feature_analytics(app_id=app_id, hours=hours)
            return self._filtered(app_id, hours, data)

    async def feedback_context(self, app_id: str | None, hours: int) -> Context:
        with create_service_span(
            "DashboardViewService", "feedback_context", app_id=app_id
        ):
            data = await self._core.get_feedback_analytics(app_id=app_id, hours=hours)
            detail_urls = self._detail_urls(fb.id for fb in data["recent_feedback"])
            return self._filtered(app_id, hours, {**data, "detail_urls": detail_urls})

    async def journey_context(self, app_id: str | None, hours: int) -> Context:
        """Journeys need one app: a cross-app Sankey flow is meaningless."""
        with create_service_span(
            "DashboardViewService", "journey_context", app_id=app_id
        ):
            if not app_id:
                return self._filtered(app_id, hours, {"requires_app": True})
            data = await self._core.get_user_journey_analytics(
                app_id=app_id, hours=hours
            )
            # The heatmap grows a row per screen; its height is layout data the view decides.
            screens = (data.get("transition_heatmap") or {}).get("screens", [])
            heatmap_height = 80 + 32 * len(screens)
            return self._filtered(
                app_id,
                hours,
                {**data, "requires_app": False, "heatmap_height": heatmap_height},
            )

    # ── Explorer ─────────────────────────────────────────────────────────

    async def explorer_context(
        self, app_id: str | None, hours: int, event_name: str | None
    ) -> Context:
        with create_service_span(
            "DashboardViewService", "explorer_context", app_id=app_id
        ):
            data = await self._core.get_metadata_analysis(
                app_id=app_id, hours=hours, event_name=event_name
            )
            scope = {"hours": hours, "app_id": app_id}
            key_urls = {
                k: self._path("key_deep_dive_panel", scope, key_name=k)
                for k in data["all_keys"]
            }
            return {
                **self._filtered(app_id, hours, data),
                "event_name": event_name,
                "key_urls": key_urls,
            }

    async def key_deep_dive_context(
        self, key_name: str, app_id: str | None, hours: int
    ) -> Context:
        with create_service_span(
            "DashboardViewService", "key_deep_dive_context", app_id=app_id
        ):
            data = await self._core.get_key_deep_dive(
                key_name, app_id=app_id, hours=hours
            )
            return self._filtered(app_id, hours, data)

    # ── Slider panels ────────────────────────────────────────────────────

    async def user_profile_context(self, user_id: str) -> Context:
        with create_service_span(
            "DashboardViewService", "user_profile_context", user_id=user_id
        ):
            profile = await self._core.get_user_profile(user_id)
            session_urls = self._session_urls(
                s["session_id"] for s in profile.get("sessions", [])
            )
            return {**profile, "session_urls": session_urls}

    async def session_detail_context(self, session_id: str) -> Context:
        """A session with no events renders the panel's empty state, not a stats grid of
        values the core never computed (it returns only the id for an empty session)."""
        with create_service_span(
            "DashboardViewService", "session_detail_context", session_id=session_id
        ):
            detail = await self._core.get_session_detail(session_id)
            user_id = detail.get("user_id")
            user_url = (
                self._path("user_profile_panel", user_id=user_id) if user_id else None
            )
            return {**detail, "has_data": bool(detail["events"]), "user_url": user_url}
