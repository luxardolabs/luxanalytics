"""A batch that fails to store gives its events back to the budgets it spent, so an SDK's retry
after a database outage is not refused for what the failed attempt used."""

import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.rate_limiter as rate_limiter
from app.core.config import settings
from app.models.app_model import App
from app.services.core.event_core_service import EventCoreService

LIMIT = 5


async def _no_redis() -> None:
    return None


def _payload(count: int, device_id: str) -> dict[str, object]:
    return {
        "events": [
            {
                "name": "app_opened",
                "timestamp": datetime.now(UTC).isoformat(),
                "metadata": {"device_id": device_id},
            }
            for _ in range(count)
        ]
    }


@pytest.fixture
def identity() -> tuple[str, str]:
    """An address and a device of this test alone: the in-memory windows outlive a test."""
    number = uuid4().int
    return f"10.{number % 250}.{number // 250 % 250}.{number // 62500 % 250 + 1}", (
        uuid4().hex
    )


@pytest.fixture(autouse=True)
def tight_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The address and the device are exactly LIMIT events wide; the app's ceiling is out of reach."""
    monkeypatch.setattr(settings, "IP_RATE_LIMIT_EVENTS", LIMIT)
    monkeypatch.setattr(settings, "DEVICE_RATE_LIMIT_EVENTS", LIMIT)
    monkeypatch.setattr(settings, "APP_RATE_LIMIT_EVENTS", 10_000)


@pytest.fixture(params=[True, False], ids=["redis", "memory"])
def window(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if not request.param:
        monkeypatch.setattr(rate_limiter, "get_redis_client", _no_redis)


@pytest.mark.db
async def test_a_batch_that_fails_to_store_leaves_its_budgets_whole(
    db: AsyncSession,
    sample_app: App,
    window: None,
    identity: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ip, device = identity

    async def fails(self: EventCoreService, *args: object) -> None:
        raise ConnectionError("the database is down")

    real = EventCoreService.create_events
    monkeypatch.setattr(EventCoreService, "create_events", fails)
    with pytest.raises(ConnectionError):
        await EventCoreService(db).ingest(
            sample_app.app_id, _payload(LIMIT, device), ip
        )

    monkeypatch.setattr(EventCoreService, "create_events", real)
    result = await EventCoreService(db).ingest(
        sample_app.app_id, _payload(LIMIT, device), ip
    )

    assert result.stored == LIMIT


@pytest.mark.db
async def test_a_batch_that_stored_keeps_its_budgets_spent(
    db: AsyncSession, sample_app: App, window: None, identity: tuple[str, str]
) -> None:
    ip, device = identity
    await EventCoreService(db).ingest(sample_app.app_id, _payload(LIMIT, device), ip)

    with pytest.raises(rate_limiter.AppRateLimitExceeded):
        await EventCoreService(db).ingest(sample_app.app_id, _payload(1, device), ip)


@pytest.mark.db
async def test_a_failed_batch_refunds_only_its_own_events(
    db: AsyncSession,
    sample_app: App,
    window: None,
    identity: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ip, device = identity
    await EventCoreService(db).ingest(sample_app.app_id, _payload(2, device), ip)

    async def fails(self: EventCoreService, *args: object) -> None:
        raise ConnectionError("the database is down")

    real = EventCoreService.create_events
    monkeypatch.setattr(EventCoreService, "create_events", fails)
    with pytest.raises(ConnectionError):
        await EventCoreService(db).ingest(
            sample_app.app_id, _payload(LIMIT - 2, device), ip
        )
    monkeypatch.setattr(EventCoreService, "create_events", real)

    with pytest.raises(rate_limiter.AppRateLimitExceeded):
        await EventCoreService(db).ingest(
            sample_app.app_id, _payload(LIMIT - 1, device), ip
        )


async def test_a_refund_that_cannot_reach_redis_is_dropped_with_a_warning(
    identity: tuple[str, str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ip, device = identity
    spent = await rate_limiter.enforce_ingest_limits("app_refund", ip, {device: 1})
    assert spent.redis_member is not None
    unreachable = redis.from_url("redis://127.0.0.1:1/0")

    async def get_unreachable_client() -> redis.Redis:
        return unreachable

    monkeypatch.setattr(rate_limiter, "get_redis_client", get_unreachable_client)
    with caplog.at_level(logging.WARNING, logger="app.core.rate_limiter"):
        await rate_limiter.refund(spent)
    await unreachable.aclose()

    limiter_logs = [r for r in caplog.records if r.name == "app.core.rate_limiter"]
    assert [r.getMessage() for r in limiter_logs] == ["Redis rate limit refund failed"]
