"""The dashboard's session state: reads and writes of the one session-generation row.

Every write that starts a generation afresh picks it at random, so no cookie already issued carries
it: a reseeded or reset row signs every session out, never back in. Generations stay below 2**62,
so advancing one never leaves BIGINT.
"""

import secrets

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.database import get_db_context
from app.models.dashboard_session_model import SESSION_STATE_ROW, DashboardSessionState

_GENERATION_BITS = 62

_GENERATION = select(DashboardSessionState.generation).where(
    DashboardSessionState.id == SESSION_STATE_ROW
)


def _fresh_generation() -> int:
    return secrets.randbits(_GENERATION_BITS)


async def read_session_generation() -> int | None:
    """The current generation; None when the row is missing. A read only: every signed-in request
    asks it. Raises SQLAlchemyError/OSError when the database is unreachable."""
    async with get_db_context() as session:
        return (await session.execute(_GENERATION)).scalar_one_or_none()


async def advance_session_generation(from_generation: int) -> bool:
    """A logout by a session of `from_generation`: when that is still the current generation, it
    moves on by one (in one statement, so every earlier cookie is refused); a cookie a logout
    already revoked matches nothing and changes nothing. Whether it advanced. Raises
    SQLAlchemyError/OSError when the database is unreachable."""
    async with get_db_context() as session:
        advanced = await session.execute(
            update(DashboardSessionState)
            .where(
                DashboardSessionState.id == SESSION_STATE_ROW,
                DashboardSessionState.generation == from_generation,
            )
            .values(
                generation=DashboardSessionState.generation % (1 << _GENERATION_BITS)
                + 1
            )
            .returning(DashboardSessionState.generation)
        )
        return advanced.scalar_one_or_none() is not None


async def ensure_session_generation() -> None:
    """Write the row at a fresh generation when it is missing (app startup, and a database restored
    from a dump, which leaves the row out); an existing row is left as it is. No ON CONFLICT: it
    must not need the table's primary key, which a restore that failed partway may not have
    written."""
    async with get_db_context() as session:
        if (await session.execute(_GENERATION)).first() is None:
            await session.execute(
                insert(DashboardSessionState).values(
                    id=SESSION_STATE_ROW, generation=_fresh_generation()
                )
            )


async def reset_session_generation() -> bool:
    """Sign every session out by moving to a fresh generation: what a database restore needs,
    since a dump taken before dumps left the row out carries a generation that cookies revoked
    after it may hold. Replaces the row whole, without needing the primary key. False when the
    table does not exist yet (a dump from before migration 011: startup creates and seeds it)."""
    async with get_db_context() as session:
        table = await session.execute(
            select(func.to_regclass(DashboardSessionState.__tablename__))
        )
        if table.scalar_one() is None:
            return False
        await session.execute(delete(DashboardSessionState))
        await session.execute(
            insert(DashboardSessionState).values(
                id=SESSION_STATE_ROW, generation=_fresh_generation()
            )
        )
        return True
