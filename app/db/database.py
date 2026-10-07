from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from app.core.config import settings
from app.db.pool_monitor import PoolMonitor

# Re-export Base from models.base for backward compatibility
from app.models.base import Base  # noqa: F401

logger = structlog.get_logger(__name__)

# Log the pool configuration
logger.info(
    "Creating async engine with pool settings",
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_POOL_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    database_url=(
        settings.DATABASE_URL.split("@")[1]
        if "@" in settings.DATABASE_URL
        else "invalid"
    ),
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


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """Get database session - auto-commits on success."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_async_db_session():
    """Get database session as async context manager."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_monitored_db() -> AsyncSession:  # type: ignore[misc]
    """Get database session with connection monitoring."""
    async with pool_monitor.get_connection_with_metrics():
        async with AsyncSessionLocal() as session:
            try:
                yield session
            finally:
                await session.close()
