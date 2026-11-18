"""A Redis client that cannot be created: /health says so, a silent server costs a bounded connect,
and the failure's traceback is logged once per interval, not once per request."""

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from limits.storage import RedisStorage

import app.core.redis_client as redis_client
import app.services.core.health_core_service as health_core_service
from app.core.config import settings
from app.core.limiter import limiter
from app.core.redis_client import OUTAGE_LOG_INTERVAL

INIT_FAILED = "Failed to initialize Redis client"


async def _no_client() -> None:
    return None


async def test_health_reports_a_configured_redis_whose_client_failed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "REDIS_URL", "redis://redis-test:6379/0")
    monkeypatch.setattr(health_core_service, "get_redis_client", _no_client)

    body = (await client.get("/health")).json()

    assert body["redis"]["status"] == "unhealthy"
    assert body["redis"]["error"]


async def test_health_reports_an_unset_redis_as_not_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(health_core_service, "get_redis_client", _no_client)

    body = (await client.get("/health")).json()

    assert body["redis"] == {"status": "not configured", "error": None}


@pytest_asyncio.fixture
async def silent_server() -> AsyncIterator[int]:
    """A TCP server that accepts and never answers: a TLS handshake against it never completes."""
    held: list[asyncio.StreamWriter] = []

    async def hold(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port
    for writer in held:
        writer.close()
    server.close()
    await server.wait_closed()


async def test_a_url_without_a_connect_timeout_gets_one(
    silent_server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "REDIS_URL", f"rediss://127.0.0.1:{silent_server}/0")
    await redis_client.close_redis_client()

    loop = asyncio.get_running_loop()
    started = loop.time()
    # Unbounded, the handshake waits forever: the outer bound turns that into a failure.
    async with asyncio.timeout(5):
        assert await redis_client.get_redis_client() is None
    assert loop.time() - started < 3


def test_a_url_keeps_its_own_connect_timeout_and_gets_one_otherwise() -> None:
    assert (
        redis_client.with_connect_timeout("redis://:pw%40x@h:6379/0?db=1")
        == "redis://:pw%40x@h:6379/0?db=1&socket_connect_timeout=1"
    )
    assert (
        redis_client.with_connect_timeout("rediss://h:6379/0")
        == "rediss://h:6379/0?socket_connect_timeout=1"
    )
    own = "redis://h:6379/0?socket_connect_timeout=7"
    assert redis_client.with_connect_timeout(own) == own


async def test_a_url_that_sets_its_connect_timeout_keeps_it(
    silent_server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings,
        "REDIS_URL",
        f"rediss://127.0.0.1:{silent_server}/0?socket_connect_timeout=0.2",
    )
    await redis_client.close_redis_client()

    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await redis_client.get_redis_client() is None
    assert loop.time() - started < 0.2 + 0.5


def test_the_request_limiter_has_the_same_connect_timeout() -> None:
    """slowapi's Redis client is the other one the app opens: it is bounded the same way."""
    storage = limiter._storage
    assert isinstance(storage, RedisStorage)
    options = storage.storage.connection_pool.connection_kwargs
    assert options.get("socket_connect_timeout") is not None


def _init_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == INIT_FAILED]


@pytest_asyncio.fixture
async def unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """No client yet, and a REDIS_URL nothing answers: each request tries to create one."""
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")
    await redis_client.close_redis_client()


@pytest.mark.usefixtures("unreachable")
async def test_a_redis_down_at_start_logs_at_most_one_traceback_per_interval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.core.redis_client"):
        for _ in range(5):
            assert await redis_client.get_redis_client() is None

    assert len(_init_failures(caplog)) <= 1


@pytest.mark.usefixtures("unreachable")
async def test_a_redis_still_down_after_the_interval_logs_its_traceback_again(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(redis_client.init_failures, "clock", lambda: now[0])
    monkeypatch.setattr(redis_client.init_failures, "last_logged", None)
    with caplog.at_level(logging.INFO, logger="app.core.redis_client"):
        for _ in range(3):
            await redis_client.get_redis_client()
        now[0] += OUTAGE_LOG_INTERVAL
        await redis_client.get_redis_client()

    logged = _init_failures(caplog)
    assert len(logged) == 2
    assert all(r.exc_info is not None for r in logged)
