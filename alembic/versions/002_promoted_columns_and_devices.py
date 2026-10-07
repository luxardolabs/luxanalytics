"""Add promoted columns to events, create devices table.

Revision ID: 002_promoted_columns
Revises: 001_baseline
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "002_promoted_columns"
down_revision = "001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Events: add promoted columns ---
    op.add_column("events", sa.Column("device_id", sa.String(), index=True))
    op.add_column("events", sa.Column("device_model", sa.String()))
    op.add_column("events", sa.Column("os_version", sa.String()))
    op.add_column("events", sa.Column("app_version", sa.String()))
    op.add_column("events", sa.Column("platform", sa.String(), server_default="ios"))
    op.add_column("events", sa.Column("properties", postgresql.JSONB()))

    # Convert event_metadata from JSON to JSONB (more efficient, indexable)
    op.execute(
        "ALTER TABLE events "
        "ALTER COLUMN event_metadata TYPE jsonb USING event_metadata::jsonb"
    )

    # New composite indexes for the query patterns we actually use
    op.create_index(
        "ix_events_app_name_received",
        "events",
        ["app_id", "name", "received_at"],
    )
    op.create_index(
        "ix_events_app_device_received",
        "events",
        ["app_id", "device_id", "received_at"],
    )
    op.create_index(
        "ix_events_app_user_received",
        "events",
        ["app_id", "user_id", "received_at"],
    )
    op.create_index(
        "ix_events_app_session_received",
        "events",
        ["app_id", "session_id", "received_at"],
    )

    # GIN index on properties for JSONB key/value queries
    op.create_index(
        "ix_events_properties_gin",
        "events",
        ["properties"],
        postgresql_using="gin",
    )

    # --- Soft delete columns for apps ---
    op.add_column(
        "apps", sa.Column("deleted_at", sa.DateTime(timezone=True), index=True)
    )

    # --- Devices table ---
    op.create_table(
        "devices",
        sa.Column("device_id", sa.String(), primary_key=True),
        sa.Column("app_id", sa.String(), nullable=False, index=True),
        sa.Column("device_model", sa.String()),
        sa.Column("device_type", sa.String()),
        sa.Column("screen_resolution", sa.String()),
        sa.Column("os_version", sa.String()),
        sa.Column("app_version", sa.String()),
        sa.Column("build_number", sa.String()),
        sa.Column("locale", sa.String()),
        sa.Column("timezone", sa.String()),
        sa.Column("is_testflight", sa.Boolean()),
        sa.Column("platform", sa.String(), server_default="ios"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_index(
        "ix_devices_app_last_seen",
        "devices",
        ["app_id", "last_seen"],
    )


def downgrade() -> None:
    op.drop_table("devices")

    op.drop_index("ix_events_properties_gin", table_name="events")
    op.drop_index("ix_events_app_session_received", table_name="events")
    op.drop_index("ix_events_app_user_received", table_name="events")
    op.drop_index("ix_events_app_device_received", table_name="events")
    op.drop_index("ix_events_app_name_received", table_name="events")

    op.drop_column("events", "properties")
    op.drop_column("events", "platform")
    op.drop_column("events", "app_version")
    op.drop_column("events", "os_version")
    op.drop_column("events", "device_model")
    op.drop_column("events", "device_id")

    op.drop_column("apps", "deleted_at")

    # Revert JSONB back to JSON
    op.execute(
        "ALTER TABLE events "
        "ALTER COLUMN event_metadata TYPE json USING event_metadata::json"
    )
