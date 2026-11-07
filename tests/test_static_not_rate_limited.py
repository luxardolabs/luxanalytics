"""/static (read-only files, no database) is held to its own, wider per-IP budget: a page load's
assets never meet the page limit, a flood of them still meets a limit, and the budget is the static
mount's own prefix: no other path can borrow it."""

import logging

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.types import Message, Scope

import app.core.rate_limiter as rate_limiter
from app.core.config import Settings, settings
from app.main import create_application
from tests.test_settings import REQUIRED, SAFE

LIMIT = 2
STATIC_LIMIT = 6
ASSET = "/static/js/app.js"


async def _no_redis() -> None:
    return None


@pytest.fixture(autouse=True)
def request_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", LIMIT)
    monkeypatch.setattr(settings, "STATIC_RATE_LIMIT_REQUESTS", STATIC_LIMIT)


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_application()), base_url="https://localhost"
    )


async def test_assets_are_served_past_the_page_limit_and_spend_none_of_it() -> None:
    async with _client() as client:
        codes = [(await client.get(ASSET)).status_code for _ in range(STATIC_LIMIT)]
        page = await client.get("/login")

    assert codes == [200] * STATIC_LIMIT
    assert page.status_code == 200


async def test_a_flood_of_assets_meets_the_static_limit() -> None:
    async with _client() as client:
        codes = [(await client.get(ASSET)).status_code for _ in range(STATIC_LIMIT + 2)]
        page = await client.get("/login")

    assert codes == [200] * STATIC_LIMIT + [429, 429]
    assert page.status_code == 200  # the flood spent none of the page budget


async def test_a_static_response_reports_the_static_limit() -> None:
    async with _client() as client:
        response = await client.get(ASSET)

    assert response.headers["x-ratelimit-limit"] == str(STATIC_LIMIT)


async def test_the_static_limit_holds_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rate_limiter, "get_redis_client", _no_redis)
    headers = {"X-Forwarded-For": "198.51.100.88"}
    async with _client() as client:
        codes = [
            (await client.get(ASSET, headers=headers)).status_code
            for _ in range(STATIC_LIMIT + 1)
        ]

    assert codes == [200] * STATIC_LIMIT + [429]


async def test_the_static_budget_is_per_client() -> None:
    async with _client() as client:
        for _ in range(STATIC_LIMIT):
            await client.get(ASSET, headers={"X-Forwarded-For": "198.51.100.51"})
        other = await client.get(ASSET, headers={"X-Forwarded-For": "198.51.100.52"})

    assert other.status_code == 200


async def test_a_page_is_still_limited() -> None:
    async with _client() as client:
        codes = [(await client.get("/login")).status_code for _ in range(LIMIT + 1)]

    assert codes == [200] * LIMIT + [429]


async def test_a_path_that_only_starts_with_static_is_held_to_the_page_limit() -> None:
    async with _client() as client:
        codes = [(await client.get("/staticfoo")).status_code for _ in range(LIMIT + 1)]

    assert codes == [404] * LIMIT + [429]


@pytest.mark.parametrize(
    "path", ["/s%09tatic/login", "/static%0a/login", "/%0dstatic/x"]
)
async def test_a_path_with_a_control_character_is_not_static(path: str) -> None:
    """URL parsing drops tab, CR and LF from a path; the budget is chosen from the path the
    router matches, which keeps them."""
    async with _client() as client:
        codes = [(await client.get(path)).status_code for _ in range(LIMIT + 1)]

    assert codes == [404] * LIMIT + [429]


@pytest.mark.parametrize(
    ("raw_path", "path"),
    [
        (b"/static/../login", "/static/../login"),
        (b"/static%2F../login", "/static/../login"),
        (b"/static/%2e%2e/login", "/static/../login"),
    ],
)
async def test_a_static_prefixed_path_does_not_reach_a_page_route(
    raw_path: bytes, path: str
) -> None:
    """Whatever such a path decodes to, it is answered by the static mount (a 404), never by
    /login: it cannot be used to reach a page route under the wider budget."""
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


def test_the_static_limit_defaults_to_a_thousand_a_minute() -> None:
    loaded = Settings.model_validate({**REQUIRED, **SAFE})

    assert loaded.STATIC_RATE_LIMIT_REQUESTS == 1000


def test_a_static_limit_that_cannot_work_refuses_to_start() -> None:
    with pytest.raises(ValueError, match="STATIC_RATE_LIMIT_REQUESTS"):
        Settings.model_validate({**REQUIRED, **SAFE, "STATIC_RATE_LIMIT_REQUESTS": 0})


async def test_a_refused_asset_is_logged_as_a_rate_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _client() as client:
        with caplog.at_level(logging.WARNING):
            for _ in range(STATIC_LIMIT + 1):
                await client.get(ASSET)

    refused = [r for r in caplog.records if r.getMessage() == "Rate limit exceeded"]
    assert len(refused) == 1
