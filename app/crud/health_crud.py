"""Health probes against the database engine: the only SQL the health check runs."""

from sqlalchemy import literal, select
from sqlalchemy.pool import QueuePool

from app.db.database import AsyncSessionLocal, async_engine


async def ping_database() -> None:
    """SELECT 1 through the app's own sessionmaker; raises SQLAlchemyError/OSError when the DB is
    unreachable. A session, not a bare engine connection: every connection the app opens comes from the
    one factory (fw.test_sessions_isolated)."""
    async with AsyncSessionLocal() as session:
        await session.execute(select(literal(1)))


def pool_counters() -> tuple[int, int, int, int] | None:
    """(size, checked_in, checked_out, overflow), when the engine runs a QueuePool.

    The async engine's AsyncAdaptedQueuePool is one; another pool class has no counters.
    """
    pool = async_engine.pool
    if not isinstance(pool, QueuePool):
        return None
    return pool.size(), pool.checkedin(), pool.checkedout(), pool.overflow()
