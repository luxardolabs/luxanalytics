"""A restored database never serves a session generation a later logout revoked.

Dumps leave the generation's row out (tests/test_backup_script.py), a restore drops any it brings
(scripts/restore_tail.sql, tests/test_auth_session.py), and the app writes a fresh one when it
starts. Writing a fresh generation does not depend on the table's primary key (a hand-made restore
can lack it), and the reset leaves a database without the table (before migration 011) to startup.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.dashboard_session_crud import (
    ensure_session_generation,
    reset_session_generation,
)
from app.models.dashboard_session_model import DashboardSessionState

pytestmark = pytest.mark.db

GENERATION = select(DashboardSessionState.generation)


async def _drop_primary_key(db: AsyncSession) -> None:
    await db.execute(
        text(
            "ALTER TABLE dashboard_session_state DROP CONSTRAINT pk_dashboard_session_state"
        )
    )


async def test_startup_writes_a_generation_without_the_primary_key(
    db: AsyncSession,
) -> None:
    """A restore that died before its post-data step: the table, no row, no primary key."""
    await _drop_primary_key(db)
    await db.execute(text("DELETE FROM dashboard_session_state"))

    await ensure_session_generation()

    assert len((await db.execute(GENERATION)).scalars().all()) == 1


async def test_a_reset_replaces_the_row_without_the_primary_key(
    db: AsyncSession,
) -> None:
    before = (await db.execute(GENERATION)).scalar_one()
    await _drop_primary_key(db)

    assert await reset_session_generation()

    after = (await db.execute(GENERATION)).scalars().all()
    assert len(after) == 1
    assert after[0] != before


async def test_a_reset_before_the_table_exists_leaves_it_to_startup(
    db: AsyncSession,
) -> None:
    """A dump from before migration 011 has no session table: the next start migrates and seeds."""
    await db.execute(
        text("ALTER TABLE dashboard_session_state RENAME TO dashboard_session_state_x")
    )

    assert not await reset_session_generation()
