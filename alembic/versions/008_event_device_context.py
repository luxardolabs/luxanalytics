"""Per-event device context: promote the last six keys out of event_metadata (LUXANALYTI-16).

002/003 promoted device_id, device_model, os_version, app_version and platform to events columns,
but device_type, build_number, screen_resolution, locale, timezone and is_testflight only reached
the devices table, which keeps each device's LATEST values. event_metadata stayed the only record
of them as they were for each event. This adds them to events and backfills every row from
event_metadata, so that column no longer holds anything that exists nowhere else.

Additive: six nullable columns and one UPDATE. Nothing is removed.

Revision ID: 008_event_device_context
Revises: 007_client_event_id
Create Date: 2026-10-08 18:30:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "008_event_device_context"
down_revision = "007_client_event_id"
branch_labels = None
depends_on = None

_TEXT_KEYS = ("device_type", "build_number", "screen_resolution", "locale", "timezone")


def upgrade() -> None:
    for key in _TEXT_KEYS:
        op.add_column("events", sa.Column(key, sa.String(), nullable=True))
    op.add_column("events", sa.Column("is_testflight", sa.Boolean(), nullable=True))
    # is_testflight stays NULL where the key was never sent, rather than false.
    op.execute(
        """
        UPDATE events SET
            device_type = event_metadata->>'device_type',
            build_number = event_metadata->>'build_number',
            screen_resolution = event_metadata->>'screen_resolution',
            locale = event_metadata->>'locale',
            timezone = event_metadata->>'timezone',
            is_testflight = CASE
                WHEN event_metadata->>'is_testflight' IS NULL THEN NULL
                ELSE event_metadata->>'is_testflight' = 'true'
            END
        WHERE event_metadata IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("events", "is_testflight")
    for key in reversed(_TEXT_KEYS):
        op.drop_column("events", key)
