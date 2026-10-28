"""The dashboard's session state: reads and writes of the one session-generation row.

Every write that starts a generation afresh picks it at random, so no cookie already issued carries
it: a reseeded or reset row signs every session out, never back in.
"""

import secrets

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.database import get_db_context
from app.models.dashboard_session_model import SESSION_STATE_ROW, DashboardSessionState

_GENERATION = select(DashboardSessionState.generation).where(
    DashboardSessionState.id == SESSION_STATE_ROW
)


def _fresh_generation() -> int:
    return secrets.randbits(62)


async def read_session_generation() -> int | None:
    """The current generation; None when the row is missing. A read only: every signed-in request
    asks it. Raises SQLAlchemyError/OSError when the database is unreachable."""
    async with get_db_context() as session:
        return (await session.execute(_GENERATION)).scalar_one_or_none()


async def advance_session_generation() -> None:
    """A logout: the generation moves on by one, atomically, so every earlier cookie is refused.
    With the row missing it is written at a fresh generation. Raises SQLAlchemyError/OSError when
    the database is unreachable."""
    async with get_db_context() as session:
        stmt = insert(DashboardSessionState).values(
            id=SESSION_STATE_ROW, generation=_fresh_generation()
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[DashboardSessionState.id],
                set_={"generation": DashboardSessionState.generation + 1},
            )
        )


async def ensure_session_generation() -> None:
    """Write the row at a fresh generation when it is missing (app startup); an existing row is
    left as it is."""
    async with get_db_context() as session:
        await session.execute(
            insert(DashboardSessionState)
            .values(id=SESSION_STATE_ROW, generation=_fresh_generation())
            .on_conflict_do_nothing(index_elements=[DashboardSessionState.id])
        )


async def reset_session_generation() -> None:
    """Sign every session out by moving to a fresh generation: what a database restore needs,
    since the restored row carries a generation that cookies revoked after the dump may hold."""
    async with get_db_context() as session:
        stmt = insert(DashboardSessionState).values(
            id=SESSION_STATE_ROW, generation=_fresh_generation()
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[DashboardSessionState.id],
                set_={"generation": stmt.excluded.generation},
            )
        )
