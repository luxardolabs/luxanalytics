"""The per-IP request limit does not count /static (read-only files, no database), and the
exemption is the static mount's own prefix: no other path can borrow it."""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.types import Message, Scope

from app.core.config import settings
from app.main import create_application

LIMIT = 2
ASSET = "/static/js/app.js"


@pytest.fixture(autouse=True)
def request_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", LIMIT)


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_application()), base_url="https://localhost"
    )


async def test_static_files_are_served_past_the_request_limit() -> None:
    async with _client() as client:
        codes = [(await client.get(ASSET)).status_code for _ in range(LIMIT * 5)]
        page = await client.get("/login")

    assert codes == [200] * (LIMIT * 5)
    assert page.status_code == 200  # the assets spent none of this address's budget


async def test_static_responses_carry_no_limit_headers() -> None:
    async with _client() as client:
        response = await client.get(ASSET)

    assert "x-ratelimit-limit" not in response.headers


async def test_a_page_is_still_limited() -> None:
    async with _client() as client:
        codes = [(await client.get("/login")).status_code for _ in range(LIMIT + 1)]

    assert codes == [200] * LIMIT + [429]


async def test_a_path_that_only_starts_with_static_is_still_limited() -> None:
    async with _client() as client:
        codes = [(await client.get("/staticfoo")).status_code for _ in range(LIMIT + 1)]

    assert codes == [404] * LIMIT + [429]


@pytest.mark.parametrize(
    ("raw_path", "path"),
    [
        (b"/static/../login", "/static/../login"),
        (b"/static%2F../login", "/static/../login"),
        (b"/static/%2e%2e/login", "/static/../login"),
    ],
)
async def test_a_static_prefixed_path_does_not_reach_a_limited_route(
    raw_path: bytes, path: str
) -> None:
    """Whatever such a path decodes to, it is answered by the static mount (a 404), never by
    /login: it cannot be used to reach a counted route past the limit."""
    async with _client() as client:
        for _ in range(LIMIT):
            assert (await client.get("/login")).status_code == 200
        assert (await client.get("/login")).status_code == 429

    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": raw_path,
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"localhost")],
        "client": ("203.0.113.9", 1234),
        "server": ("localhost", 443),
    }
    await create_application()(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 404
