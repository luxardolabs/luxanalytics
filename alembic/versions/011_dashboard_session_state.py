"""The dashboard's session generation: one row, the generation every session cookie must carry.

Additive: a new table and its one row, at a random generation, which no existing cookie carries,
so every session signs in again once. The logout revocation lives here from this revision on; the
Redis key `dashboard:sessions_valid_after` is no longer read.

Revision ID: 011_dashboard_session_state
Revises: 010_devices_per_app
Create Date: 2026-10-28 02:30:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "011_dashboard_session_state"
down_revision = "010_devices_per_app"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_session_state",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "id = 1", name=op.f("ck_dashboard_session_state_single_row")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_session_state")),
    )
    op.execute(
        "INSERT INTO dashboard_session_state (id, generation) "
        "VALUES (1, floor(random() * 4611686018427387903)::bigint)"
    )


def downgrade() -> None:
    op.drop_table("dashboard_session_state")
