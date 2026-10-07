"""AppsViewService — the web seam for the apps admin surface.

Shapes form input into DTOs, calls AppCoreService, and converts what it returns into the
schemas the templates render (no ORM object reaches a template). The DSN is built here:
it is a URL, so it is assembled in the view from the ingest route's NAME
(luxarch --playbook url-in-view), never on the model.
"""

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tracing import create_service_span
from app.schemas.app_schema import (
    AppCreate,
    AppListItem,
    AppResponse,
    AppRow,
    AppUpdate,
    AppUrls,
)
from app.services.core.app_core_service import AppCoreService
from app.web.templates import templates

# The ingest route a DSN points at (app/api/v1/routers/events_router.py).
_DSN_ROUTE = "create_events_by_project_id"


class DuplicateAppIdError(ValueError):
    """An app with this app_id already exists."""


class AppsViewService:
    def __init__(self, db: AsyncSession, request: Request) -> None:
        self._core = AppCoreService(db)
        self._request = request

    def _dsn(self, app: AppRow) -> str:
        """https://PUBLIC_ID@host/api/v1/events/PROJECT_ID, on the public base URL."""
        path = self._request.app.url_path_for(_DSN_ROUTE, project_id=app.project_id)
        base = urlsplit(settings.EXTERNAL_URL or str(self._request.base_url))
        return f"{base.scheme}://{app.public_id}@{base.netloc}{path}"

    def _urls(self, app_id: str) -> AppUrls:
        """Paths resolved by route NAME (luxarch --playbook url-in-view). Paths, not absolute URLs:
        behind the proxy the request's scheme can read http, and an http:// link in an https page
        is blocked as mixed content."""
        path_for = self._request.app.url_path_for
        dashboard = path_for("overview_page")
        return AppUrls(
            detail=path_for("app_detail_panel", app_id=app_id),
            edit=path_for("edit_app_panel", app_id=app_id),
            update=path_for("update_app", app_id=app_id),
            dashboard=f"{dashboard}?{urlencode({'app_id': app_id})}",
        )

    def _response(self, app: AppRow) -> AppResponse:
        return AppResponse(
            **app.model_dump(), dsn=self._dsn(app), urls=self._urls(app.app_id)
        )

    async def _get(self, app_id: str) -> AppRow:
        with create_service_span("AppsViewService", "_get", app_id=app_id):
            app = await self._core.get_app_by_app_id(app_id)
            if app is None:
                raise HTTPException(404, "App not found")
            return app

    async def list_context(self, q: str = "") -> dict[str, Any]:
        """The apps list partial: every app (inactive included) with its event count."""
        with create_service_span("AppsViewService", "list_context"):
            apps = (
                await self._core.search_apps(q)
                if q
                else await self._core.get_all_apps(include_inactive=True)
            )
            counts = await self._core.get_event_counts([a.app_id for a in apps])
            items = []
            for app in apps:
                items.append(
                    AppListItem(
                        app_id=app.app_id,
                        name=app.name,
                        organization=app.organization,
                        is_active=app.is_active,
                        public_id=app.public_id,
                        dsn=self._dsn(app),
                        event_count=counts.get(app.app_id, 0),
                        urls=self._urls(app.app_id),
                    )
                )
            return {"apps": items}

    async def edit_panel_context(self, app_id: str) -> dict[str, Any]:
        with create_service_span(
            "AppsViewService", "edit_panel_context", app_id=app_id
        ):
            return {"app": self._response(await self._get(app_id))}

    async def detail_panel_context(self, app_id: str) -> dict[str, Any]:
        with create_service_span(
            "AppsViewService", "detail_panel_context", app_id=app_id
        ):
            app = await self._get(app_id)
            stats = await self._core.get_app_stats(app_id)
            return {"app": self._response(app), "stats": stats}

    async def create(
        self,
        name: str,
        app_id: str,
        organization: str | None,
        description: str | None,
    ) -> str:
        """Create the app; returns the toast message. Raises DuplicateAppIdError."""
        with create_service_span("AppsViewService", "create", app_id=app_id):
            if await self._core.get_app_by_app_id(app_id):
                raise DuplicateAppIdError(f'App ID "{app_id}" already exists')
            await self._core.create_app(
                AppCreate(
                    name=name,
                    app_id=app_id,
                    organization=organization,
                    description=description,
                )
            )
            return f"App '{name}' created"

    async def update(
        self,
        app_id: str,
        name: str | None,
        organization: str | None,
        description: str | None,
        is_active_checked: bool,
    ) -> str:
        """Apply the edit form; returns the toast message.

        The Active toggle is a checkbox, which a browser OMITS when unchecked: absent means
        False, never "leave unchanged" (LUXANALYTI-63: a None here wrote NULL into a NOT NULL
        column, so an app could not be deactivated).
        """
        with create_service_span("AppsViewService", "update", app_id=app_id):
            update = AppUpdate(is_active=is_active_checked)
            if name is not None:
                update.name = name
            if organization is not None:
                update.organization = organization
            if description is not None:
                update.description = description
            app = await self._core.update_app(app_id, update)
            if app is None:
                raise HTTPException(404, "App not found")
            return f"App '{app.name}' updated"

    async def delete(self, app_id: str) -> str:
        """Delete the app; returns the toast message."""
        with create_service_span("AppsViewService", "delete", app_id=app_id):
            app = await self._get(app_id)
            await self._core.delete_app(app_id)
            return f"App '{app.name}' deleted"

    async def list_with_toast(self, message: str) -> Response:
        """After a create/update/delete: the refreshed list, retargeted into #apps-list, toasted."""
        with create_service_span("AppsViewService", "list_with_toast"):
            response = templates.TemplateResponse(
                self._request, "partials/apps/list.html", await self.list_context()
            )
            response.headers["HX-Trigger"] = json.dumps(
                {"showtoast": {"message": message, "type": "success"}}
            )
            response.headers["HX-Retarget"] = "#apps-list"
            return response

    def form_error(self, message: str) -> Response:
        """A 400 shown in the form's own error box (the emitted htmx-error-swap honours
        HX-Error-Swap), not swapped over the apps list the form targets."""
        with create_service_span("AppsViewService", "form_error"):
            response = templates.TemplateResponse(
                self._request,
                "partials/apps/form_error.html",
                {"message": message},
                status_code=400,
            )
            response.headers["HX-Retarget"] = "#form-errors"
            response.headers["HX-Error-Swap"] = "true"
            return response

    async def export_json(self) -> tuple[bytes, str]:
        """Every app as a JSON download: (body, filename)."""
        with create_service_span("AppsViewService", "export_json"):
            apps = [
                self._response(a)
                for a in await self._core.get_all_apps(include_inactive=True)
            ]
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
            filename = f"apps_{datetime.now(UTC).strftime('%Y%m%d')}.json"
            return json.dumps(data, indent=2).encode(), filename
