"""Add analytics query indexes.

Revision ID: 004_analytics_indexes
Revises: 003_backfill
"""

from alembic import op

revision = "004_analytics_indexes"
down_revision = "003_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_events_perf_operation
        ON events USING btree ((properties->>'operation'))
        WHERE name = 'performance_measured' AND properties IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_events_screen_session
        ON events USING btree (session_id, received_at)
        WHERE name = 'screen_viewed' AND properties->>'screen' IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_events_user_received
        ON events USING btree (user_id, received_at DESC)
        WHERE user_id IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_events_session_received
        ON events USING btree (session_id, received_at)
        WHERE session_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_events_perf_operation")
    op.execute("DROP INDEX IF EXISTS ix_events_screen_session")
    op.execute("DROP INDEX IF EXISTS ix_events_user_received")
    op.execute("DROP INDEX IF EXISTS ix_events_session_received")
