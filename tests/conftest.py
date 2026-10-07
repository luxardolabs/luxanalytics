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
from urllib.parse import urlsplit
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
from app.core.config import settings as app_settings
from app.core.redis_client import close_redis_client, get_redis_client
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


@pytest_asyncio.fixture(autouse=True)
async def _redis_client_per_test() -> AsyncIterator[None]:
    """The suite runs against a real Redis (repo.test_stack_parity), isolated per test the way `db`
    rolls back: what a test counted (rate-limit windows) is flushed after it. The app's client is one
    per process, bound to the loop that opened it, and pytest-asyncio gives each test its own loop:
    close it while this test's loop is alive, so the next test opens its own."""
    yield
    redis_client = await get_redis_client()
    if redis_client is not None:
        _guard_not_the_dev_redis(app_settings.REDIS_URL or "")
        await redis_client.flushdb()
    await close_redis_client()


def _guard_not_the_dev_redis(url: str) -> None:
    """FLUSHDB is irreversible: refuse it on anything but a throwaway test Redis, the way
    _guard_not_the_dev_database refuses the dev/prod database. `make test` runs
    luxanalytics_testredis; pytest inside the dev app container would otherwise wipe the dev
    stack's Redis (rate-limit state) after its first test."""
    host = urlsplit(url).hostname or ""
    if "test" not in host.lower():
        raise RuntimeError(
            f"REDIS_URL does not look like a disposable test Redis: {url!r}. The suite flushes it "
            "after every test; run it with `make test`, which starts the throwaway one."
        )


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


# luxarch:route-smoke asserter v20 — DO NOT edit the marker line above (fw.route_smoke_wired finds it).
#
# WHY THIS FILE EXISTS
# A dependency deprecates an API; your code keeps working (warning only); a later pin makes it RAISE;
# `poetry.lock` bumps — and the code is now broken with nothing failing. Not audit (a deprecation is
# not a CVE), not mypy (the attribute still exists; the behaviour changed), not the lock diff. It bit
# one repo twice in six weeks (Starlette TemplateResponse arg order; pydantic `model_fields` instance
# access), both caught by luck. The canonical pytest config already has `filterwarnings = error` — the
# exact mechanism that turns a DeprecationWarning into a failing test one version EARLY. It was inert
# because no test executed the line. The surfaces where deprecations bite hardest (admin pages, error
# handlers, exports) are the least covered. So the fix is not a warning filter — it is: EVERY registered
# route is exercised by the suite, so `filterwarnings = error` actually fires everywhere it matters.
#
# WHAT THIS DOES
# Records every route the suite hits (via a Starlette-level patch — client/app-agnostic) and, at the
# end of the session, fails if any registered HTTP/WebSocket route was never requested. Binary and
# un-gameable: it is the FACT (the route ran), not line-coverage %, not a static guess.
#
# HOW TO WIRE IT (see: luxarch --playbook route-smoke)
#   1) Save this as tests/conftest.py, or paste its body into your existing tests/conftest.py.
#   2) Set APP_IMPORT below to your app factory / instance import.
#   3) Keep `filterwarnings = error` in your pytest config (canonical). Route-smoke + that pairing is
#      what catches the deprecation BEFORE the pin that makes it fatal — either alone is insufficient.
#   4) Cover the hard routes for real: log in through the REAL login route (never forge a cookie or
#      override the auth dependency); assert a route was REQUESTED, not a response shape (fragment
#      endpoints legitimately return bare HTML); reset the rate-limit counter, don't disable the limiter.
#      For the canonical PATTERNS as a fill-in scaffold, run `luxarch --emit route-smoke-example` — the
#      technique is versioned in the image so you copy it, never a drifting other-repo's tests.
#   5) Exempt only what genuinely has no test surface (health/metrics) — with a comment saying why.
#
# NOTE FOR ADOPTERS AND FOR WHOEVER EDITS THIS ASSET NEXT: this block carries NO module-level imports,
# deliberately. It is pasted BELOW existing code in an existing tests/conftest.py, where
# `from __future__ import …` is a SyntaxError (it must be the first statement) and any other import is
# ruff E402 — and luxlint's emitted-asset exemption drops T201/T203/PLC0415, NOT E402. So an import
# here leaves an adopter choosing between editing a DO-NOT-EDIT block and carrying a permanent red.
# v9 had none; v11 added two and cost an adopter exactly that. Import inside a function if
# you ever need one — PLC0415's exemption already covers that.

# --- EDIT THIS: how your app object is imported (the thing that owns the route table). ---
APP_IMPORT = "app.main:app"  # "package.module:attribute"

# --- EDIT THIS: routes a test DELIBERATELY drives to a 5xx (error-handler coverage). Each needs a
# why. Anything not listed here that returns 5xx during the suite is a real bug and fails the run. ---
EXPECT_5XX: frozenset[str] = frozenset(
    {
        # "/__error__",  # exercises the 500 handler on purpose
    }
)

# --- EDIT THIS: GET routes that ALWAYS redirect by design, and that the suite therefore only ever
# sees as a 3xx. Each needs a why. Ships EMPTY. One repo's suite logged its session-scoped
# client out in one test file, and because `test_route_smoke.py` sorts after it, ALL 46 authenticated
# routes were requested logged out. Each returned 307 to the login page, each satisfied
# `status_code < 500`, and the suite proved the login redirect worked and nothing else. A genuine 500
# — a template reading a context key the handler never set — passed 417 tests.
#
# The bar was "the route RAN", and an auth bounce means the HANDLER never ran: only the middleware
# did. So a GET route the suite never saw produce a non-3xx has not been covered, whatever `_HIT`
# says. Scoped to GET because a POST/PUT/DELETE that always redirects is post-redirect-get and
# correct — excluding those by STRUCTURE rather than by allowlist is what keeps this list short.
#
# Note the shape of the escape: that case would have needed 46 entries here. One line is a
# considered exception; forty-six is a confession.
EXPECT_3XX: frozenset[str] = frozenset(
    {
        # "/logout",       # redirects to login by design; the POST path asserts the cookie is cleared
        # "/",             # permanent redirect to /dashboard
        "/logout",  # always redirects to /login; test_logout_clears_the_session asserts the session is gone
        "/",  # always redirects to /dashboard/overview (test_root_redirects_to_the_dashboard)
    }
)

# --- EDIT THIS: routes with no meaningful test surface. Keep it short; each needs a why. ---
# Ships EMPTY, with the common cases commented — like EXPECT_5XX above. It used to ship `/health`
# and `/metrics` LIVE as "examples", and that is what went wrong: a repo inherited an
# exemption for `/metrics`, the route was never registered (`get_metrics()` had no caller, so the
# Prometheus registry could never be scraped), and the pre-written comment — "prometheus scrape — no
# behaviour to assert" — made an absence that nobody had reviewed read as a decision somebody had.
# An exemption you did not write is the most expensive kind: it survives onboarding and a full
# burn-down because it looks considered. Uncomment only what this app actually serves.
EXEMPT: frozenset[str] = frozenset(
    {
        # "/health",   # liveness probe — no behaviour to assert
        # "/metrics",  # prometheus scrape — no behaviour to assert
    }
)

# ── recording (do not edit below) ──────────────────────────────────────────────────────────────
_HIT: set[int] = (
    set()
)  # ids of route objects that were exercised (see _patch_route_class)
_OUTCOMES = {"passed": 0, "failed": 0, "skipped": 0}
# route id -> the 5xx statuses the suite saw from it. --playbook route-smoke line 84
# claims route-smoke "catches ordinary logic bugs that 500 on first request", but nothing recorded
# status, so that sentence was only true when a test author happened to assert on it. Four prod
# 500s shipped while this asserter reported PASS. Recording status is NOT a response-SHAPE
# assertion — a bare-HTML fragment still passes; only a 5xx fails.
_SERVER_ERRORS: dict[int, set[int]] = {}
# route id -> the (status, location-path) pairs the suite saw, and the set of routes that produced at
# least one NON-redirect response. Together these answer "did the handler ever actually run?", which
# `_HIT` cannot: a 307 from auth middleware records a hit for a handler that never executed
# (the logged-out suite above). A 4xx counts as rendered — an explicit refusal is the route answering, where a
# silent bounce to a login page is not.
_REDIRECTS: dict[int, set[tuple[int, str]]] = {}
_RENDERED: set[int] = set()
# Under `pytest -n` (xdist) every worker is its own process with its own recorder, and the CONTROLLER,
# where pytest_sessionfinish certifies, runs no test at all: it saw 0 requests and refused a green run
# outright. Route ids differ per process, so each worker translates its ids to (host, method,
# path) before handing them back, and the controller maps them onto its own ids.
_WORKER_PAYLOADS: list[dict[str, list[object]]] = []


def pytest_runtest_logreport(report) -> None:  # type: ignore[no-untyped-def]
    # Count outcomes so the 0-hits net can tell "the request suite SKIPPED (e.g. a DB-less run)" from
    # "tests ran but the recorder saw nothing (real misconfig)". A real skip is decided
    # at SETUP. CRITICAL: an xfail RAN — pytest reports it at the CALL phase with outcome=="skipped" and
    # `wasxfail` set, so it must NOT be counted as a skip, or a single xfail (the fleet's own
    # known-broken idiom) would disarm net #2 and route-smoke would certify nothing.
    if report.when == "setup" and report.outcome == "skipped":
        _OUTCOMES["skipped"] += 1
    elif report.when == "call":
        outcome = report.outcome
        if outcome == "skipped" and getattr(report, "wasxfail", False):
            outcome = "xfailed"  # an xfail executed; it is not a skip
        _OUTCOMES[outcome] = _OUTCOMES.get(outcome, 0) + 1


def _patch_route_class(cls, match_full) -> None:  # type: ignore[no-untyped-def]
    # Record the route OBJECT'S IDENTITY, not (method, path). The recorder only ever sees `self`, which
    # knows its UNPREFIXED path, while the mount prefix lives on the parent wrapper — so a string key
    # could never agree between recording and enumeration (they'd both have to reconstruct the full
    # path, and the recorder can't). Keying on `id(route)` makes the two sides agree BY CONSTRUCTION:
    # enumeration walks the same objects and records their ids. (Proven end-to-end on a real repo.)
    # Record on BOTH matches() (fires on a FULL match even if a dep later 403s — import-time deprecations
    # still caught) and handle() (body dispatch). Idempotent per class; `_HIT` is a set so a super()
    # double-record is harmless.
    if getattr(cls.handle, "_luxarch_route_smoke", False):
        return
    _orig_matches = cls.matches

    def matches(self, scope):  # type: ignore[no-untyped-def]
        result = _orig_matches(self, scope)
        try:
            if result[0] == match_full:
                _HIT.add(id(self))
        except Exception:
            pass
        return result

    matches._luxarch_route_smoke = True  # type: ignore[attr-defined]
    cls.matches = matches

    _orig_handle = cls.handle

    async def handle(self, scope, receive, send):  # type: ignore[no-untyped-def]
        _HIT.add(id(self))

        async def _send(message):  # type: ignore[no-untyped-def]
            # `http.response.start` carries the status. Wrapped rather than inspected after the fact
            # so it works for any client and for streaming responses alike.
            try:
                if message.get("type") == "http.response.start":
                    status = int(message.get("status", 0))
                    if status >= 500:
                        _SERVER_ERRORS.setdefault(id(self), set()).add(status)
                    if 300 <= status < 400:
                        loc = ""
                        for k, v in message.get("headers") or ():
                            if k.lower() == b"location":
                                loc = v.decode("latin-1", "replace")
                                break
                        # Query string dropped: `?next=/admin/users` differs per route and would
                        # hide that a dozen routes all bounce to the SAME page.
                        _REDIRECTS.setdefault(id(self), set()).add(
                            (status, loc.split("?")[0])
                        )
                    else:
                        _RENDERED.add(id(self))
            except Exception:
                pass
            return await send(message)

        return await _orig_handle(self, scope, receive, _send)

    handle._luxarch_route_smoke = True  # type: ignore[attr-defined]
    cls.handle = handle


def _install_recorder() -> None:
    # CRITICAL: FastAPI's APIRoute OVERRIDES both handle() and matches() (subclass wins), and FastAPI
    # dispatches by calling `original_route.handle(...)` on the APIRoute directly — so patching only
    # the Starlette parent Route recorded ZERO hits (v1 patched handle, v2 added matches; both missed).
    # Patch APIRoute FIRST, then the Starlette Route (WebSocketRoute etc. still go through Route). The
    # `Match.FULL` enum is shared. (Root-caused, and the fix confirmed live in an adopting repo.)
    import starlette.routing as _r

    _patch_route_class(_r.Route, _r.Match.FULL)
    try:
        import fastapi.routing as _fr

        _patch_route_class(_fr.APIRoute, _r.Match.FULL)
    except (
        Exception
    ):  # no FastAPI (shouldn't happen for a fastapi-web repo) — Route patch stands
        pass


_install_recorder()


def _registered_route_entries() -> list[tuple[str, str, str, int]]:
    # (HOST, method, FULL prefixed path, id(route)). The id is what matching keys on (agrees with the
    # recorder by construction). The HOST is carried down so shadow-detection keys on (host, method,
    # path): a Host()-composed app reaches the SAME route object once per hostname, and different
    # sub-apps legitimately share `/`, `/docs`, … — neither is a shadow, because Starlette matches
    # exactly ONE host per request (the v8 false positive). The prefix is NOT on the route —
    # it's on the wrapper: `_IncludedRouter.include_context.prefix` (empty on `.original_router.prefix`)
    # and `Mount.path`.
    mod, _, attr = APP_IMPORT.partition(":")
    import importlib

    app = getattr(importlib.import_module(mod), attr or "app")
    entries: list[tuple[str, str, str, int]] = []

    def _walk(routes, prefix="", host="") -> None:  # type: ignore[no-untyped-def]
        for route in routes:
            # Starlette >=1.x wraps include_router() results instead of flattening — the real routes hang
            # off `.original_router.routes`, and the include prefix lives on the wrapper's include_context.
            inner = getattr(route, "original_router", None)
            if inner is not None:
                ictx = getattr(route, "include_context", None)
                _walk(inner.routes, prefix + (getattr(ictx, "prefix", "") or ""), host)
                continue
            sub = getattr(route, "routes", None)
            kind = type(route).__name__
            if kind == "Mount":  # a Mount (sub-app) carries its prefix on `.path`
                mpath = prefix + (getattr(route, "path", "") or "")
                # Record the mount's OWN path with a sentinel method. A Mount has no `.methods` and a
                # bare ASGI sub-app (`app.mount("/metrics", make_asgi_app())`) has no `.routes`
                # either, so it used to vanish from the walk entirely — invisible to BOTH halves:
                # never reportable as uncovered, and, once v13 added the inverse check, never
                # exemptable either, because the exemption read as stale. The one surface this
                # asserter cannot check was also the one it refused to let you document, and the
                # message said "describing a route that does not exist" about a route that was
                # serving 200s.
                #
                # The sentinel keeps it out of the COVERAGE requirement — a third-party ASGI app's
                # internals are not this asserter's business — while making it visible to the stale
                # check.
                entries.append((host, "MOUNT", mpath, id(route)))
                if sub:
                    _walk(sub, mpath, host)
                continue
            if (
                sub is not None and kind == "Host"
            ):  # discriminates on HOSTNAME → no path prefix, new host
                _walk(
                    sub,
                    prefix,
                    getattr(route, "host", "") or getattr(route, "path", "") or host,
                )
                continue
            path = prefix + getattr(route, "path", "")
            methods = getattr(route, "methods", None)
            if methods:  # APIRoute / Route
                for m in set(methods) - {"HEAD", "OPTIONS"}:
                    entries.append((host, m, path, id(route)))
            elif type(route).__name__ == "WebSocketRoute":
                entries.append(
                    (
                        host,
                        "WS",
                        prefix
                        + str(
                            getattr(route, "path_format", None)
                            or getattr(route, "path", "")
                            or ""
                        ),
                        id(route),
                    )
                )

    _walk(app.routes)
    return entries


# Below this many registered routes, an app with included routers is almost certainly mis-walked
# (the flat-walk bug), not genuinely tiny — fail loud rather than "certify" a handful.
_MIN_PLAUSIBLE_ROUTES = 5


def _session_is_narrowed(session) -> bool:  # type: ignore[no-untyped-def]
    """True when this run deliberately selected a SUBSET of the suite.

    Net #2 exists to catch a blind RECORDER on a run where request tests ran and
    recorded nothing. A focused run (`pytest tests/unit/x.py`, `-k`, a node id) selects no request
    tests at all, so zero hits is the correct outcome — not evidence of misconfiguration. The only
    stand-down signal was the skip count, and a narrowed selection has 0 skips, so every focused
    unit-test run "failed". That is the everyday TDD loop, and a red exit there trains people to
    ignore the exit code, which blunts the loud-fail nets that actually matter.

    Certification requires the WHOLE configured suite; anything narrower says so and stands down."""
    # If narrowing cannot be determined, answer NOT narrowed so every loud-fail net still fires. The
    # opposite default would let an unrecognised session shape stand the asserter down silently —
    # a hollow green, which is the failure this whole file exists to prevent.
    opt = getattr(getattr(session, "config", None), "option", None)
    if opt is None:
        return False
    if getattr(opt, "keyword", "") or getattr(opt, "markexpr", ""):
        return True  # -k / -m
    if getattr(session, "deselected", 0):
        return True
    try:
        testpaths = list(session.config.getini("testpaths") or [])
    except Exception:
        testpaths = []
    try:
        args = [a for a in (session.config.args or []) if not a.startswith("-")]
    except Exception:
        return False
    if not args:
        return False  # bare `pytest` with testpaths from config — the full suite
    # Explicit args that are not exactly the configured testpaths = a deliberate subset. A node id
    # (`file::test`) is always narrower than a path.
    if any("::" in a for a in args):
        return True
    import os.path

    # The string comparison below once reported EVERY run as narrowed under the fleet's own
    # canonical config, so route-smoke certified nothing on any repo using it — while
    # fw.route_smoke_wired stayed green and the stand-down message read like normal operation ("run
    # the full suite to certify", when the full suite is what ran). The reporter's probe on a real
    # `make test`:
    #
    #     rootdir /   inifile /pytest.ini   testpaths ['tests']   args ['/w']   deselected 0
    #
    # `luxlint --emit-config pytest` ships `testpaths = tests`, and the fleet pattern mounts that
    # file read-only and runs `pytest -c /pytest.ini` with the repo at the work dir. rootdir becomes
    # `/`, so `testpaths` cannot resolve against it and pytest falls back to collecting from the cwd,
    # setting args to the work dir. Collection is complete; only the comparison breaks. Two guards
    # disagreeing about the same prescribed setup, with the asserter the one that lost.
    #
    # So: pytest's own fallback is not a deliberate subset.
    root = os.path.normpath(str(getattr(session.config, "rootpath", "") or ""))
    here = os.path.normpath(os.getcwd())
    if len(args) == 1 and os.path.normpath(args[0]).rstrip("/") in {root, here}:
        return False
    # v16 also had an "ini file outside the rootdir -> not narrowed" leg here. It is GONE, because it
    # was redundant AND harmful. Redundant: the rootdir/cwd check above already handles
    # every case the narrowed-run report raised — verified against the reporter's probe. Harmful: it returned
    # not-narrowed OUTRIGHT, discarding the args, so `pytest -c /cfg/pytest.ini --rootdir=/app
    # tests/test_tool_gate.py` read as the full suite, recorded zero route hits and the zero-hits net
    # REFUSED. That is the focused-run failure — the everyday focused run goes red — coming back
    # through a new leg. The comment beside it said it should only skip the testpaths COMPARISON; the
    # code switched off the whole determination. Correct intent, broader implementation.
    #
    # Deleted rather than reordered: with the rootdir/cwd check present it has no remaining job, and a
    # leg that does nothing is one someone later has to work out the purpose of.
    #
    # Args are compared BOTH as written and relative to the cwd/rootdir, so `pytest /app/tests` with
    # `testpaths = tests` is recognised as the full suite rather than standing the asserter down —
    # a stand-down is a hollow green, and the absolute spelling of a configured testpath is not a
    # deliberate subset.
    want = {os.path.normpath(t).rstrip("/") for t in testpaths}
    got: set[str] = set()
    for a in args:
        n = os.path.normpath(a).rstrip("/")
        got.add(n)
        for base in (here, root):
            if base and n.startswith(base.rstrip("/") + os.sep):
                got.add(n[len(base.rstrip("/")) + 1 :])
    if not want:
        # No testpaths to compare against: an explicit FILE is the only thing that still reads as a
        # deliberate subset. A directory arg with no configured testpaths could be the whole suite.
        return any(a.endswith(".py") for a in args)
    # COVERAGE, not intersection. `pytest tests` under `testpaths = ["tests", "integration"]` runs half
    # the suite, and an intersection test would call that the full one — a stand-down, which is a
    # hollow green. Every configured testpath must be named for this to be the whole suite.
    return not want <= got


def _by_rid(
    entries: list[tuple[str, str, str, int]],
) -> dict[int, list[tuple[str, str, str]]]:
    out: dict[int, list[tuple[str, str, str]]] = {}
    for h, m, p, rid in entries:
        out.setdefault(rid, []).append((h, m, p))
    return out


def _worker_payload(
    entries: list[tuple[str, str, str, int]],
) -> dict[str, list[object]]:
    """This worker's recordings, keyed by (host, method, path) so another process can read them."""
    by_rid = _by_rid(entries)

    def keyed(rids: set[int]) -> list[tuple[str, str, str]]:
        return sorted({k for r in rids for k in by_rid.get(r, ())})

    return {
        "hit": [list(k) for k in keyed(_HIT)],
        "rendered": [list(k) for k in keyed(_RENDERED)],
        "server_errors": [
            [list(k), sorted(v)]
            for r, v in _SERVER_ERRORS.items()
            for k in by_rid.get(r, ())
        ],
        "redirects": [
            [list(k), [list(x) for x in sorted(v)]]
            for r, v in _REDIRECTS.items()
            for k in by_rid.get(r, ())
        ],
    }


def _route_key(o: object) -> tuple[str, ...] | None:
    return tuple(str(x) for x in o) if isinstance(o, list) else None


def _merge_worker_payloads(entries: list[tuple[str, str, str, int]]) -> None:
    rid_by_key: dict[tuple[str, ...], int] = {
        (h, m, p): rid for h, m, p, rid in entries
    }

    def rid_of(o: object) -> int | None:
        key = _route_key(o)
        return rid_by_key.get(key) if key is not None else None

    for payload in _WORKER_PAYLOADS:
        for k in payload.get("hit", []):
            if (rid := rid_of(k)) is not None:
                _HIT.add(rid)
        for k in payload.get("rendered", []):
            if (rid := rid_of(k)) is not None:
                _RENDERED.add(rid)
        for item in payload.get("server_errors", []):
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[1], list)
                and (rid := rid_of(item[0])) is not None
            ):
                _SERVER_ERRORS.setdefault(rid, set()).update(
                    int(str(st)) for st in item[1]
                )
        for item in payload.get("redirects", []):
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[1], list)
                and (rid := rid_of(item[0])) is not None
            ):
                _REDIRECTS.setdefault(rid, set()).update(
                    (int(str(x[0])), str(x[1]))
                    for x in item[1]
                    if isinstance(x, list) and len(x) == 2
                )


def _xdist_merge_hook() -> object:
    # The controller collects each worker's payload as the worker goes down. `pytest_testnodedown` is
    # xdist's own hook, so it is marked `optionalhook=True` (inert without xdist), and the decorator
    # needs `pytest`, which this block may only import inside a function (see the NOTE above).
    # v19: built here and bound below rather than registered from a `pytest_configure`. v18 defined
    # `pytest_configure`, and this block is pasted into the repo's own conftest, where Python keeps
    # only the LAST def of a name: it silently replaced the repo's hook (one repo lost three
    # dynamically registered markers vanished, 12 files failed collection under --strict-markers).
    # This block defines no hook name a repo's conftest would also define.
    import pytest

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(node: object, error: object) -> None:
        payload = getattr(node, "workeroutput", {}).get("luxarch_route_smoke")
        if payload:
            _WORKER_PAYLOADS.append(payload)

    return pytest_testnodedown


pytest_testnodedown = _xdist_merge_hook()


def pytest_sessionfinish(session, exitstatus) -> None:  # type: ignore[no-untyped-def]
    # Only CERTIFY on a green run — a red suite's route list is untrustworthy (a failed test may have
    # aborted before its route ran). But say so OUT LOUD: a silent skip lets coverage lapse invisibly
    # while the suite is red. Non-enforcing by design; just visible.
    if exitstatus not in (0, None):
        print(
            "\nluxarch route-smoke: INERT — suite exitstatus="
            f"{exitstatus}, route certification SKIPPED. NO route is certified until the failing "
            "test(s) are fixed. See luxarch --playbook route-smoke."
        )
        return
    try:
        entries = _registered_route_entries()
    except (
        Exception
    ) as exc:  # app import misconfigured — make it loud, not silently green
        raise SystemExit(
            f"luxarch route-smoke: could not import the app via APP_IMPORT={APP_IMPORT!r} "
            f"({exc}). Set it to your app factory/instance. See luxarch --playbook route-smoke."
        ) from exc
    workeroutput = getattr(getattr(session, "config", None), "workeroutput", None)
    if workeroutput is not None:
        # An xdist WORKER: hand the recordings to the controller and certify nothing here. Each worker
        # saw only its share of the suite, so a per-worker verdict would be wrong in both directions.
        workeroutput["luxarch_route_smoke"] = _worker_payload(entries)
        return
    _merge_worker_payloads(entries)
    # Loud-fail net #3: a duplicate (HOST, method, path) — from DISTINCT route objects — means the app
    # registered the same method+path twice under one host, so one handler shadows the other (the
    # shadowed one is dead/unreachable). Keyed on HOST because a Host()-composed app reaches the same
    # route under N hostnames and different sub-apps share `/`, `/docs`, … — those are not shadows
    # (Starlette matches exactly one host per request), so keying on (method, path) alone false-flagged
    # thousands (v8). A collision is real only when two DIFFERENT route ids share it.
    seen: dict[tuple[str, str, str], int] = {}
    shadowed: set[tuple[str, str, str]] = set()
    for h, m, p, rid in entries:
        k = (h, m, p)
        if k in seen and seen[k] != rid:
            shadowed.add(k)
        else:
            seen.setdefault(k, rid)
    if shadowed:
        dupes = sorted(f"{m} {p}" + (f" @{h}" if h else "") for (h, m, p) in shadowed)
        raise SystemExit(
            f"luxarch route-smoke: {len(shadowed)} (host, method, path) registered by more than one "
            f"handler — one SHADOWS the other (the shadowed one is dead/unreachable). Remove the dead "
            f"handler. Duplicates: {dupes}. See --playbook route-smoke."
        )
    # Loud-fail net #1: implausibly few (INCLUDING ZERO) routes → the enumerator is mis-walking, NOT a
    # 2-route app. ZERO is the most certain "the walk is broken" signal, so it must fail LOUDEST — the
    # old `0 < len(entries)` guard excluded zero, so a walk-to-nothing certified GREEN silently, the exact
    # hollow green this net exists to prevent.
    if not entries:
        raise SystemExit(
            "luxarch route-smoke: enumerated 0 routes — the walk found NOTHING, so this run certifies "
            "nothing (a hollow green). Either APP_IMPORT is wrong, or the app composes its routes in a "
            "container the walker doesn't descend (a Host()/Mount sub-app, an unusual router wrapper). "
            "Refusing to certify. Re-emit the asserter (luxarch --emit route-smoke) and check APP_IMPORT. "
            "See --playbook route-smoke."
        )
    if len(entries) < _MIN_PLAUSIBLE_ROUTES:
        raise SystemExit(
            f"luxarch route-smoke: only {len(entries)} route(s) enumerated — implausibly few for an "
            "app with included routers, so the asserter is almost certainly mis-walking the route table "
            "(Starlette version?), NOT genuinely tiny. Refusing to certify — a low count would be a "
            "hollow green. Update the asserter (luxarch --emit route-smoke). See --playbook route-smoke."
        )
    # Loud-fail net #2: routes exist but NOTHING was recorded. Two cases, cleanly separable by the skip
    # count: if the request suite SKIPPED (a DB-less `make test` → 0 hits / many skipped),
    # route-smoke simply wasn't exercised this run — that's NOT a misconfiguration; stand down (the
    # DB-full run enforces coverage). Only when tests actually RAN and still recorded nothing is the
    # recorder blind (wrong app / bypassing TestClient) — THAT is the misconfig to fail on.
    if entries and not _HIT:
        if _session_is_narrowed(session):
            print(
                "luxarch route-smoke: not evaluated — this run selected a SUBSET of the suite, which "
                "need not contain any route test. Certification requires the full suite."
            )
            return
        if _OUTCOMES["skipped"]:
            print(
                f"luxarch route-smoke: not evaluated — 0 requests observed but {_OUTCOMES['skipped']} "
                "test(s) skipped, so the request suite didn't run this pass (DB-less?). The DB-full run "
                "enforces coverage."
            )
            return
        raise SystemExit(
            "luxarch route-smoke: 0 requests were observed across the whole suite while "
            f"{len(entries)} routes are registered and nothing skipped. TWO causes look identical from "
            "zero hits: (a) the recorder isn't observing — wrong APP_IMPORT, or a TestClient that "
            "bypasses the app (e.g. Starlette 1.x needs httpx2 installed, or the test network has no DNS "
            "for db/redis so every request dies before routing); or (b) the suite has NO route tests "
            "yet. Make ONE real request — a single public-page GET — to disambiguate: if it records, "
            "it's (b), you have real coverage gaps to fill (KEEP that probe, it's load-bearing — remove "
            "the last route test and this refusal returns). If it still doesn't record, it's (a), fix "
            "the wiring. Under `pytest -n` the workers' recordings are merged here (asserter v18+); an "
            "OLDER asserter under xdist shows exactly this message on a healthy suite, so re-emit it "
            "(luxarch --emit route-smoke) before hunting (a). Refusing to certify on zero requests "
            "either way. See --playbook route-smoke."
        )
    # Loud-fail net #4: a route the suite REQUESTED returned 5xx. `--playbook
    # route-smoke` line 84 claims route-smoke "catches ordinary logic bugs that 500 on first request",
    # but nothing looked at status, so the claim held only where a test author happened to assert on
    # it. A test that requests POST /vendors and gets a 500 satisfied the old asserter completely;
    # four such 500s shipped to production while this file reported PASS.
    #
    # This is NOT the response-SHAPE assertion the playbook warns against — a fragment endpoint
    # returning bare HTML still passes. Only a 5xx fails, and EXPECT_5XX exempts a route a test drives
    # to an error on purpose.
    if _SERVER_ERRORS:
        by_rid = {rid: (m, p_) for (_h, m, p_, rid) in entries}
        bad = sorted(
            f"{by_rid[rid][0]:4} {by_rid[rid][1]}  -> {sorted(codes)}"
            for rid, codes in _SERVER_ERRORS.items()
            if rid in by_rid and by_rid[rid][1] not in EXPECT_5XX
        )
        if bad:
            session.exitstatus = 1
            raise SystemExit(
                f"luxarch route-smoke: {len(bad)} route(s) returned 5xx to the suite. The request was "
                "recorded, so route coverage looks satisfied — but the route is BROKEN. Fix it, or if "
                "a test drives it to an error deliberately, add the path to EXPECT_5XX with a reason:"
                "\n  " + "\n  ".join(bad) + "\nSee luxarch --playbook route-smoke."
            )
    # Loud-fail net #5: every response a GET route gave the suite was a REDIRECT.
    # `_HIT` records that the route MATCHED, which an auth bounce satisfies — the middleware ran and
    # the handler did not. A reporting repo logged its session-scoped client out in one test file, and
    # because the smoke file sorts alphabetically after it, all 46 authenticated routes were requested
    # logged out: 46 x 307-to-login, every assertion satisfied, a real 500 sailing through 417 tests.
    #
    # Scoped to GET by STRUCTURE: a POST/PUT/DELETE that always redirects is post-redirect-get and
    # correct, so excluding it needs no allowlist entry and cannot rot.
    methods_by_rid: dict[int, set[str]] = {}
    for _h, m, _p, rid in entries:
        methods_by_rid.setdefault(rid, set()).add(m)
    bounced = {
        rid: (p_, sorted(_REDIRECTS[rid]))
        for (_h, _m, p_, rid) in entries
        if rid in _REDIRECTS
        and rid not in _RENDERED
        and "GET" in methods_by_rid.get(rid, set())
        and p_ not in EXPECT_3XX
        and p_ not in EXEMPT
    }
    if bounced:
        # Group by target. One route redirecting is business logic; a dozen routes redirecting to the
        # SAME page is a gate, and naming that page turns a list of failures into one diagnosis.
        targets: dict[str, int] = {}
        for _p, obs in bounced.values():
            for _st, loc in obs:
                targets[loc] = targets.get(loc, 0) + 1
        lines = sorted(
            f"GET  {p_}  -> "
            + ", ".join(f"{st} -> {loc or '(no Location)'}" for st, loc in obs)
            for p_, obs in bounced.values()
        )
        shared = max(targets.items(), key=lambda kv: kv[1]) if targets else ("", 0)
        hint = ""
        if shared[1] >= 3:
            hint = (
                f"\n{shared[1]} of them redirect to the SAME target ({shared[0]}). That is the shape "
                "of an AUTHENTICATION bounce, not of business logic — the usual cause is a shared, "
                "session-scoped client that some earlier test logged out and never signed back in. "
                "Check whether a logout test leaves the fixture authenticated for the files that sort "
                "after it."
            )
        session.exitstatus = 1
        raise SystemExit(
            f"luxarch route-smoke: {len(bounced)} GET route(s) NEVER returned a non-redirect to the "
            "suite. The request was recorded, so coverage looks satisfied — but a 3xx means the "
            "HANDLER did not run, so nothing in it was exercised and a deprecation or a broken "
            "template inside it ships latent. Request them in a state that renders, or list a route "
            "that redirects BY DESIGN in EXPECT_3XX with a reason:\n  "
            + "\n  ".join(lines)
            + hint
            + "\nSee luxarch --playbook route-smoke."
        )
    # Covered iff THAT route object was exercised (id match) — the two sides agree by construction. One
    # route object reached under N hostnames is ONE route to cover, so collapse on route identity (rid)
    # — else a Host()-composed app reports the same uncovered route once per host (the v8 false positive).
    # The INVERSE check, and it is the more dangerous direction. Subtracting EXEMPT from the
    # registered routes trusted the list without ever asking whether the exempted route EXISTS. A
    # repo carried `/metrics` in EXEMPT from onboarding, commented "prometheus scrape — no behaviour
    # to assert", and the route was never registered: `get_metrics()` had no caller and the registry
    # could never be scraped. That survived onboarding, a full guard burn-down, and
    # `fw.route_smoke_wired` reporting PASS throughout, and was found only by reading /metrics by
    # hand. The exemption did not merely fail to catch the gap — it is WHY nothing looked wrong: a
    # missing route with no exemption reads as untested or absent, while a missing route WITH one
    # reads as a deliberate, reviewed decision.
    #
    # Deliberately NOT skipped on a narrowed run: a subset selection still REGISTERS every route, so
    # unlike `missing` this count is meaningful even from one test.
    # Mounts included on purpose: a mount IS a registered surface, so exempting one is legitimate.
    registered_paths = {p for (_h, _m, p, _rid) in entries}
    # `app.mount("/metrics", ...)` serves at `/metrics/`; a redirect from `/metrics` is the normal
    # shape, so accept either spelling in EXEMPT rather than making the trailing slash load-bearing.
    registered_paths |= {p.rstrip("/") for p in registered_paths}
    registered_paths |= {p + "/" for p in registered_paths if not p.endswith("/")}
    # Every path list gets this, not just EXEMPT. The `/metrics` case was about EXEMPT, but EXPECT_5XX has
    # carried the identical hazard since it was added and nothing checked it: a path naming no
    # registered route reads as a reviewed decision while actually hiding the route's absence. Fixing
    # one instance of a class and leaving its siblings is how the same bug gets reported twice.
    stale = sorted(
        f"{name}: {path}"
        for name, paths in (
            ("EXEMPT", EXEMPT),
            ("EXPECT_5XX", EXPECT_5XX),
            ("EXPECT_3XX", EXPECT_3XX),
        )
        for path in paths - registered_paths
    )
    if stale:
        session.exitstatus = 1
        raise SystemExit(
            "luxarch route-smoke: "
            f"{len(stale)} exemption path(s) match no registered route — the entry is describing a "
            "route that does not exist, which HIDES its absence rather than documenting a decision. "
            "Remove the entry, or register the route:\n  "
            + "\n  ".join(stale)
            + "\nSee luxarch --playbook route-smoke."
        )
    missing_by_rid = {
        rid: f"{m:4} {p}"
        for (h, m, p, rid) in entries
        if rid not in _HIT and p not in EXEMPT and m != "MOUNT"
    }
    missing = sorted(missing_by_rid.values())
    if missing and _session_is_narrowed(session):
        print(
            f"luxarch route-smoke: not evaluated — {len(missing)} route(s) unrequested, but this run "
            "selected a SUBSET of the suite so that count is meaningless. Run the full suite to certify."
        )
        return
    if missing:
        session.exitstatus = 1
        raise SystemExit(
            "luxarch route-smoke: "
            f"{len(missing)} registered route(s) were NEVER requested by the suite — a deprecation "
            "in any of them ships latent (fine on the running pin, fatal on the next). Add a test "
            "that requests each, or EXEMPT it with a reason:\n  "
            + "\n  ".join(missing)
            + "\nSee luxarch --playbook route-smoke."
        )
