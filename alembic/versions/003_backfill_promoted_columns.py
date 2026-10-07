"""Backfill promoted columns from event_metadata JSON.

Revision ID: 003_backfill
Revises: 002_promoted_columns
"""

from alembic import op
import sqlalchemy as sa

revision = "003_backfill"
down_revision = "002_promoted_columns"
branch_labels = None
depends_on = None

# Device context keys to strip from properties
DEVICE_CONTEXT_KEYS = {
    "device_id", "device_model", "device_type", "system_version",
    "app_version", "build_number", "screen_resolution", "locale",
    "timezone", "is_testflight",
}


def upgrade() -> None:
    # Backfill promoted columns from event_metadata using a single SQL UPDATE.
    # This is much faster than row-by-row Python processing.
    op.execute("""
        UPDATE events SET
            device_id = event_metadata->>'device_id',
            device_model = event_metadata->>'device_model',
            os_version = event_metadata->>'system_version',
            app_version = event_metadata->>'app_version',
            platform = 'ios',
            properties = event_metadata - ARRAY[
                'device_id', 'device_model', 'device_type', 'system_version',
                'app_version', 'build_number', 'screen_resolution', 'locale',
                'timezone', 'is_testflight'
            ]
        WHERE event_metadata IS NOT NULL
          AND device_id IS NULL
    """)

    # Populate devices table from the backfilled events.
    # Uses DISTINCT ON to get one row per device_id, taking the most recent event's data.
    op.execute("""
        INSERT INTO devices (
            device_id, app_id, device_model, device_type, os_version,
            app_version, build_number, screen_resolution, locale, timezone,
            is_testflight, platform, first_seen, last_seen
        )
        SELECT DISTINCT ON (sub.device_id)
            sub.device_id,
            sub.app_id,
            sub.device_model,
            sub.event_metadata->>'device_type',
            sub.os_version,
            sub.app_version,
            sub.event_metadata->>'build_number',
            sub.event_metadata->>'screen_resolution',
            sub.event_metadata->>'locale',
            sub.event_metadata->>'timezone',
            CASE WHEN sub.event_metadata->>'is_testflight' = 'true' THEN true ELSE false END,
            'ios',
            sub.first_seen,
            sub.last_seen
        FROM (
            SELECT
                device_id,
                app_id,
                device_model,
                os_version,
                app_version,
                event_metadata,
                received_at,
                MIN(received_at) OVER (PARTITION BY device_id) AS first_seen,
                MAX(received_at) OVER (PARTITION BY device_id) AS last_seen
            FROM events
            WHERE device_id IS NOT NULL
        ) sub
        ORDER BY sub.device_id, sub.received_at DESC
        ON CONFLICT (device_id) DO NOTHING
    """)


def downgrade() -> None:
    # Clear backfilled data
    op.execute("""
        UPDATE events SET
            device_id = NULL,
            device_model = NULL,
            os_version = NULL,
            app_version = NULL,
            platform = NULL,
            properties = NULL
    """)
    op.execute("DELETE FROM devices")
