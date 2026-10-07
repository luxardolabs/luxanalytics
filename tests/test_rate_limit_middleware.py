"""RateLimitMiddleware decides the limit, then runs the request exactly once.

Its try used to wrap call_next, so any exception the app raised landed in the "Redis failed"
fallback, was logged at debug, and the request ran a SECOND time (a failed ingest replayed).
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.middleware.request_middleware import RateLimitMiddleware


class Boom(Exception):
    pass


@pytest.fixture
def counted_app() -> tuple[FastAPI, list[str]]:
    calls: list[str] = []
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/api/boom")
    async def boom() -> None:
        calls.append("boom")
        raise Boom

    @app.get("/api/ok")
    async def ok() -> dict[str, bool]:
        calls.append("ok")
        return {"ok": True}

    return app, calls


async def test_an_app_exception_propagates_and_the_request_runs_once(
    counted_app: tuple[FastAPI, list[str]],
) -> None:
    app, calls = counted_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        with pytest.raises(Boom):
            await ac.get("/api/boom")
    assert calls == ["boom"]


async def test_a_normal_request_runs_once(
    counted_app: tuple[FastAPI, list[str]],
) -> None:
    app, calls = counted_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/api/ok")
    assert response.status_code == 200
    assert calls == ["ok"]
