"""Drop the superseded *_timestamp composite indexes.

001_baseline created ix_events_app_{name,user,session}_timestamp. 002 added the *_received
replacements and the models moved to them, but nothing dropped the originals, so every database
built from the chain carried both sets. Nothing queries events.timestamp (it is written at ingest
only; every query filters on received_at), so the old three only cost write amplification on
every insert. Found by `make db-verify`; owner-approved in LUXANALYTI-51.

Revision ID: 005_drop_timestamp_indexes
Revises: 004_analytics_indexes
Create Date: 2026-10-07 03:30:00.000000
"""

from alembic import op

revision = "005_drop_timestamp_indexes"
down_revision = "004_analytics_indexes"
branch_labels = None
depends_on = None

_SUPERSEDED = {
    "ix_events_app_name_timestamp": ["app_id", "name", "timestamp"],
    "ix_events_app_user_timestamp": ["app_id", "user_id", "timestamp"],
    "ix_events_app_session_timestamp": ["app_id", "session_id", "timestamp"],
}


def upgrade() -> None:
    for name in _SUPERSEDED:
        op.drop_index(name, table_name="events", if_exists=True)


def downgrade() -> None:
    # The exact definitions 001_baseline created.
    for name, columns in _SUPERSEDED.items():
        op.create_index(name, "events", columns, if_not_exists=True)
