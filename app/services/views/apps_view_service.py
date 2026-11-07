"""AppsViewService — the web seam for the apps admin surface.

Shapes form input into DTOs, calls AppCoreService, and converts what it returns into the
schemas the templates render (no ORM object reaches a template). The DSN is built here:
it is a URL, so it is assembled in the view from the ingest route's NAME
(luxarch --playbook url-in-view), never on the model.
"""

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError
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
_DSN_ROUTE = "api.events.create_for_project"


def page_context(request: Request) -> dict[str, Any]:
    """The apps page: where its list and its New App panel load from."""
    path_for = request.app.url_path_for
    return {"urls": {"list": path_for("apps.list"), "new": path_for("apps.new_panel")}}


def new_panel_context(request: Request) -> dict[str, Any]:
    """The New App form: no app yet, and the route it posts to."""
    return {"app": None, "create_url": request.app.url_path_for("apps.create")}


class AppFormError(ValueError):
    """The app form was refused; the message is what the form's error box shows."""


class DuplicateAppIdError(AppFormError):
    """An app with this app_id already exists."""


def _form_message(error: ValidationError) -> str:
    """The refused fields and why, without echoing the submitted values."""
    return "; ".join(
        f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}"
        for e in error.errors(include_input=False)
    )


def _create_form(
    name: str, app_id: str, organization: str | None, description: str | None
) -> AppCreate:
    """The New App form as a validated DTO; AppFormError names what the form got wrong."""
    try:
        return AppCreate(
            name=name,
            app_id=app_id,
            organization=organization,
            description=description,
        )
    except ValidationError as exc:
        raise AppFormError(_form_message(exc)) from exc


def _update_form(
    name: str | None,
    organization: str | None,
    description: str | None,
    is_active: bool,
) -> AppUpdate:
    """The edit form as a validated DTO, built in one call: a field the form left out stays unset
    (unchanged); AppFormError names what the form got wrong."""
    fields: dict[str, str | bool] = {"is_active": is_active}
    for field, value in (
        ("name", name),
        ("organization", organization),
        ("description", description),
    ):
        if value is not None:
            fields[field] = value
    try:
        return AppUpdate.model_validate(fields)
    except ValidationError as exc:
        raise AppFormError(_form_message(exc)) from exc


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
        dashboard = path_for("dashboard.overview.page")
        # The app routes take {app_id:path}, so a "/" in an id is part of the path; every other
        # reserved character ("?", "#", "%", "&") is percent-encoded to stay in it.
        encoded = quote(app_id, safe="/")
        return AppUrls(
            detail=path_for("apps.detail_panel", app_id=encoded),
            edit=path_for("apps.edit_panel", app_id=encoded),
            update=path_for("apps.update", app_id=encoded),
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
        """Create the app; returns the toast message. Raises AppFormError (DuplicateAppIdError
        when the app_id is taken)."""
        with create_service_span("AppsViewService", "create", app_id=app_id):
            data = _create_form(name, app_id, organization, description)
            if await self._core.create_app(data) is None:
                held = await self._core.get_app_by_app_id_including_deleted(app_id)
                if held is not None and held.deleted_at is not None:
                    raise DuplicateAppIdError(
                        f'App ID "{app_id}" belongs to a deleted app and cannot be reused'
                    )
                raise DuplicateAppIdError(f'App ID "{app_id}" already exists')
            return f"App '{name}' created"

    async def update(
        self,
        app_id: str,
        name: str | None,
        organization: str | None,
        description: str | None,
        is_active_checked: bool,
    ) -> str:
        """Apply the edit form; returns the toast message. Raises AppFormError.

        The Active toggle is a checkbox, which a browser OMITS when unchecked: absent means
        False, never "leave unchanged" (LUXANALYTI-63: a None here wrote NULL into a NOT NULL
        column, so an app could not be deactivated).
        """
        with create_service_span("AppsViewService", "update", app_id=app_id):
            update = _update_form(name, organization, description, is_active_checked)
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
