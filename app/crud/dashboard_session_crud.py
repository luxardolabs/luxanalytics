"""The dashboard's session state: reads and writes of the one logout-stamp row."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.database import get_db_context
from app.models.dashboard_session_model import SESSION_STATE_ROW, DashboardSessionState

_STAMP = select(DashboardSessionState.sessions_valid_after).where(
    DashboardSessionState.id == SESSION_STATE_ROW
)


async def read_sessions_valid_after() -> datetime | None:
    """The last logout's time; None when the row is missing (migration 011 writes it). A read only:
    every signed-in GET asks it. Raises SQLAlchemyError/OSError when the database is unreachable."""
    async with get_db_context() as session:
        return (await session.execute(_STAMP)).scalar_one_or_none()


async def write_sessions_valid_after(now: datetime) -> None:
    """Record a logout at `now`. Raises SQLAlchemyError/OSError when the database is unreachable."""
    async with get_db_context() as session:
        stmt = insert(DashboardSessionState).values(
            id=SESSION_STATE_ROW, sessions_valid_after=now
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[DashboardSessionState.id],
                set_={"sessions_valid_after": stmt.excluded.sessions_valid_after},
            )
        )
