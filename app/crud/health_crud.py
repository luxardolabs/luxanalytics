"""Health probes against the database engine: the only SQL the health check runs."""

from sqlalchemy import literal, select
from sqlalchemy.pool import QueuePool

from app.db.database import async_engine


async def ping_database() -> None:
    """SELECT 1 on a fresh connection; raises SQLAlchemyError/OSError when the DB is unreachable."""
    async with async_engine.connect() as conn:
        await conn.execute(select(literal(1)))


def pool_counters() -> tuple[int, int, int, int] | None:
    """(size, checked_in, checked_out, overflow), when the engine runs a QueuePool.

    The async engine's AsyncAdaptedQueuePool is one; another pool class has no counters.
    """
    pool = async_engine.pool
    if not isinstance(pool, QueuePool):
        return None
    return pool.size(), pool.checkedin(), pool.checkedout(), pool.overflow()
