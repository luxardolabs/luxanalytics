"""A path that differs from a route only by its trailing slash: a GET or HEAD is redirected to the
route by a relative Location (behind the TLS-ending proxy the request reads http, so an absolute
one would send the browser to http://), and an unsafe method is never redirected (a 307 repeats
the method, so it would act on whatever the other path names)."""

from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from httpx import AsyncClient
from starlette.types import Message, Scope

from app.main import create_application

pytestmark = pytest.mark.db


async def _asgi_get(raw_path: bytes, query: bytes = b"") -> tuple[int, dict[str, str]]:
    """The app's answer to a GET whose raw path and query hold bytes an HTTP client would
    percent-encode (curl sends UTF-8 as it is)."""
    app = create_application()
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": unquote(raw_path.decode("utf-8")),
        "raw_path": raw_path,
        "query_string": query,
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("198.51.100.250", 1),
        "server": ("test", 443),
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in start["headers"]}
    return int(start["status"]), headers


@pytest.mark.parametrize(
    "path", ["/dashboard/events%2F/", "/dashboard/events//", "/dashboard/events%2F%2F"]
)
async def test_a_path_whose_slash_is_not_its_last_one_is_not_redirected(
    dashboard_client: AsyncClient, path: str
) -> None:
    """Only the one trailing slash is dropped, on the path as requested and as decoded alike: a
    redirect never points at a path no route serves."""
    response = await dashboard_client.get(path)

    location = response.headers.get("location")
    if location is not None:
        assert (await dashboard_client.get(location)).status_code != 404, location
    else:
        assert response.status_code == 404


@pytest.mark.parametrize(
    ("raw_path", "query", "location"),
    [
        (b"/login/", b"next=/caf\xc3\xa9", "/login?next=/caf%C3%A9"),
        (b"/login/", b"next=/caf%C3%A9", "/login?next=/caf%C3%A9"),
        (
            b"/dashboard/events/",
            b"event_name=\xc3\xa9",
            "/dashboard/events?event_name=%C3%A9",
        ),
    ],
)
async def test_a_raw_utf8_byte_is_kept_as_its_percent_encoding(
    raw_path: bytes, query: bytes, location: str
) -> None:
    status, headers = await _asgi_get(raw_path, query)

    assert status == 307
    assert headers["location"] == location


async def test_a_signed_out_raw_utf8_path_is_the_login_next_byte_for_byte() -> None:
    status, headers = await _asgi_get(b"/dashboard/user/caf\xc3\xa9")

    assert status == 303
    login = headers["location"]
    assert parse_qs(urlsplit(login).query)["next"][0] == "/dashboard/user/caf%C3%A9"


@pytest.mark.parametrize(
    ("path", "location"),
    [
        ("/dashboard/events/", "/dashboard/events"),
        (
            "/dashboard/events/?hours=24&app_id=a%2Fb",
            "/dashboard/events?hours=24&app_id=a%2Fb",
        ),
        ("/apps", "/apps/"),
        ("/dashboard/overview/?app_id=a%3Fb", "/dashboard/overview?app_id=a%3Fb"),
    ],
)
async def test_a_get_is_redirected_by_a_relative_location(
    dashboard_client: AsyncClient, path: str, location: str
) -> None:
    response = await dashboard_client.get(path)

    assert response.status_code in (307, 308), response.status_code
    assert response.headers["location"] == location


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/logout/"),
        ("POST", "/login/"),
        ("POST", "/set-app-context/"),
        ("POST", "/apps"),
        ("PUT", "/apps/test_app%2F"),
        ("DELETE", "/apps/test_app%2F"),
        ("POST", "/api/v1/events/public/"),
    ],
)
async def test_an_unsafe_method_is_never_redirected(
    dashboard_client: AsyncClient, method: str, path: str
) -> None:
    response = await dashboard_client.request(method, path)

    assert response.status_code not in (301, 302, 303, 307, 308), (
        response.status_code,
        response.headers.get("location"),
    )


@pytest.mark.parametrize(
    "path",
    [
        "//evil.example/",
        "//evil.example",
        "/\\evil.example/",
        "/%2F%2Fevil.example/",
        "/%2F/evil.example/",
        "/%5Cevil.example/",
        "/%5C%5Cevil.example/",
        "/.//evil.example/",
        "/%2e//evil.example/",
        "/static//evil.example",
        "/dashboard/events/?u=//evil.example",
        "/dashboard/events/?u=https://evil.example@x",
        "/dashboard/events/?@evil.example",
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_a_slash_redirect_never_leaves_the_site(
    dashboard_client: AsyncClient, path: str, method: str
) -> None:
    response = await dashboard_client.request(method, path)

    location = response.headers.get("location")
    if location is None:
        return
    assert location.startswith("/") and not location.startswith(("//", "/\\"))
    assert "\\" not in location.split("?")[0], location
    assert urlsplit(location).netloc == "" and urlsplit(location).scheme == ""


@pytest.mark.parametrize(
    "raw_path",
    [
        b"/dashboard/events\t/",
        b"/dashboard/events\r\nX-Evil: 1/",
        b"/\t/evil.example/dashboard/events/",
    ],
)
async def test_a_control_byte_in_the_path_reaches_no_header(raw_path: bytes) -> None:
    _, headers = await _asgi_get(raw_path)

    assert "x-evil" not in headers
    assert not any(c in headers.get("location", "") for c in "\t\r\n")


async def test_a_head_is_redirected_like_a_get(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.head("/dashboard/events/")

    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard/events"


async def test_a_method_override_does_not_get_a_post_redirected(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.post(
        "/apps", headers={"X-HTTP-Method-Override": "GET"}, data={"_method": "GET"}
    )

    assert response.status_code not in (301, 302, 303, 307, 308)


async def test_a_redirected_path_is_served(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get("/dashboard/events/")

    assert (await dashboard_client.get(response.headers["location"])).status_code == 200
