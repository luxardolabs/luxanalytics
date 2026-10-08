"""The Jinja filters the dashboard renders every number, time and label through: exact outputs."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.config import settings
from app.web import template_filters as f

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


# ── Timezone ─────────────────────────────────────────────────────────────────────────────────


def test_localtime_converts_and_tolerates_bad_input() -> None:
    assert f.localtime(None) is None
    assert f.localtime(date(2026, 10, 8)) == date(2026, 10, 8)
    naive = NOW.replace(tzinfo=None)
    assert f.localtime(naive) == naive.replace(tzinfo=UTC)
    chicago = f.localtime(NOW, "America/Chicago")
    assert isinstance(chicago, datetime) and chicago.hour == 7
    assert f.localtime(NOW, "Not/AZone") == NOW  # unknown zone: rendered unconverted


def test_tzname() -> None:
    assert f.tzname(None) == "--"
    assert f.tzname(NOW.replace(tzinfo=None)) == "UTC"
    assert f.tzname(NOW) == "UTC"


# ── Absolute formats ─────────────────────────────────────────────────────────────────────────


def test_datetime_date_and_time_formats() -> None:
    assert f.format_datetime(None) == "--"
    assert f.format_datetime(NOW) == "2026-10-08 12:00"
    assert f.format_datetime(NOW, "iso") == "2026-10-08T12:00:00"
    assert (
        f.format_datetime(NOW, "%d.%m") == "08.10"
    )  # an unknown name is a strftime pattern
    assert f.format_datetime(
        datetime.now(UTC) - timedelta(minutes=5), "relative"
    ).endswith("ago")
    assert f.format_date(None) == "--"
    assert f.format_date(NOW, "us") == "10/08/2026"
    assert f.format_time(None) == "--"
    assert f.format_time(NOW, "24h") == "12:00"


# ── Relative time ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ago", "short", "long"),
    [
        (timedelta(seconds=30), "30s ago", "30 seconds ago"),
        (timedelta(minutes=1), "1m ago", "1 minute ago"),
        (timedelta(minutes=5), "5m ago", "5 minutes ago"),
        (timedelta(hours=1), "1h ago", "1 hour ago"),
        (timedelta(hours=3), "3h ago", "3 hours ago"),
        (timedelta(days=1), "1d ago", "1 day ago"),
        (timedelta(days=4), "4d ago", "4 days ago"),
        (timedelta(days=30), "1mo ago", "1 month ago"),
        (timedelta(days=90), "3mo ago", "3 months ago"),
        (timedelta(days=365), "1y ago", "1 year ago"),
        (timedelta(days=800), "2y ago", "2 years ago"),
    ],
)
def test_timeago(ago: timedelta, short: str, long: str) -> None:
    assert f.timeago(NOW - ago, now=NOW) == short
    assert f.timeago(NOW - ago, style="long", now=NOW) == long


def test_timeago_edges() -> None:
    assert f.timeago(None) == "--"
    assert f.timeago(NOW + timedelta(minutes=1), now=NOW) == "just now"
    naive_now = NOW.replace(tzinfo=None)
    naive_dt = naive_now - timedelta(hours=1)
    assert f.timeago(naive_dt, now=naive_now) == "1h ago"


@pytest.mark.parametrize(
    ("ago", "colour"),
    [
        (timedelta(minutes=1), "text-green-600"),
        (timedelta(minutes=10), "text-yellow-600"),
        (timedelta(hours=2), "text-gray-400"),
        (timedelta(days=2), "text-gray-400"),
    ],
)
def test_timeago_color(ago: timedelta, colour: str) -> None:
    assert f.timeago_color(NOW - ago, now=NOW) == colour


def test_timeago_color_edges() -> None:
    assert f.timeago_color(None) == "text-gray-400"
    naive = (NOW - timedelta(minutes=1)).replace(tzinfo=None)
    assert f.timeago_color(naive, now=NOW) == "text-green-600"
    assert f.timeago_color(datetime.now(UTC)) == "text-green-600"


# ── Durations and numbers ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "short", "long", "colon"),
    [
        (0, "0s", "0 seconds", "0:00"),
        (45, "45s", "45 seconds", "0:45"),
        (1, "1s", "1 second", "0:01"),
        (61, "1m", "1 minute", "1:01"),
        (3600, "1h", "1 hour", "1:00:00"),
        (3725, "1h 2m", "1 hour 2 minutes", "1:02:05"),
        (90061, "1d 1h 1m", "1 day 1 hour 1 minute", "1:01:01:01"),
        (2 * 86400, "2d", "2 days", "2:00:00:00"),
    ],
)
def test_duration(seconds: int, short: str, long: str, colon: str) -> None:
    assert f.duration(seconds) == short
    assert f.duration(seconds, "long") == long
    assert f.duration(seconds, "colon") == colon


def test_duration_edges() -> None:
    assert f.duration(None) == "--"
    assert f.duration(-5) == "--"
    assert f.duration(59.9) == "59s"


def test_numbers_and_names() -> None:
    assert f.num(None) == "--"
    assert f.num(72.456) == "72.5"
    assert f.num(3, decimals=2) == "3.00"
    assert f.number_format(None) == "0"
    assert f.number_format(1234567.9) == "1,234,567"
    assert f.friendly_name(None) == "--"
    assert f.friendly_name("app_launched") == "App Launched"


def test_day_and_month_names() -> None:
    assert f.day_of_week(None) == "--"
    assert f.day_of_week(NOW) == "Thu"
    assert f.day_of_week(NOW, "long") == "Thursday"
    assert f.day_of_week(NOW, "number") == "3"
    assert f.month_name(None) == "--"
    assert f.month_name(NOW) == "Oct"
    assert f.month_name(NOW, "long") == "October"
    assert f.month_name(NOW, "number") == "10"


# ── JSON and the cache-bust token ────────────────────────────────────────────────────────────


def test_tojson_safe() -> None:
    value = {"at": NOW, "on": date(2026, 10, 8), "n": 1}
    assert f.tojson_safe(value) == (
        '{"at": "2026-10-08T12:00:00+00:00", "on": "2026-10-08", "n": 1}'
    )
    assert f.tojson_safe([1], indent=2) == "[\n  1\n]"
    with pytest.raises(TypeError):
        f.tojson_safe({"x": object()})


@pytest.mark.parametrize(
    ("commit", "token"),
    [
        ("abc1234", "abc1234"),
        (None, "dev"),
        ("", "dev"),
        ("unknown", "dev"),
        ("local", "dev"),
    ],
)
def test_static_version(
    monkeypatch: pytest.MonkeyPatch, commit: str | None, token: str
) -> None:
    monkeypatch.setattr(settings, "BUILD_COMMIT", commit)
    assert f.static_version() == token
