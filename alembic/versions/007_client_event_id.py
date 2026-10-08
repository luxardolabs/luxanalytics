"""Events carry the client's event id; a resent event is dropped on insert (LUXANALYTI-68).

Additive: a nullable column and a unique index over (app_id, client_event_id), partial on
non-NULL, so existing rows (no client id) and clients that send none are unaffected.

Revision ID: 007_client_event_id
Revises: 006_naming_convention_pks
Create Date: 2026-10-08 01:30:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "007_client_event_id"
down_revision = "006_naming_convention_pks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("client_event_id", sa.String(64), nullable=True))
    op.create_index(
        "ix_events_app_id_client_event_id",
        "events",
        ["app_id", "client_event_id"],
        unique=True,
        postgresql_where=sa.text("client_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_events_app_id_client_event_id", table_name="events")
    op.drop_column("events", "client_event_id")
