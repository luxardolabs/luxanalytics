"""Defects the second adversarial pass (/adversarial on 798924a..HEAD) reproduced, kept as
regressions. Each failed before its fix."""

import asyncio
import json
import uuid
import zlib

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.middleware.request_middleware import InflatedTooLarge, inflate_body
from app.models.device_model import Device
from app.models.event_model import Event
from app.schemas.event_schema import EventCreate
from app.services.core.event_core_service import EventCoreService
from tests.test_ingest_http import PROJECT, _dsn_auth


def _bomb() -> bytes:
    """A few hundred KB that inflates to 512 MB of zeros."""
    return zlib.compress(b"\0" * (512 * 1024 * 1024), 9)


def _event(**overrides: object) -> dict[str, object]:
    return {
        "name": "app_opened",
        "timestamp": "2026-10-06T12:00:00Z",
        "metadata": {},
        **overrides,
    }


async def _stored(db: AsyncSession) -> int:
    query = select(func.count()).select_from(Event).where(Event.app_id == "test_app")
    return (await db.execute(query)).scalar_one()


# ── 1: an unauthenticated decompression bomb ─────────────────────────────────────────────────


def test_inflate_stops_at_the_limit() -> None:
    with pytest.raises(InflatedTooLarge):
        inflate_body(_bomb(), settings.MAX_REQUEST_SIZE)


@pytest.mark.db
async def test_a_decompression_bomb_is_a_413_before_auth(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/events/",
        content=_bomb(),
        headers={"Content-Type": "application/json", "Content-Encoding": "deflate"},
    )
    assert response.status_code == 413


# ── 2: concurrent requests with the same keys in opposite orders ─────────────────────────────


def _events(ids: list[str], device: str | None = None) -> list[EventCreate]:
    metadata = {"device_id": device} if device else {}
    return [EventCreate.model_validate(_event(id=i, metadata=metadata)) for i in ids]


async def _request(
    maker: async_sessionmaker[AsyncSession], app_id: str, events: list[EventCreate]
) -> str:
    """What get_db does around one ingest: one transaction, committed."""
    async with maker() as session:
        try:
            await EventCoreService(session).create_events(app_id, events)
            await session.commit()
            return "ok"
        except SQLAlchemyError as e:  # a deadlock is reported, not raised
            await session.rollback()
            return f"{type(e).__name__}: {str(e)[:100]}"


@pytest.mark.db
async def test_opposite_order_batches_do_not_deadlock(_engine: AsyncEngine) -> None:
    maker = async_sessionmaker(bind=_engine, expire_on_commit=False)
    app_id = f"adv_{uuid.uuid4().hex[:8]}"
    failures: list[str] = []
    try:
        for _ in range(5):
            ids = [str(uuid.uuid4()) for _ in range(500)]
            devices = [f"d{uuid.uuid4().hex[:6]}" for _ in range(20)]
            a = _events(ids) + [
                e for d in devices for e in _events([str(uuid.uuid4())], d)
            ]
            b = _events(ids[::-1]) + [
                e for d in devices[::-1] for e in _events([str(uuid.uuid4())], d)
            ]
            results = await asyncio.gather(
                _request(maker, app_id, a), _request(maker, app_id, b)
            )
            failures += [r for r in results if r != "ok"]
        async with maker() as session:
            stored = await session.execute(
                select(func.count()).select_from(Event).where(Event.app_id == app_id)
            )
        assert not failures, failures[:2]
        assert stored.scalar_one() == 5 * (500 + 2 * 20)
    finally:
        async with maker() as session:
            await session.execute(delete(Event).where(Event.app_id == app_id))
            await session.execute(delete(Device).where(Device.app_id == app_id))
            await session.commit()


# ── 3: an `id` we cannot key on is no dedupe, not a rejected batch ───────────────────────────


@pytest.mark.db
@pytest.mark.parametrize("bad_id", [12345, "", "x" * 65, True, {"a": 1}, "abc\x00def"])
async def test_an_unusable_id_still_stores_the_batch(
    client: AsyncClient, db: AsyncSession, sample_app: object, bad_id: object
) -> None:
    response = await client.post(
        f"/api/v1/events/{PROJECT}",
        json={"events": [_event(id=bad_id), _event()]},
        headers=_dsn_auth(),
    )
    assert response.status_code == 200, response.text
    assert await _stored(db) == 2


# ── 4: pool gauges mean what their help says ─────────────────────────────────────────────────


@pytest.mark.db
async def test_pool_overflow_is_never_negative(client: AsyncClient) -> None:
    text = (await client.get("/metrics")).text
    line = next(ln for ln in text.splitlines() if ln.startswith("db_pool_overflow "))
    assert float(line.split()[1]) >= 0


# ── 5: the bare-list form is capped like {"events": [...]} ───────────────────────────────────


@pytest.mark.db
@pytest.mark.parametrize("n", [1001, 4000])
async def test_a_bare_list_over_the_cap_is_a_422(
    client: AsyncClient, sample_app: object, n: int
) -> None:
    response = await client.post(
        f"/api/v1/events/{PROJECT}", json=[_event()] * n, headers=_dsn_auth()
    )
    assert response.status_code == 422


# ── 6: NUL is a client error, not a database error ───────────────────────────────────────────


@pytest.mark.db
@pytest.mark.parametrize(
    "event",
    [
        _event(name="a\x00b"),
        _event(user_id="u\x00"),
        _event(metadata={"screen": "home\x00"}),
    ],
)
async def test_nul_in_an_event_is_a_422(
    client: AsyncClient, sample_app: object, event: dict[str, object]
) -> None:
    response = await client.post(
        f"/api/v1/events/{PROJECT}", json=event, headers=_dsn_auth()
    )
    assert response.status_code == 422


# ── 7: in-process migrations keep the app's log identity ─────────────────────────────────────


@pytest.mark.db
async def test_startup_migrations_do_not_relabel_the_app_logs(
    capfd: pytest.CaptureFixture[str],
) -> None:
    import logging

    from app.core.logging_config import configure_logging
    from app.db.database import async_engine
    from app.main import run_migrations

    # The app's own logging, as app.main installs it at import (the test harness built the schema
    # through alembic the CLI way, which labels the process as migrations).
    configure_logging(
        service="luxanalytics",
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
    )
    await run_migrations()
    await async_engine.dispose()
    capfd.readouterr()
    logging.getLogger("probe").warning("after migrations")
    line = next(
        ln for ln in capfd.readouterr().out.splitlines() if "after migrations" in ln
    )
    assert json.loads(line)["service"] == "luxanalytics"


@pytest.mark.db
async def test_a_custom_validator_failure_is_a_422_not_a_500(
    client: AsyncClient, sample_app: object
) -> None:
    """A blank name fails a custom validator; its error context is not JSON."""
    response = await client.post(
        f"/api/v1/events/{PROJECT}",
        json=_event(name="   "),
        headers=_dsn_auth(),
    )
    assert response.status_code == 422
