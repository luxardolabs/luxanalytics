"""Baseline: capture existing schema.

Revision ID: 001_baseline
Revises: None
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Events table (original schema)
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("app_id", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False, index=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("user_id", sa.String(), index=True),
        sa.Column("session_id", sa.String(), index=True),
        sa.Column("event_metadata", postgresql.JSON()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Original composite indexes
    op.create_index(
        "ix_events_app_name_timestamp",
        "events",
        ["app_id", "name", "timestamp"],
    )
    op.create_index(
        "ix_events_app_user_timestamp",
        "events",
        ["app_id", "user_id", "timestamp"],
    )
    op.create_index(
        "ix_events_app_session_timestamp",
        "events",
        ["app_id", "session_id", "timestamp"],
    )

    # Apps table
    op.create_table(
        "apps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("public_id", sa.String(32), unique=True, nullable=False, index=True),
        sa.Column("project_id", sa.String(16), unique=True, nullable=False, index=True),
        sa.Column("app_id", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("organization", sa.String(255)),
        sa.Column("description", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "app_metadata", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
    )


def downgrade() -> None:
    op.drop_table("apps")
    op.drop_index("ix_events_app_session_timestamp", table_name="events")
    op.drop_index("ix_events_app_user_timestamp", table_name="events")
    op.drop_index("ix_events_app_name_timestamp", table_name="events")
    op.drop_table("events")
