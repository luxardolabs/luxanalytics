"""What a log line may say about a request's path: its template, not the URL.

Dashboard URLs carry SDK end-user ids (`/dashboard/user/{user_id}`, `?search_q=`), so a log
records `/dashboard/user/{user_id}`, never the id or the query string.
"""

from starlette.requests import Request


def route_template(request: Request) -> str:
    """The request's path with each matched path parameter's value replaced by its name, and no
    query string. Built from the full path, because a route object under nested routers holds its
    path relative to its router."""
    path = request.url.path
    for name, value in request.path_params.items():
        text = str(value)
        if text:
            head, sep, tail = path.rpartition(text)
            if sep:
                path = f"{head}{{{name}}}{tail}"
    return path
