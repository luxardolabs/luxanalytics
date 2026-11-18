"""The request's path and query as the client sent them."""

from urllib.parse import quote

from starlette.types import Scope

# What a path keeps unencoded when the server gave no raw path: the RFC 3986 path characters.
_PATH_SAFE = "/!$&'()*+,;=:@~"


def _as_sent(raw: bytes) -> str:
    """Bytes as URL text: printable ASCII as it is, every other byte percent-encoded. A client that
    sends UTF-8 unencoded (curl does) keeps its bytes; read as latin-1 and quoted again, they would
    be encoded twice."""
    return "".join(chr(b) if 0x21 <= b <= 0x7E else f"%{b:02X}" for b in raw)


def requested_path(scope: Scope) -> str:
    """The path as requested, its percent-encoding kept (`/user/a%3Fb`, `/user/a%2Fb`). The
    decoded `scope["path"]` turns an id's `?`, `#` or `/` into URL syntax, so a URL built back from
    it reaches another row; this is the path to send a browser back to."""
    raw = scope.get("raw_path")
    if isinstance(raw, bytes):
        # A raw `?` can only begin the query, which some servers leave on raw_path.
        return _as_sent(raw.partition(b"?")[0])
    path: str = scope["path"]
    return quote(path, safe=_PATH_SAFE)


def with_requested_query(path: str, scope: Scope) -> str:
    """`path` plus the query string as sent. Not `request.url.query`: Starlette builds that URL
    from the decoded path, so an id's `?` moves the rest of the id into the query."""
    query: bytes = scope.get("query_string", b"")
    return f"{path}?{_as_sent(query)}" if query else path
