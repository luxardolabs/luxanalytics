"""A signed HMAC request that was stored is not stored again inside its timestamp window.

The signature covers the body and the timestamp, so a captured request verifies until the timestamp
leaves the window. Once a request has been committed its signature is marked in Redis; a request
whose signature is marked is answered 200 as a duplicate, stores nothing and spends no ingest
budget. The mark is written after the commit, so a request that failed, was cancelled or died
before the commit leaves none and its retry is stored. An unreachable Redis lets every request
through."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as redis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.hmac_replay as hmac_replay
from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.crud.event_crud import EventWriteCRUD
from app.main import create_application
from app.models.app_model import App
from app.models.event_model import Event
from tests.test_app_rate_limit import (
    LIMIT,
    _batch,
    _client,
    _event,
    _post_signed,
    _signed,
)

pytestmark = pytest.mark.db

DUPLICATE = "Duplicate request ignored"


@pytest_asyncio.fixture
async def server() -> AsyncIterator[AsyncClient]:
    """Answers a 500 as a response, as a real server does, instead of raising it."""
    async with AsyncClient(
        transport=ASGITransport(app=create_application(), raise_app_exceptions=False),
        base_url="https://localhost",
    ) as ac:
        yield ac


async def _stored(db: AsyncSession, app_id: str) -> int:
    result = await db.execute(
        select(func.count()).select_from(Event).where(Event.app_id == app_id)
    )
    return result.scalar_one()


async def _marks() -> list[str]:
    redis_client = await get_redis_client()
    assert redis_client is not None
    return [key async for key in redis_client.scan_iter("hmac:sig:*")]


def _fail_insert_once(monkeypatch: pytest.MonkeyPatch) -> None:
    real = EventWriteCRUD.bulk_insert
    armed = {"on": True}

    async def insert(self: EventWriteCRUD, session: AsyncSession, rows: Any) -> Any:
        if armed["on"]:
            armed["on"] = False
            raise RuntimeError("the insert failed")
        return await real(self, session, rows)

    monkeypatch.setattr(EventWriteCRUD, "bulk_insert", insert)


async def test_a_replayed_signed_request_is_stored_once(
    server: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    body = _event()
    headers = _signed(body, sample_app.app_id)

    replies = [
        await server.post("/api/v1/events/", content=body, headers=headers)
        for _ in range(3)
    ]

    assert [r.status_code for r in replies] == [200, 200, 200]
    assert replies[0].json()["events_received"] == 1
    assert [r.json()["message"] for r in replies[1:]] == [DUPLICATE, DUPLICATE]
    assert await _stored(db, sample_app.app_id) == 1


async def test_a_new_signature_is_not_a_replay(
    server: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    for device in ("device-1", "device-2"):
        body = _event(device)
        response = await server.post(
            "/api/v1/events/", content=body, headers=_signed(body, sample_app.app_id)
        )
        assert response.status_code == 200
    assert await _stored(db, sample_app.app_id) == 2


async def test_two_key_ids_sharing_a_secret_do_not_share_marks(
    server: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings, "HMAC_KEYS", json.dumps({"app_a": "s" * 40, "app_b": "s" * 40})
    )
    body = _event()
    headers_a = _signed(body, "app_a")
    headers_b = {**headers_a, "X-Key-ID": "app_b"}

    await server.post("/api/v1/events/", content=body, headers=headers_a)
    reply = await server.post("/api/v1/events/", content=body, headers=headers_b)

    assert reply.json()["events_received"] == 1
    assert await _stored(db, "app_a") == 1
    assert await _stored(db, "app_b") == 1


async def test_replays_do_not_spend_the_apps_ingest_budget(
    monkeypatch: pytest.MonkeyPatch, db: AsyncSession, sample_app: App
) -> None:
    monkeypatch.setattr(settings, "APP_RATE_LIMIT_EVENTS", LIMIT)
    body = _event("device-1")
    headers = _signed(body, sample_app.app_id)
    async with _client() as ac:
        codes = []
        for i in range(LIMIT + 1):
            r = await ac.post(
                "/api/v1/events/",
                content=body,
                headers={**headers, "X-Forwarded-For": f"203.0.113.{i + 1}"},
            )
            codes.append(r.status_code)
        genuine = await _post_signed(
            ac, _event("device-2"), sample_app.app_id, "198.51.100.7"
        )

    assert codes == [200] * (LIMIT + 1)
    assert genuine == 200
    assert await _stored(db, sample_app.app_id) == 2


async def test_replays_still_spend_the_per_ip_request_budget(
    server: AsyncClient, sample_app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", LIMIT)
    body = _event()
    headers = {**_signed(body, sample_app.app_id), "X-Forwarded-For": "203.0.113.77"}

    codes = [
        (
            await server.post("/api/v1/events/", content=body, headers=headers)
        ).status_code
        for _ in range(LIMIT + 1)
    ]

    assert codes == [200] * LIMIT + [429]


async def test_the_mark_is_written_after_the_commit(
    server: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    body = _event()
    headers = _signed(body, sample_app.app_id)
    assert await _marks() == []

    await server.post("/api/v1/events/", content=body, headers=headers)

    marks = await _marks()
    assert marks == [f"hmac:sig:{sample_app.app_id}:{headers['X-HMAC-Signature']}"]
    redis_client = await get_redis_client()
    assert redis_client is not None
    ttl = await redis_client.ttl(marks[0])
    assert 0 < ttl <= 2 * settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE + 1


async def test_a_failed_commit_leaves_no_mark_and_the_retry_is_stored(
    server: AsyncClient,
    db: AsyncSession,
    sample_app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _event()
    headers = _signed(body, sample_app.app_id)
    real_commit = AsyncSession.commit
    armed = {"on": True}

    async def commit(self: AsyncSession) -> None:
        if armed["on"]:
            armed["on"] = False
            raise OperationalError("COMMIT", {}, Exception("server closed connection"))
        await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)

    failed = await server.post("/api/v1/events/", content=body, headers=headers)
    assert failed.status_code == 500
    assert await _marks() == []
    assert await _stored(db, sample_app.app_id) == 0

    retry = await server.post("/api/v1/events/", content=body, headers=headers)

    assert retry.json()["events_received"] == 1
    assert await _stored(db, sample_app.app_id) == 1


async def test_a_failure_before_the_commit_leaves_no_mark_and_the_retry_is_stored(
    server: AsyncClient,
    db: AsyncSession,
    sample_app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _event()
    headers = _signed(body, sample_app.app_id)
    _fail_insert_once(monkeypatch)

    failed = await server.post("/api/v1/events/", content=body, headers=headers)
    assert failed.status_code == 500
    assert await _marks() == []

    retry = await server.post("/api/v1/events/", content=body, headers=headers)

    assert retry.json()["events_received"] == 1
    assert await _stored(db, sample_app.app_id) == 1


async def test_a_retry_while_the_first_attempt_is_in_flight_is_stored_when_the_first_fails(
    server: AsyncClient,
    separate_connection: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two requests with their own transactions, really committed: the first fails after the retry
    ran, and the retry's rows stay."""
    app_id = "test_app"
    body = _event()
    headers = _signed(body, app_id)
    real = EventWriteCRUD.bulk_insert
    started, release = asyncio.Event(), asyncio.Event()
    calls: list[int] = []

    async def insert(self: EventWriteCRUD, session: AsyncSession, rows: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            started.set()
            await release.wait()
            raise RuntimeError("the first attempt fails after the retry ran")
        return await real(self, session, rows)

    monkeypatch.setattr(EventWriteCRUD, "bulk_insert", insert)
    first = asyncio.create_task(
        server.post("/api/v1/events/", content=body, headers=headers)
    )
    await asyncio.wait_for(started.wait(), 10)

    retry = await server.post("/api/v1/events/", content=body, headers=headers)
    release.set()
    first_reply = await first

    assert first_reply.status_code == 500
    assert retry.json()["events_received"] == 1
    assert await _stored(separate_connection, app_id) == 1


async def test_a_request_that_dies_before_the_commit_leaves_no_mark_and_the_retry_is_stored(
    server: AsyncClient,
    db: AsyncSession,
    sample_app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled request stands for a killed worker: it stops inside the insert, with the
    transaction open."""
    body = _event()
    headers = _signed(body, sample_app.app_id)
    real = EventWriteCRUD.bulk_insert
    started, block = asyncio.Event(), asyncio.Event()
    armed = {"on": True}

    async def insert(self: EventWriteCRUD, session: AsyncSession, rows: Any) -> Any:
        if armed["on"]:
            armed["on"] = False
            started.set()
            await block.wait()
        return await real(self, session, rows)

    monkeypatch.setattr(EventWriteCRUD, "bulk_insert", insert)
    task = asyncio.create_task(
        server.post("/api/v1/events/", content=body, headers=headers)
    )
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _marks() == []
    assert await _stored(db, sample_app.app_id) == 0

    retry = await server.post("/api/v1/events/", content=body, headers=headers)

    assert retry.json()["events_received"] == 1
    assert await _stored(db, sample_app.app_id) == 1


async def test_a_refused_request_leaves_no_mark(
    server: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    sample_app.is_active = False
    await db.flush()
    body = _event()
    headers = _signed(body, sample_app.app_id)

    refused = await server.post("/api/v1/events/", content=body, headers=headers)

    assert refused.status_code == 403
    assert await _marks() == []


async def test_an_unreachable_redis_lets_ingest_through_and_logs_it(
    server: AsyncClient,
    db: AsyncSession,
    sample_app: App,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without Redis a stored request cannot be recognised: ingest keeps working, as the rate
    limiter does, and a replay is stored (an event id still dedupes events that carry one)."""
    unreachable = redis.from_url("redis://127.0.0.1:1/0")

    async def get_unreachable_client() -> redis.Redis:
        return unreachable

    monkeypatch.setattr(hmac_replay, "get_redis_client", get_unreachable_client)
    body = _event()
    headers = _signed(body, sample_app.app_id)

    with caplog.at_level(logging.ERROR):
        codes = [
            (
                await server.post("/api/v1/events/", content=body, headers=headers)
            ).status_code
            for _ in range(2)
        ]
    await unreachable.aclose()

    assert codes == [200, 200]
    assert await _stored(db, sample_app.app_id) == 2
    messages = {r.getMessage() for r in caplog.records}
    assert {"HMAC replay check failed", "HMAC replay mark failed"} <= messages


async def test_ingest_works_when_redis_is_not_configured(
    server: AsyncClient,
    db: AsyncSession,
    sample_app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_redis() -> None:
        return None

    monkeypatch.setattr(hmac_replay, "get_redis_client", no_redis)
    body = _event()
    headers = _signed(body, sample_app.app_id)

    for _ in range(2):
        response = await server.post("/api/v1/events/", content=body, headers=headers)
        assert response.status_code == 200

    assert await _stored(db, sample_app.app_id) == 2


async def test_a_replayed_request_with_an_event_id_stores_it_once(
    server: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    body = json.dumps(
        {
            "id": "7f1b6c1e-0d3e-4a52-9a43-5a2f0c9d8e11",
            "name": "app_opened",
            "timestamp": "2026-10-06T12:00:00Z",
            "metadata": {},
        }
    ).encode()
    headers = _signed(body, sample_app.app_id)
    for _ in range(2):
        response = await server.post("/api/v1/events/", content=body, headers=headers)
        assert response.status_code == 200
    assert await _stored(db, sample_app.app_id) == 1


async def test_a_batch_refused_by_a_limit_leaves_no_mark_and_the_retry_is_stored(
    db: AsyncSession, sample_app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _batch(2)
    headers = _signed(body, sample_app.app_id)
    monkeypatch.setattr(settings, "IP_RATE_LIMIT_EVENTS", 1)
    async with _client() as ac:
        refused = await ac.post("/api/v1/events/", content=body, headers=headers)
        monkeypatch.setattr(settings, "IP_RATE_LIMIT_EVENTS", 6000)
        retried = await ac.post("/api/v1/events/", content=body, headers=headers)

    assert refused.status_code == 429
    assert retried.json()["events_received"] == 2
    assert await _stored(db, sample_app.app_id) == 2


async def test_a_stats_signature_replayed_on_ingest_is_not_a_duplicate(
    server: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    """The stats read marks nothing: an empty body signs the same for every route, and the
    replayed read is an empty ingest body, a 400."""
    headers = _signed(b"", sample_app.app_id)
    stats = await server.get("/api/v1/events/stats", headers=headers)
    assert stats.status_code == 200

    replay = await server.post("/api/v1/events/", content=b"", headers=headers)

    assert replay.status_code == 400
    assert await _stored(db, sample_app.app_id) == 0


async def test_replays_of_the_stats_read_spend_the_apps_budget(
    monkeypatch: pytest.MonkeyPatch, sample_app: App
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", LIMIT)
    headers = _signed(b"", sample_app.app_id)
    async with _client() as ac:
        codes = [
            (
                await ac.get(
                    "/api/v1/events/stats",
                    headers={**headers, "X-Forwarded-For": f"203.0.113.{i + 1}"},
                )
            ).status_code
            for i in range(LIMIT + 1)
        ]

    assert codes == [200] * LIMIT + [429]


async def test_a_deleted_apps_key_cannot_read_its_stats(
    server: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    sample_app.soft_delete()
    await db.flush()

    response = await server.get(
        "/api/v1/events/stats", headers=_signed(b"", sample_app.app_id)
    )

    assert response.status_code == 403


async def test_an_inactive_apps_key_can_still_read_its_stats(
    server: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    sample_app.is_active = False
    await db.flush()

    response = await server.get(
        "/api/v1/events/stats", headers=_signed(b"", sample_app.app_id)
    )

    assert response.status_code == 200


async def test_a_non_ascii_signature_is_refused_not_a_500(
    server: AsyncClient, sample_app: App
) -> None:
    body = _event()
    headers: dict[str, Any] = {**_signed(body, sample_app.app_id)}
    headers["X-HMAC-Signature"] = "\xe9".encode("latin-1")

    response = await server.post("/api/v1/events/", content=body, headers=headers)

    assert response.status_code == 403
