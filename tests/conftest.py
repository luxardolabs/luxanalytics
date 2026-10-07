# luxlint canonical DB-test harness — the fleet pattern for testing against a REAL
# database (not mocks). Emit it as a starting point:  luxlint --emit-config conftest
#
# Adapt the REPO lines marked «EDIT» (your Base, your get_db dependency, your app factory,
# your module-level sessionmaker, your alembic.ini). Everything else is the standard: a per-test transaction that ROLLS BACK,
# so tests share one migrated schema, never see each other's writes, and stay fast.
#
# Why real Postgres, not SQLite/mocks: you ship on Postgres — jsonb, enums, ON
# CONFLICT, TimescaleDB, cascade behaviour and constraint errors only reproduce on
# the real engine. A mock proves your mock works. Use a disposable container.
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# «EDIT» — the module holding your module-level `async_sessionmaker`, and its name. Every fixture that
# gives the test a database points this maker at it, so app code opening its own session
# (`get_db_context()`, schedulers, durable modules) lands in the SAME database the test reads.
import app.db.database as _appdb

# «EDIT» — the FastAPI dependency your routes use to get a session
from app.db.database import get_db

# «EDIT» — your app factory / instance
from app.main import create_application as create_app

# «EDIT» — your declarative Base (with every model imported so metadata is complete)
from app.models.base_model import Base  # noqa: F401  (adjust import)

_APP_MAKER = "AsyncSessionLocal"

# THE test database. A DEDICATED env var — never the app's DATABASE_URL — so the suite can never
# accidentally run against the dev/prod database (it did once: a committing test left real rows behind,
# and a FAILING test skips its cleanup, so a stale row sat in dev for a week). The default is a
# throwaway container. In CI: spin up postgres as a service; locally: `make test-db`.
import os  # noqa: E402  (kept local to this canonical block)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)


def _guard_not_the_dev_database(url: str) -> None:
    """Refuse to run the DB suite against anything but a throwaway test database. This is the
    fleet floor — the suite really commits, so pointing it at dev/prod mutates real data. A test DB's
    name must be clearly test-scoped (`test`/`_test`) or on a test-only host."""
    if "test" not in url.rsplit("/", 1)[-1].lower() and "-test" not in url.lower():
        raise RuntimeError(
            f"TEST_DATABASE_URL does not look like a disposable test database: {url!r}. "
            "The DB suite must NEVER point at the dev/prod database — start the throwaway one "
            "with `make test-db`."
        )


def _build_test_schema(url: str = TEST_DATABASE_URL) -> None:
    """Build the throwaway DB's schema the SAME WAY dev/prod does — from the MIGRATION CHAIN. On an
    alembic repo `Base.metadata.create_all` is banned (fw.schema_from_migrations): a create_all schema
    is built from the models, NOT the chain, so the suite would run on a schema that can silently
    diverge from what a real deploy gets (a broken migration the tests never see). Building from
    `alembic upgrade head` means the suite exercises the real schema — one construction, no divergence.
    «EDIT» the alembic.ini path. NOT on alembic (create_all is then your only mechanism): swap this
    body for a `Base.metadata.create_all` via a sync connection.

    ⚠ VERIFY YOUR `env.py` LETS A CALLER-SUPPLIED URL WIN. env.py runs AFTER the line below sets the
    option, so an env.py that chooses its own URL — `config.set_main_option("sqlalchemy.url",
    settings.database_url)` unconditionally, `configuration["sqlalchemy.url"] = settings…`, or a
    `create_async_engine(settings…)` that never reads the Config — silently wins, and this migrates
    whatever env.py picked. Under `make test` (loaded with the dev env file) that is the DEV DATABASE,
    and `_guard_not_the_dev_database()` above CANNOT catch it: that guard validates the URL this fixture
    INTENDS, while the override happens two layers down inside alembic. The fix is one line in env.py —
    `url = config.get_main_option("sqlalchemy.url") or settings.database_url` — and a bare `alembic
    upgrade head` still works, because alembic.ini supplies nothing. Enforced by luxarch
    `repo.alembic_env_honors_caller_url`; reported as a near-miss, and 8 of 10 fleet env.py files had it.

    No SYNC driver is required: an async-native env.py (`async_engine_from_config` + `asyncio.run`) —
    the fleet's own FastAPI shape — drives the async URL itself. Add a sync driver only if your env.py
    is sync."""
    from alembic.config import Config

    from alembic import command

    # Under xdist each worker owns its own database; create it before migrating into it. The serial
    # case (url == TEST_DATABASE_URL) already exists, so this is a no-op there.
    #
    # Done through the ASYNC driver the repo already has, not a sync one. Stripping "+asyncpg" to get
    # a sync URL silently demands psycopg — and which psycopg, since SQLAlchemy 2.1 resolves a bare
    # `postgresql://` to psycopg v3 — so it would make an async-native repo install a driver it does
    # not otherwise need. That is the same "(sync driver)" trap OPENCLAIM-355 reported in this file's
    # docstring, and it is not worth re-introducing two fixtures below the correction.
    if url != TEST_DATABASE_URL:
        import asyncio as _asyncio

        import sqlalchemy as _sa
        from sqlalchemy.ext.asyncio import create_async_engine as _cae

        base, target = url.rsplit("/", 1)

        async def _create() -> None:
            admin = _cae(f"{base}/postgres", isolation_level="AUTOCOMMIT")
            async with admin.connect() as c:
                await c.execute(_sa.text(f'drop database if exists "{target}"'))
                await c.execute(_sa.text(f'create database "{target}"'))
            await admin.dispose()

        _asyncio.run(_create())

    cfg = Config("alembic.ini")  # «EDIT» path to your alembic.ini
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")  # sync API — the fixture runs it off the event loop


# ── per-worker database: what makes `pytest -n auto` possible at all ─────────────────────────────
# A single shared test database forces serial execution, and that is the whole cost of a slow suite —
# not the schema build. Measured on a 69-revision chain: `alembic upgrade head` is 0.6s, and workers
# build in PARALLEL, so the wall-clock cost is one build no matter how many workers you run. (A
# `CREATE DATABASE ... TEMPLATE` clone is ~0.05s, but it needs exclusive access to the template while
# cloning, which is its own source of flakes under xdist. Measured, then rejected: it buys half a
# second and costs a coordination problem.)
@pytest.fixture(scope="session")
def worker_db_url() -> str:
    """This worker's OWN database — "gw0"/"gw1"/... under `-n`, the plain URL when serial.

    Read from the ENV VAR xdist sets, not from xdist's `worker_id` fixture. The fixture exists only
    while the plugin is loaded, so depending on it makes the whole suite error with
    `fixture 'worker_id' not found` on any repo that has not installed pytest-xdist and on any run
    with `-p no:xdist`. The env var is absent in exactly those cases, which is the same question
    answered without a hard dependency on the plugin.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    if worker == "master":
        return TEST_DATABASE_URL
    base, name = TEST_DATABASE_URL.rsplit("/", 1)
    return f"{base}/{name}_{worker}"


# The schema is built ONCE per worker, SYNCHRONOUSLY — no event loop is involved, so nothing created
# here can outlive the loop that made it. The engine is deliberately NOT session-scoped; see `_engine`.
@pytest.fixture(scope="session")
def _schema(worker_db_url: str) -> str:
    """Build this worker's database + schema from the MIGRATION CHAIN, once. Returns its URL."""
    _guard_not_the_dev_database(worker_db_url)
    _build_test_schema(
        worker_db_url
    )  # `alembic upgrade head` — a sync call, no loop needed
    return worker_db_url


@pytest_asyncio.fixture
async def _engine(_schema: str) -> AsyncIterator[AsyncEngine]:
    """An engine PER TEST, created and disposed inside the test's OWN event loop — the one engine
    `db`, `owner_session` and `separate_connection` all draw from.

    A session-scoped engine is shared across loops, which is what forces `loop_scope` gymnastics — and
    when those are wrong the fixture teardown is deferred and a connection leaks per test until the pool
    wedges (measured: five connections sitting at `BEGIN;`, the sixth test blocks forever). It is also
    the reason a suite ends up needing an autouse `_dispose_app_engine_between_tests` hack. Creating an
    engine is cheap — it opens nothing until first use — so per-test is the simpler, sounder default.
    The small pool matters under `-n`: the ceiling is the DB's max_connections, not CPU. It still holds
    the two connections a durability lock needs (writer + separate reader) plus `db`'s own.

    **It also points the app's module-level maker at this engine**, for as long as the test runs. That
    is what makes a durability lock mean anything under `-n`: the WRITER is app code
    (`async with get_db_context()`), which asks the app's maker, and the READER is `separate_connection`,
    which draws from here. Before 0.58.2 only `db` swapped the maker, and `owner_session` /
    `separate_connection` deliberately don't take `db` (a lock needs real commits). Serial hid it,
    because the worker database IS the base database. Under `-n` the writer landed in the unmigrated
    base database and the reader looked in `…_gw0`: 27 of 735 open-claim tests failed, every one a
    durability lock, each reading as an application bug (OPENCLAIM-357).

    Under `-n`, a test that reaches app database code must request one of these fixtures (`db`,
    `owner_session`, `separate_connection`, or `_engine` itself). Otherwise the app's maker still points
    at the base database, which no worker migrates, and it fails loudly with `relation … does not
    exist`. In serial the same test passes only because some EARLIER test happened to migrate that
    database: a missing dependency hidden by ordering.

    App code writing through the maker in a durability test REALLY commits, exactly like
    `owner_session`: clean up what it committed. A fresh database per RUN is not enough, because later
    tests in the same run see the rows, and a unique key seeded twice reads as a flaky test.
    """
    engine = create_async_engine(_schema, pool_size=2, max_overflow=3)
    original = getattr(_appdb, _APP_MAKER)
    setattr(_appdb, _APP_MAKER, async_sessionmaker(bind=engine, expire_on_commit=False))
    try:
        yield engine
    finally:
        setattr(_appdb, _APP_MAKER, original)
        await engine.dispose()


@pytest_asyncio.fixture
async def db(_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One connection, one outer transaction, and a SAVEPOINT that EVERY session joins — so each test
    starts from the same migrated schema and leaks nothing, whichever session owner did the writing.

    Two properties this fixture used to CLAIM and not have, both measured against a fleet-shaped app
    (LUXWX-1023). They are the reason a suite needs an engine-dispose hack between tests, and the reason
    it cannot be parallelised:

    1. `join_transaction_mode="create_savepoint"`. The previous version opened a plain
       `connection.begin()`, so app code calling `commit()` COMMITTED THE OUTER TRANSACTION — the
       rollback below then undid nothing, and on asyncpg it raised
       `InterfaceError: cannot rollback; the transaction is in error state` outright. With savepoint
       mode an inner commit commits a SAVEPOINT and the outer transaction survives to be rolled back.
       (Several fleet conftests carry a comment calling this a "SAVEPOINT session". It was not one.)

    2. The module-level MAKER is swapped, not just the FastAPI dependency. `dependency_overrides`
       only covers the DI path; a session opened by `get_db_context()` — schedulers, background work,
       owned/durable modules — builds its own session from the app's maker, commits to the shared
       database and ESCAPES this rollback entirely. Measured: a route writing through the DI path and
       a route writing through the context path, one test, one rollback; the DI row vanished and the
       context row was still there afterwards. Patching the maker catches both, because both ask it.

    The maker is named once, at the top (`_appdb` / `_APP_MAKER`). If your app has more than one, they
    all need swapping — and that is worth fixing at the source instead
    (`luxarch --playbook db-session-unification`).
    """
    connection = await _engine.connect()
    trans = await connection.begin()
    maker = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    # Over `_engine`'s real-commit maker for this test; restored to it (not to the app's original) below.
    previous = getattr(_appdb, _APP_MAKER)
    setattr(_appdb, _APP_MAKER, maker)
    session = maker()
    try:
        yield session
    finally:
        setattr(_appdb, _APP_MAKER, previous)
        await session.close()
        await trans.rollback()  # undo everything this test wrote, through EITHER owner
        await connection.close()


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An HTTP client whose requests use the SAME rolled-back session, so an
    end-to-end call and its assertions see one consistent transaction."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ── durability fixtures ─────────────────────────────────────────────────────────────────────────
# The rollback-isolated `db` fixture above SWALLOWS a commit() into its outer transaction — invisible to
# any other connection — so it CANNOT prove cross-session durability (the bug class where a flush looked
# durable but a background task on a pooled connection got None: BOUTIQUE-383). A durability lock needs
# a session that REALLY commits plus a SEPARATE connection that sees only committed rows. See
# `luxarch --playbook db-mutations` §7 and FLEET-VERIFICATION-STANDARD.
@pytest_asyncio.fixture
async def owner_session(_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session that REALLY commits (its own connection, no outer rollback) — the writer in a
    durability lock. Clean up what it commits, or use a disposable DB."""
    maker = async_sessionmaker(bind=_engine, expire_on_commit=False)
    async with maker() as s:
        yield s


@pytest_asyncio.fixture
async def separate_connection(_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A DISTINCT connection that sees only COMMITTED rows — the reader in a durability lock. Assert
    invisibility on THIS before the owner commits, visibility after. Reading back through the writing
    session proves nothing."""
    maker = async_sessionmaker(bind=_engine, expire_on_commit=False)
    async with maker() as s:
        yield s


# Usage:
#   @pytest.mark.db
#   async def test_create_item(db: AsyncSession) -> None:
#       item = await ItemCrud().create(db, name="x")
#       assert (await ItemCrud().get(db, item.id)).name == "x"
#
#   async def test_route(client: AsyncClient) -> None:
#       r = await client.post("/items", json={"name": "x"})
#       assert r.status_code == 201
_ = pytest  # keep the import meaningful when a repo trims the sample tests


# ── Seed Data Fixtures ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sample_app(db: AsyncSession) -> object:
    """Create a sample app for testing."""
    from app.models.app_model import App

    app = App(
        id=uuid4(),
        public_id="test_public_id_12345678901",
        project_id="1234567890123456",
        app_id="test_app",
        name="Test Application",
        organization="Test Org",
        is_active=True,
        app_metadata={},
    )
    db.add(app)
    await db.flush()
    return app


@pytest_asyncio.fixture
async def sample_events(db: AsyncSession) -> list[dict[str, object]]:
    """Seed a set of events with promoted columns + properties."""
    from sqlalchemy import insert

    from app.models.event_model import Event

    now = datetime.now(UTC)
    events_data = [
        {
            "id": str(uuid4()),
            "app_id": "test_app",
            "name": "screen_view",
            "timestamp": now,
            "received_at": now,
            "user_id": "user_1",
            "session_id": "session_1",
            "device_id": "device_aaa",
            "device_model": "iPhone17,1",
            "os_version": "18.3",
            "app_version": "1.0.23",
            "platform": "ios",
            "properties": {"screen": "home", "feature": "main"},
            "event_metadata": {
                "device_id": "device_aaa",
                "device_model": "iPhone17,1",
                "system_version": "18.3",
                "app_version": "1.0.23",
                "screen": "home",
                "feature": "main",
            },
        },
        {
            "id": str(uuid4()),
            "app_id": "test_app",
            "name": "button_tapped",
            "timestamp": now,
            "received_at": now,
            "user_id": "user_1",
            "session_id": "session_1",
            "device_id": "device_aaa",
            "device_model": "iPhone17,1",
            "os_version": "18.3",
            "app_version": "1.0.23",
            "platform": "ios",
            "properties": {"button_name": "save", "screen": "settings"},
            "event_metadata": {
                "device_id": "device_aaa",
                "device_model": "iPhone17,1",
                "button_name": "save",
                "screen": "settings",
            },
        },
        {
            "id": str(uuid4()),
            "app_id": "test_app",
            "name": "error_occurred",
            "timestamp": now,
            "received_at": now,
            "user_id": "user_2",
            "session_id": "session_2",
            "device_id": "device_bbb",
            "device_model": "iPhone16,1",
            "os_version": "17.5",
            "app_version": "1.0.22",
            "platform": "ios",
            "properties": {
                "error_type": "network",
                "error_message": "timeout",
                "screen": "profile",
            },
            "event_metadata": {
                "device_id": "device_bbb",
                "error_type": "network",
                "error_message": "timeout",
                "screen": "profile",
            },
        },
        {
            "id": str(uuid4()),
            "app_id": "test_app",
            "name": "performance_measured",
            "timestamp": now,
            "received_at": now,
            "user_id": "user_1",
            "session_id": "session_1",
            "device_id": "device_aaa",
            "device_model": "iPhone17,1",
            "os_version": "18.3",
            "app_version": "1.0.23",
            "platform": "ios",
            "properties": {
                "operation": "api_call",
                "duration_ms": "150",
                "success": "true",
            },
            "event_metadata": {
                "device_id": "device_aaa",
                "operation": "api_call",
                "duration_ms": "150",
                "success": "true",
            },
        },
    ]

    await db.execute(insert(Event).values(events_data))
    await db.flush()
    return events_data
