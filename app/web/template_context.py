"""Custom template response with automatic context injection.

Ensures all pages get consistent context (version, app list, user)
without requiring each router to manually pass it.
"""

from typing import Any

from fastapi.templating import Jinja2Templates
from starlette.responses import Response


class AutoContextTemplates(Jinja2Templates):
    """Extended Jinja2Templates that auto-injects common context."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._global_context: dict[str, Any] = {}

    def set_global_context(self, key: str, value: Any) -> None:
        """Set a global context variable available to all templates."""
        self._global_context[key] = value

    def TemplateResponse(  # type: ignore[override]  # noqa: N802
        self,
        name: str,
        context: dict[str, Any],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        media_type: str | None = None,
        background: Any = None,
    ) -> Response:
        # context must contain "request"

        # Inject global context (don't override what route explicitly set)
        for key, value in self._global_context.items():
            if key not in context:
                context[key] = value

        # Auto-inject version from settings
        if "version" not in context:
            try:
                from app.core.config import settings
                context["version"] = settings.APP_VERSION
            except Exception:
                context["version"] = "dev"

        return super().TemplateResponse(  # type: ignore[call-arg]
            name=name,
            context=context,
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )
