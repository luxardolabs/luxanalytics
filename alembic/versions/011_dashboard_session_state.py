"""The logout stamp's table: one row, the time before which every dashboard session is refused.

Additive: a new table and its one row, stamped with the migration's time, which signs every session
out once. The stamp lives here from this revision on; the Redis key is no longer read.

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
        sa.Column("sessions_valid_after", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "id = 1", name=op.f("ck_dashboard_session_state_single_row")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_session_state")),
    )
    # The one row, stamped now: every session signed in before this migration is signed out once,
    # since a logout recorded only in Redis is not carried over.
    op.execute(
        "INSERT INTO dashboard_session_state (id, sessions_valid_after) VALUES (1, now())"
    )


def downgrade() -> None:
    op.drop_table("dashboard_session_state")
