"""Test fixtures with real PostgreSQL via testcontainers.

No SQLite — all tests run against the same database engine as production.
Alembic migrations are applied to the test database.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.db.database import get_db
from app.models.base import Base

# Import all models so Base.metadata is populated
from app.models import App, Device, Event  # noqa: F401


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def postgres_container():
    """Start a real PostgreSQL container for the test session."""
    with PostgresContainer("postgres:15-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def db_url(postgres_container):
    """Get the async database URL from the test container."""
    # testcontainers gives us a sync URL like postgresql://...
    sync_url = postgres_container.get_connection_url()
    # Convert to async psycopg URL
    return sync_url.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="session")
def test_engine(db_url):
    """Create async engine pointing at the test container."""
    return create_async_engine(db_url, echo=False)


@pytest_asyncio.fixture(scope="session")
async def setup_database(test_engine):
    """Create all tables once for the session using Base.metadata."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine, setup_database):
    """Get a fresh database session per test, rolled back after."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session):
    """Create a test HTTP client with db dependency overridden."""
    from app.main import create_application

    test_app = create_application()

    async def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    test_app.dependency_overrides.clear()


# ── Seed Data Fixtures ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sample_app(db_session):
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
    db_session.add(app)
    await db_session.flush()
    return app


@pytest_asyncio.fixture
async def sample_events(db_session):
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
                "device_id": "device_aaa", "device_model": "iPhone17,1",
                "system_version": "18.3", "app_version": "1.0.23",
                "screen": "home", "feature": "main",
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
                "device_id": "device_aaa", "device_model": "iPhone17,1",
                "button_name": "save", "screen": "settings",
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
            "properties": {"error_type": "network", "error_message": "timeout", "screen": "profile"},
            "event_metadata": {
                "device_id": "device_bbb", "error_type": "network",
                "error_message": "timeout", "screen": "profile",
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
            "properties": {"operation": "api_call", "duration_ms": "150", "success": "true"},
            "event_metadata": {
                "device_id": "device_aaa", "operation": "api_call",
                "duration_ms": "150", "success": "true",
            },
        },
    ]

    await db_session.execute(insert(Event).values(events_data))
    await db_session.flush()
    return events_data
