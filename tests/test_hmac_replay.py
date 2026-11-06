"""A signed HMAC request is processed once inside its timestamp window.

The signature covers the body and the timestamp, so a captured request verifies until the
timestamp leaves the window. The first use of a signature claims it in Redis (SET NX); a second use
is answered 200 as a duplicate, stores nothing and spends no rate-limit budget. A request that fails
gives its claim back, and an unreachable Redis lets every request through."""

import json
import logging

import pytest
import redis.asyncio as redis
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.hmac_replay as hmac_replay
from app.core.config import settings
from app.core.redis_client import get_redis_client
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

SIGNATURE_PREFIX = "hmac:sig:"


async def _stored(db: AsyncSession, app_id: str) -> int:
    result = await db.execute(
        select(func.count()).select_from(Event).where(Event.app_id == app_id)
    )
    return result.scalar_one()


async def test_a_replayed_signed_request_is_stored_once(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    body = _event()
    headers = _signed(body, sample_app.app_id)

    replies = [
        await client.post("/api/v1/events/", content=body, headers=headers)
        for _ in range(3)
    ]

    assert [r.status_code for r in replies] == [200, 200, 200]
    assert replies[0].json()["events_received"] == 1
    assert replies[1].json()["message"] == "Duplicate request ignored"
    assert await _stored(db, sample_app.app_id) == 1


async def test_a_new_signature_is_not_a_replay(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    for device in ("device-1", "device-2"):
        body = _event(device)
        response = await client.post(
            "/api/v1/events/", content=body, headers=_signed(body, sample_app.app_id)
        )
        assert response.status_code == 200
    assert await _stored(db, sample_app.app_id) == 2


async def test_replays_do_not_spend_the_apps_budget(
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


async def test_replays_of_the_stats_read_do_not_spend_the_apps_budget(
    monkeypatch: pytest.MonkeyPatch, sample_app: App
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", LIMIT)
    headers = _signed(b"", sample_app.app_id)
    async with _client() as ac:
        codes = []
        for i in range(LIMIT + 2):
            r = await ac.get(
                "/api/v1/events/stats",
                headers={**headers, "X-Forwarded-For": f"203.0.113.{i + 1}"},
            )
            codes.append(r.status_code)

    assert codes == [200] * (LIMIT + 2)


async def test_a_stats_signature_replayed_on_ingest_is_a_duplicate(
    client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    """An empty body signs the same for every route: the signature, not the route, is single use."""
    headers = _signed(b"", sample_app.app_id)
    stats = await client.get("/api/v1/events/stats", headers=headers)
    assert stats.status_code == 200

    replay = await client.post("/api/v1/events/", content=b"", headers=headers)

    assert replay.status_code == 200
    assert replay.json()["message"] == "Duplicate request ignored"
    assert await _stored(db, sample_app.app_id) == 0


async def test_a_refused_request_gives_its_signature_back(
    monkeypatch: pytest.MonkeyPatch, db: AsyncSession, sample_app: App
) -> None:
    """A batch refused by a rate limit stored nothing, so retrying the same signed request once
    there is room stores it."""
    body = _batch(2)
    headers = _signed(body, sample_app.app_id)
    monkeypatch.setattr(settings, "IP_RATE_LIMIT_EVENTS", 1)
    async with _client() as ac:
        refused = await ac.post("/api/v1/events/", content=body, headers=headers)
        monkeypatch.setattr(settings, "IP_RATE_LIMIT_EVENTS", 6000)
        retried = await ac.post("/api/v1/events/", content=body, headers=headers)

    assert refused.status_code == 429
    assert retried.status_code == 200
    assert retried.json()["events_received"] == 2
    assert await _stored(db, sample_app.app_id) == 2


async def test_an_unreachable_redis_lets_ingest_through(
    client: AsyncClient,
    db: AsyncSession,
    sample_app: App,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without Redis a signature cannot be remembered: ingest keeps working, as the rate limiter
    does, and a replay is stored (the event id still dedupes events that carry one)."""
    unreachable = redis.from_url("redis://127.0.0.1:1/0")

    async def get_unreachable_client() -> redis.Redis:
        return unreachable

    monkeypatch.setattr(hmac_replay, "get_redis_client", get_unreachable_client)
    body = _event()
    headers = _signed(body, sample_app.app_id)

    with caplog.at_level(logging.ERROR):
        codes = [
            (
                await client.post("/api/v1/events/", content=body, headers=headers)
            ).status_code
            for _ in range(2)
        ]
    await unreachable.aclose()

    assert codes == [200, 200]
    assert await _stored(db, sample_app.app_id) == 2
    assert "HMAC replay check failed" in {r.getMessage() for r in caplog.records}


async def test_a_replayed_request_with_an_event_id_stores_it_once(
    client: AsyncClient, db: AsyncSession, sample_app: App
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
        response = await client.post("/api/v1/events/", content=body, headers=headers)
        assert response.status_code == 200
    assert await _stored(db, sample_app.app_id) == 1


async def test_a_claimed_signature_expires_with_its_window(
    client: AsyncClient, sample_app: App
) -> None:
    body = _event()
    headers = _signed(body, sample_app.app_id)
    await client.post("/api/v1/events/", content=body, headers=headers)

    redis_client = await get_redis_client()
    assert redis_client is not None
    key = SIGNATURE_PREFIX + headers["X-HMAC-Signature"]
    ttl = await redis_client.ttl(key)

    assert 0 < ttl <= 2 * settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE + 1
