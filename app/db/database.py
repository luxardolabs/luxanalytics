import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.pool_monitor import PoolMonitor

# Re-export Base from models.base for backward compatibility
from app.models.base_model import Base  # noqa: F401

logger = logging.getLogger(__name__)

# Log the pool configuration
logger.info(
    "Creating async engine with pool settings",
    extra={
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_POOL_MAX_OVERFLOW,
        "pool_recycle": settings.DB_POOL_RECYCLE,
        "pool_pre_ping": settings.DB_POOL_PRE_PING,
        "database_url": settings.DATABASE_URL.split("@")[1]
        if "@" in settings.DATABASE_URL
        else "invalid",
    },
)

# Async engine with robust connection pooling
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_POOL_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_reset_on_return=settings.DB_POOL_RESET_ON_RETURN,
    connect_args={},
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

# Initialize pool monitor
pool_monitor = PoolMonitor(async_engine)


async def get_db() -> AsyncIterator[AsyncSession]:
    """The REQUEST transaction owner (FastAPI dependency): commits on success, rolls back on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_context() -> AsyncIterator[AsyncSession]:
    """The NON-REQUEST transaction owner (jobs, scripts): commits on clean exit, rolls back on error.

    With get_db, the only two places a transaction is committed (luxarch --playbook db-mutations).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_monitored_db() -> AsyncIterator[AsyncSession]:
    """Get database session with connection monitoring."""
    async with (
        pool_monitor.get_connection_with_metrics(),
        AsyncSessionLocal() as session,
    ):
        try:
            yield session
        finally:
            await session.close()
