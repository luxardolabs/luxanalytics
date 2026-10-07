"""EventCreate.timestamp: an ISO8601 string on the wire, an aware datetime once validated."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.event_schema import EventCreate


def _event(timestamp: object) -> EventCreate:
    return EventCreate.model_validate({"name": "app_opened", "timestamp": timestamp})


def test_a_z_suffixed_timestamp_is_an_aware_utc_datetime() -> None:
    assert _event("2026-10-06T12:00:00Z").timestamp == datetime(
        2026, 10, 6, 12, tzinfo=UTC
    )


def test_a_naive_timestamp_is_taken_as_utc() -> None:
    assert _event("2026-10-06T12:00:00").timestamp.tzinfo == UTC


def test_a_timestamp_beyond_the_skew_tolerance_is_rejected() -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with pytest.raises(ValidationError, match="in the future"):
        _event(future)


@pytest.mark.parametrize("bad", ["yesterday", 1759752000, None])
def test_anything_but_an_iso8601_string_is_rejected(bad: object) -> None:
    with pytest.raises(ValidationError):
        _event(bad)
