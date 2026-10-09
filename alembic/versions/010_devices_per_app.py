"""Key devices on (app_id, device_id): one row per app install, not per phone (LUXANALYTI-84).

The SDK's device id comes from identifierForVendor, which every app of one vendor shares, so a
phone running two apps reports one id to both. Keyed on device_id alone, the row went to whichever
app was seen first (the other apps' device lists missed the phone) and every app overwrote its
version, build, TestFlight flag and last_seen. On prod, 7 of 61 devices had events from more than
one app.

Upgrade: for each device id with events from more than one app, its one row is replaced by a row
per app, rebuilt from that app's own events: each context column is the app's latest non-null
value (by event time, then receive time), first/last seen its first and last receive time. Rows of
single-app devices are left as they are. The primary key becomes (app_id, device_id) first.
Rehearsed on a full copy of prod (72,624 events, 61 device rows, 75 app/device pairs).

Downgrade: one row per device again, keeping each device's most recently seen row (the others are
derived data, rebuildable from events, which this never touches).

Revision ID: 010_devices_per_app
Revises: 009_drop_event_metadata
Create Date: 2026-10-08 23:30:00.000000
"""

from alembic import op

revision = "010_devices_per_app"
down_revision = "009_drop_event_metadata"
branch_labels = None
depends_on = None

_CONTEXT = (
    "device_model",
    "device_type",
    "screen_resolution",
    "os_version",
    "app_version",
    "build_number",
    "locale",
    "timezone",
    "is_testflight",
    "platform",
)


def _latest(column: str) -> str:
    return (
        f"(array_agg({column} ORDER BY timestamp DESC, received_at DESC)"
        f" FILTER (WHERE {column} IS NOT NULL))[1] AS {column}"
    )


def upgrade() -> None:
    # The key first: a phone's per-app rows cannot exist under the old device_id-only key.
    op.drop_constraint("pk_devices", "devices", type_="primary")
    op.create_primary_key("pk_devices", "devices", ["app_id", "device_id"])
    op.execute(
        """
        CREATE TEMPORARY TABLE shared_devices ON COMMIT DROP AS
        SELECT device_id FROM events
        WHERE device_id IS NOT NULL
        GROUP BY device_id HAVING count(DISTINCT app_id) > 1
        """
    )
    op.execute(
        "DELETE FROM devices WHERE device_id IN (SELECT device_id FROM shared_devices)"
    )
    columns = ", ".join(_CONTEXT)
    latest = ",\n            ".join(_latest(c) for c in _CONTEXT)
    op.execute(
        f"""
        INSERT INTO devices (app_id, device_id, {columns}, first_seen, last_seen)
        SELECT app_id, device_id, {columns}, first_seen, last_seen FROM (
            SELECT app_id, device_id,
            {latest},
            min(received_at) AS first_seen,
            max(received_at) AS last_seen
            FROM events
            WHERE device_id IN (SELECT device_id FROM shared_devices)
            GROUP BY app_id, device_id
        ) rebuilt
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM devices d
        USING (
            SELECT app_id, device_id,
                   row_number() OVER (PARTITION BY device_id ORDER BY last_seen DESC, app_id) AS n
            FROM devices
        ) ranked
        WHERE ranked.n > 1 AND d.app_id = ranked.app_id AND d.device_id = ranked.device_id
        """
    )
    op.drop_constraint("pk_devices", "devices", type_="primary")
    op.create_primary_key("pk_devices", "devices", ["device_id"])
