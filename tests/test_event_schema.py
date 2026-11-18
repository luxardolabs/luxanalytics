"""EventCreate.timestamp: an ISO8601 string on the wire, an aware datetime once validated."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.event_schema import TEXT_VALUE_MAX_BYTES, EventCreate


def _event(timestamp: object) -> EventCreate:
    return EventCreate.model_validate({"name": "app_opened", "timestamp": timestamp})


def test_a_z_suffixed_timestamp_is_an_aware_utc_datetime() -> None:
    assert _event("2026-10-06T12:00:00Z").timestamp == datetime(
        2026, 10, 6, 12, tzinfo=UTC
    )


def test_a_naive_timestamp_is_taken_as_utc() -> None:
    assert _event("2026-10-06T12:00:00").timestamp.tzinfo == UTC


def test_a_timestamp_beyond_the_skew_tolerance_is_clamped_to_now() -> None:
    """A fast device clock is clamped, not rejected: a 422 made the SDK drop the batch
    (LUXANALYTI-80)."""
    before = datetime.now(UTC)
    clamped = _event((before + timedelta(hours=1)).isoformat()).timestamp
    assert before <= clamped <= datetime.now(UTC)


@pytest.mark.parametrize("bad", ["yesterday", 1759752000, None])
def test_anything_but_an_iso8601_string_is_rejected(bad: object) -> None:
    with pytest.raises(ValidationError):
        _event(bad)


def _with_metadata(**metadata: str) -> EventCreate:
    return EventCreate.model_validate(
        {
            "name": "app_opened",
            "timestamp": "2026-10-06T12:00:00Z",
            "metadata": metadata,
        }
    )


def test_a_device_id_past_what_the_indexes_hold_is_refused() -> None:
    """device_id is in two btree indexes and the devices primary key, on every event: past a btree
    row's limit the INSERT fails for every batch carrying one. It is an identity, so it is refused,
    never shortened (two devices would merge)."""
    assert _with_metadata(device_id="x" * 255).metadata["device_id"] == "x" * 255
    with pytest.raises(ValidationError, match="device_id"):
        _with_metadata(device_id="x" * 256)


def _performance_event(operation: str) -> EventCreate:
    return EventCreate.model_validate(
        {
            "name": "performance_measured",
            "timestamp": "2026-10-06T12:00:00Z",
            "metadata": {"operation": operation},
        }
    )


@pytest.mark.parametrize("key", ["screen", "operation", "button"])
@pytest.mark.parametrize("unit", ["x", "é", "\U0001f600"])
def test_a_long_displayed_value_on_any_event_is_cut_to_fit(key: str, unit: str) -> None:
    """The pages group and show these values, and events are never deleted: one oversized value
    would bloat its page for good. It is cut on a character boundary; the event is kept."""
    long_value = unit * 5000
    other = EventCreate.model_validate(
        {
            "name": "sync_failed",
            "timestamp": "2026-10-06T12:00:00Z",
            "metadata": {key: long_value},
        }
    )

    kept = other.metadata[key]
    assert 0 < len(kept.encode()) <= TEXT_VALUE_MAX_BYTES
    assert long_value.startswith(kept)
    assert len(kept.encode()) > TEXT_VALUE_MAX_BYTES - 4


def test_a_screen_within_the_limit_is_stored_as_sent() -> None:
    screen = "Settings/" + "z" * 1500
    assert _with_metadata(screen=screen).metadata["screen"] == screen


def test_an_operation_the_performance_index_holds_is_stored_as_sent() -> None:
    operation = "GET /" + "y" * 1500

    assert _performance_event(operation).metadata["operation"] == operation


@pytest.mark.parametrize("unit", ["x", "é", "日", "\U0001f600"])
def test_an_operation_past_the_index_row_is_cut_to_fit_in_bytes(unit: str) -> None:
    """The index limit is bytes: multi-byte text is measured as stored, and a character is never
    cut in half."""
    operation = unit * 3000

    kept = _performance_event(operation).metadata["operation"]

    assert 0 < len(kept.encode()) <= TEXT_VALUE_MAX_BYTES
    assert operation.startswith(kept)
    assert len(kept.encode()) > TEXT_VALUE_MAX_BYTES - 4


def test_other_metadata_is_stored_as_sent() -> None:
    long_note = "n" * 8000
    assert _with_metadata(note=long_note).metadata["note"] == long_note
