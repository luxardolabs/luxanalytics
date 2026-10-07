"""Constants shared across ingestion and migration."""

# Device context keys that get promoted from metadata to dedicated columns.
# These are stripped from the properties JSONB on ingest.
DEVICE_CONTEXT_KEYS = frozenset({
    "device_id",
    "device_model",
    "device_type",
    "system_version",
    "app_version",
    "build_number",
    "screen_resolution",
    "locale",
    "timezone",
    "is_testflight",
})

# Mapping from metadata key names to Event model column names
# (where the names differ between SDK payload and DB column)
METADATA_TO_COLUMN = {
    "system_version": "os_version",
}
