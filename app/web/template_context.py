"""Custom template response with automatic context injection.

Ensures all pages get consistent context (version, app list, user)
without requiring each router to manually pass it.
"""

from typing import Any

from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings


class AutoContextTemplates(Jinja2Templates):
    """Extended Jinja2Templates that auto-injects common context."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._global_context: dict[str, Any] = {}

    def set_global_context(self, key: str, value: Any) -> None:
        """Set a global context variable available to all templates."""
        self._global_context[key] = value

    def TemplateResponse(  # type: ignore[override]
        self,
        request: Request,
        name: str,
        context: dict[str, Any] | None = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
    ) -> Response:
        # Starlette 1.x canonical form: request first, then the template name.
        context = dict(context or {})

        # Inject global context (don't override what route explicitly set)
        for key, value in self._global_context.items():
            context.setdefault(key, value)

        # Auto-inject version from settings
        context.setdefault("version", settings.APP_VERSION)

        return super().TemplateResponse(
            request,
            name,
            context,
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )
