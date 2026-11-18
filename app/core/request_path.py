"""The request's path as the client sent it."""

from urllib.parse import quote

from starlette.types import Scope

# What a path keeps unencoded when the server gave no raw path: the RFC 3986 path characters.
_PATH_SAFE = "/!$&'()*+,;=:@~"


def requested_path(scope: Scope) -> str:
    """The path as requested, its percent-encoding kept (`/user/a%3Fb`, `/user/a%2Fb`). The
    decoded `scope["path"]` turns an id's `?`, `#` or `/` into URL syntax, so a URL built back from
    it reaches another row; this is the path to send a browser back to."""
    raw = scope.get("raw_path")
    if isinstance(raw, bytes):
        # A raw `?` can only begin the query, which some servers leave on raw_path.
        return raw.decode("latin-1").partition("?")[0]
    path: str = scope["path"]
    return quote(path, safe=_PATH_SAFE)


def with_requested_query(path: str, scope: Scope) -> str:
    """`path` plus the query string as sent. Not `request.url.query`: Starlette builds that URL
    from the decoded path, so an id's `?` moves the rest of the id into the query."""
    query: bytes = scope.get("query_string", b"")
    return f"{path}?{query.decode('latin-1')}" if query else path
