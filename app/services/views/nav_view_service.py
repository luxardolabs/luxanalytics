"""NavViewService — the top nav's view model: every link resolved by route name, ready to render.

Every page extends layouts/base.html, which includes the nav, so the shared templates instance
injects it into each render (`nav_context`, a context processor in app/web/templates.py). The
template renders the finished strings and holds no route knowledge (luxarch --playbook url-in-view).
"""

from typing import Any

from fastapi import Request

from app.core.tracing import create_service_span

# (route name, label), in nav order
_PAGES = (
    ("dashboard.overview.page", "Overview"),
    ("dashboard.events.page", "Events"),
    ("dashboard.devices.page", "Devices"),
    ("dashboard.features.page", "Features"),
    ("dashboard.journey.page", "Journey"),
    ("dashboard.errors.page", "Errors"),
    ("dashboard.performance.page", "Performance"),
    ("dashboard.feedback.page", "Feedback"),
    ("dashboard.explorer.page", "Explorer"),
)


class NavViewService:
    def __init__(self, request: Request) -> None:
        self._request = request

    def _path(self, route: str) -> str:
        return str(self._request.app.url_path_for(route))

    def context(self) -> dict[str, Any]:
        """`nav` for the layout: the page links (each marked active when the request is under it),
        the app selector's source, and the right-hand links."""
        with create_service_span("NavViewService", "context"):
            current = self._request.url.path
            pages = []
            for name, label in _PAGES:
                url = self._path(name)
                pages.append(
                    {"label": label, "url": url, "active": current.startswith(url)}
                )
            return {
                "nav": {
                    "home": self._path("dashboard.overview.page"),
                    "apps_dropdown": self._path("dashboard.apps_dropdown"),
                    "pages": pages,
                    "apps": self._path("apps.page"),
                    "health": self._path("system.health"),
                    "logout": self._path("auth.logout"),
                    # app.js sends an htmx 401 here (read from the body's data-login-url)
                    "login": self._path("auth.login_page"),
                }
            }


def nav_context(request: Request) -> dict[str, Any]:
    """The templates' context processor: Starlette calls it with the request on every render."""
    return NavViewService(request).context()
