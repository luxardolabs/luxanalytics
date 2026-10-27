"""What a log line may say about a request's path: its template, not the URL.

Dashboard URLs carry SDK end-user ids (`/dashboard/user/{user_id}`, `?search_q=`), so a log
records `/dashboard/user/{user_id}`, never the id or the query string.
"""

from starlette.requests import Request

# A path no route matched is logged as this: a mistyped or hand-built URL can hold an id too
UNMATCHED = "(no route)"


def route_template(request: Request) -> str:
    """The matched request's path with each path parameter's value replaced by its name, and no
    query string; UNMATCHED when no route matched.

    Built from the scope's decoded path, the one routing matched against: an id that holds `?` or
    `#` (the dashboard percent-encodes them) is part of it, where `request.url` would cut the path
    there. Not the route object's own path: under nested routers that is relative to its router.
    """
    if request.scope.get("route") is None:
        return UNMATCHED
    path = f"{request.scope.get('root_path', '')}{request.scope.get('path', '')}"
    for name, value in request.path_params.items():
        text = str(value)
        if text:
            head, sep, tail = path.rpartition(text)
            if sep:
                path = f"{head}{{{name}}}{tail}"
    return path
