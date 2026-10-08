"""Drop events.event_metadata: every value in it is held in a column or in properties (LUXANALYTI-16).

It was the original single JSON column (001). 002/003 split it into promoted columns, `properties`
and the devices table; 008 promoted the last six per-event device keys. Rehearsed on a full copy
of prod before this was written: after 008, 0 of 72,624 events had an event_metadata key whose
value was not also in a column or in properties. So this drops a duplicate, not data.

The downgrade rebuilds the column from those same columns and properties, so it loses nothing
either. (`platform` comes back on every row, from the column, where some rows never sent it.)
Some prod rows store `properties` as a JSON null rather than SQL NULL; the merge treats any
non-object as empty, since `jsonb null || object` would make an array.

Revision ID: 009_drop_event_metadata
Revises: 008_event_device_context
Create Date: 2026-10-08 19:00:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "009_drop_event_metadata"
down_revision = "008_event_device_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("events", "event_metadata")


def downgrade() -> None:
    op.add_column(
        "events", sa.Column("event_metadata", postgresql.JSONB(), nullable=True)
    )
    op.execute(
        """
        UPDATE events SET event_metadata = jsonb_strip_nulls(jsonb_build_object(
            'device_id', device_id,
            'device_model', device_model,
            'system_version', os_version,
            'app_version', app_version,
            'platform', platform,
            'device_type', device_type,
            'build_number', build_number,
            'screen_resolution', screen_resolution,
            'locale', locale,
            'timezone', timezone,
            'is_testflight', CASE WHEN is_testflight IS NULL THEN NULL
                                  WHEN is_testflight THEN 'true' ELSE 'false' END
        )) || CASE WHEN jsonb_typeof(properties) = 'object' THEN properties ELSE '{}'::jsonb END
        """
    )
