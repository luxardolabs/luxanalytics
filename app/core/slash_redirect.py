"""The router's answer to a path that no route matches: a slash redirect for a GET or HEAD that
matches a route once its trailing slash is added or dropped, and a 404 otherwise.

Starlette's own slash redirect (`redirect_slashes`, off on this app) answers every method, with an
absolute Location built from the request's scheme. Behind the TLS-ending proxy that scheme reads
http, so the browser was sent to `http://`; and a 307 repeats the method, so a PUT or DELETE on a
path ending in `/` was replayed against whatever the shorter path names. This one answers only
the safe methods, with a relative Location: the path as requested, percent-encoding kept, and the
query as sent.
"""

from starlette.responses import RedirectResponse
from starlette.routing import Match, Router
from starlette.types import Receive, Scope, Send

from app.core.request_path import requested_path, with_requested_query

_SAFE_METHODS = frozenset({"GET", "HEAD"})


class SlashRedirect:
    """An ASGI app for `Router.default`: the slash redirect, else the router's own 404."""

    def __init__(self, router: Router) -> None:
        self._router = router
        self._not_found = router.not_found

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        target = self._target(scope)
        if target is None:
            await self._not_found(scope, receive, send)
            return
        await RedirectResponse(target, status_code=307)(scope, receive, send)

    def _target(self, scope: Scope) -> str | None:
        """The relative Location to redirect to, or None for a 404."""
        if scope["type"] != "http" or scope["method"] not in _SAFE_METHODS:
            return None
        path: str = scope["path"]
        root_path: str = scope.get("root_path", "")
        # The site root has no slash to add or drop.
        if not path.removeprefix(root_path).strip("/"):
            return None
        raw = requested_path(scope)
        if path.endswith("/"):
            # A slash that is an encoded %2F in the request is part of an id, not the path's end.
            if not raw.endswith("/"):
                return None
            path, raw = path.rstrip("/"), raw.rstrip("/")
        else:
            path, raw = f"{path}/", f"{raw}/"
        # `//host` and `/\host` are another site to a browser.
        if not raw.startswith("/") or raw.startswith(("//", "/\\")):
            return None
        moved = {**scope, "path": path}
        if not any(route.matches(moved)[0] != Match.NONE for route in self._router.routes):
            return None
        return with_requested_query(raw, scope)
