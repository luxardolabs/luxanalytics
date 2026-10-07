"""
Jinja2 template filters for LuxAnalytics Portal.

All filters handle None gracefully, returning "--" when the input is None.

Ported from LuxWX production filters — kept the core date/time/number
formatting, dropped weather/billing-specific filters.

FILTERS REFERENCE:

DATE FORMATS ({{ dt | date }} or {{ dt | date('format') }}):
    default     "2024-01-15"            ISO format
    short       "1/15/24"               Compact US style
    medium      "Jan 15, 2024"          Month abbrev
    long        "January 15, 2024"      Full month name
    full        "Monday, January 15, 2024"  With weekday

TIME FORMATS ({{ dt | time }} or {{ dt | time('format') }}):
    default     "2:30 pm"               12h
    short       "2:30 pm"               12h no seconds
    24h         "14:30"                 24h no seconds

DATETIME FORMATS ({{ dt | datetime }} or {{ dt | datetime('format') }}):
    default     "2024-01-15 14:30"      Space-separated
    short       "1/15/24, 2:30 pm"      Compact US style
    medium      "Jan 15, 2024, 2:30 pm" Month abbrev
    friendly    "Jan 15 at 2:30 pm"     Casual format
    relative    "5 minutes ago"         Uses timeago filter

RELATIVE TIME ({{ dt | timeago }}):
    short (default)  "5m ago", "2h ago", "3d ago"
    long             "5 minutes ago", "2 hours ago"

NUMBER FORMAT:
    {{ 1234567 | number_format }}  -> "1,234,567"
    {{ 72.5 | num }}               -> "72.5"
    {{ 72.5 | num(0) }}            -> "73"
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.core.pagination import paginated_url

# Named format presets
DATE_FORMATS = {
    "default": "%Y-%m-%d",
    "short": "%-m/%-d/%y",
    "medium": "%b %-d, %Y",
    "long": "%B %-d, %Y",
    "full": "%A, %B %-d, %Y",
    "iso": "%Y-%m-%d",
    "us": "%m/%d/%Y",
    "eu": "%d/%m/%Y",
    "compact": "%Y%m%d",
}

TIME_FORMATS = {
    "default": "%-I:%M %P",
    "short": "%-I:%M %P",
    "medium": "%-I:%M:%S %P",
    "long": "%H:%M:%S",
    "24h": "%H:%M",
    "12h": "%-I:%M %P",
    "iso": "%H:%M:%S",
    "compact": "%H%M",
}

DATETIME_FORMATS = {
    "default": "%Y-%m-%d %H:%M",
    "short": "%-m/%-d/%y, %-I:%M %P",
    "medium": "%b %-d, %Y, %-I:%M %P",
    "long": "%B %-d, %Y at %-I:%M:%S %P",
    "full": "%A, %B %-d, %Y at %-I:%M %P",
    "iso": "%Y-%m-%dT%H:%M:%S",
    "iso_tz": "%Y-%m-%dT%H:%M:%S%z",
    "log": "%Y-%m-%d %H:%M:%S",
    "friendly": "%b %-d at %-I:%M %P",
}


# ── Timezone ──────────────────────────────────────────────────────────────────


def localtime(
    dt: datetime | date | None, tz: str | None = None
) -> datetime | date | None:
    """Convert a datetime to the specified timezone."""
    if dt is None:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    if tz:
        try:
            return dt.astimezone(ZoneInfo(tz))
        except ZoneInfoNotFoundError, ValueError:
            # An unknown or malformed zone name renders the time unconverted (UTC).
            return dt
    return dt


def tzname(dt: datetime | None) -> str:
    """Get timezone abbreviation from a datetime."""
    if dt is None:
        return "--"
    if dt.tzinfo is None:
        return "UTC"
    name = dt.tzinfo.tzname(dt)
    return name if name else "UTC"


# ── Format helpers ────────────────────────────────────────────────────────────


def _get_format(fmt: str, format_dict: dict[str, str]) -> str:
    return format_dict.get(fmt, fmt)


def format_datetime(dt: datetime | None, fmt: str = "default") -> str:
    if dt is None:
        return "--"
    if fmt == "relative":
        return timeago(dt)
    return dt.strftime(_get_format(fmt, DATETIME_FORMATS))


def format_date(dt: datetime | None, fmt: str = "default") -> str:
    if dt is None:
        return "--"
    return dt.strftime(_get_format(fmt, DATE_FORMATS))


def format_time(dt: datetime | None, fmt: str = "default") -> str:
    if dt is None:
        return "--"
    return dt.strftime(_get_format(fmt, TIME_FORMATS))


# ── Relative time ─────────────────────────────────────────────────────────────

_TIME_ABBREVIATIONS = [
    (" seconds", "s"),
    (" second", "s"),
    (" minutes", "m"),
    (" minute", "m"),
    (" hours", "h"),
    (" hour", "h"),
    (" days", "d"),
    (" day", "d"),
    (" weeks", "w"),
    (" week", "w"),
    (" months", "mo"),
    (" month", "mo"),
    (" years", "y"),
    (" year", "y"),
]


def _abbreviate_time(text: str) -> str:
    for long_unit, short_unit in _TIME_ABBREVIATIONS:
        if long_unit in text:
            return text.replace(long_unit, short_unit)
    return text


def timeago(
    dt: datetime | None, style: str = "short", now: datetime | None = None
) -> str:
    """Format a datetime as relative time: '5m ago', '2h ago', etc."""
    if dt is None:
        return "--"
    if now is None:
        now = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if dt > now:
        return "just now"

    delta = now - dt
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        long_text = f"{total_seconds} seconds ago"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        long_text = f"{minutes} {'minute' if minutes == 1 else 'minutes'} ago"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        long_text = f"{hours} {'hour' if hours == 1 else 'hours'} ago"
    elif total_seconds < 2592000:
        days = total_seconds // 86400
        long_text = f"{days} {'day' if days == 1 else 'days'} ago"
    elif total_seconds < 31536000:
        months = total_seconds // 2592000
        long_text = f"{months} {'month' if months == 1 else 'months'} ago"
    else:
        years = total_seconds // 31536000
        long_text = f"{years} {'year' if years == 1 else 'years'} ago"

    if style == "long":
        return long_text
    return _abbreviate_time(long_text)


def timeago_color(dt: datetime | None, now: datetime | None = None) -> str:
    """Return Tailwind CSS class based on recency."""
    if dt is None:
        return "text-gray-400"
    if now is None:
        now = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = now - dt
    if delta.days > 0 or delta.seconds >= 3600:
        return "text-gray-400"
    elif delta.seconds >= 300:
        return "text-yellow-600"
    else:
        return "text-green-600"


# ── Duration ──────────────────────────────────────────────────────────────────


def duration(seconds: int | float | None, style: str = "short") -> str:
    """Format seconds as human-readable duration: '2h 15m', '3d 5h'."""
    if seconds is None:
        return "--"
    seconds = int(seconds)
    if seconds < 0:
        return "--"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    if style == "colon":
        if days > 0:
            return f"{days}:{hours:02d}:{minutes:02d}:{secs:02d}"
        elif hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"
    elif style == "long":
        parts = []
        if days > 0:
            parts.append(f"{days} {'day' if days == 1 else 'days'}")
        if hours > 0:
            parts.append(f"{hours} {'hour' if hours == 1 else 'hours'}")
        if minutes > 0:
            parts.append(f"{minutes} {'minute' if minutes == 1 else 'minutes'}")
        if secs > 0 and not parts:
            parts.append(f"{secs} {'second' if secs == 1 else 'seconds'}")
        return " ".join(parts) if parts else "0 seconds"
    else:  # short
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if secs > 0 and not parts:
            parts.append(f"{secs}s")
        return " ".join(parts) if parts else "0s"


# ── Number formatting ─────────────────────────────────────────────────────────


def num(value: float | int | None, decimals: int = 1) -> str:
    """Format number with fixed decimal places: {{ 72.5 | num }} -> '72.5'"""
    if value is None:
        return "--"
    return f"{value:.{decimals}f}"


def number_format(value: int | float | None) -> str:
    """Format number with thousands separator: {{ 12345 | number_format }} -> '12,345'"""
    if value is None:
        return "0"
    return f"{int(value):,}"


def friendly_name(value: str | None) -> str:
    """Convert snake_case to Title Case: {{ 'app_launched' | friendly_name }} -> 'App Launched'"""
    if value is None:
        return "--"
    return value.replace("_", " ").title()


def day_of_week(dt: datetime | None, style: str = "short") -> str:
    if dt is None:
        return "--"
    if style == "number":
        return str(dt.weekday())
    elif style == "long":
        return dt.strftime("%A")
    return dt.strftime("%a")


def month_name(dt: datetime | None, style: str = "short") -> str:
    if dt is None:
        return "--"
    if style == "number":
        return str(dt.month)
    elif style == "long":
        return dt.strftime("%B")
    return dt.strftime("%b")


# ── JSON with datetime support ────────────────────────────────────────────────


def tojson_safe(value: object, indent: int | None = None) -> str:
    """JSON encode with datetime support."""

    def default(obj: object) -> str:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    return json.dumps(value, default=default, indent=indent)


# ── Registration ──────────────────────────────────────────────────────────────


# A build that was never stamped with a commit (local runs, tests) must not pin every asset to a
# constant token: treat each sentinel as unstamped (luxarch --playbook cache-busting).
_UNSTAMPED_COMMITS = {None, "", "unknown", "dev", "local"}


def static_version() -> str:
    """The ?v= cache-bust for first-party static assets: the git short SHA (= BUILD_COMMIT)."""
    commit = settings.BUILD_COMMIT
    return "dev" if commit in _UNSTAMPED_COMMITS or commit is None else commit


def register_filters(templates: Jinja2Templates) -> None:
    """Register all custom filters with a Jinja2Templates instance."""
    # One cache-bust token, from git (FLEET-BUILD-DEPLOY-STANDARD "Static asset cache-busting").
    templates.env.globals["static_version"] = static_version()
    templates.env.globals["now"] = lambda: datetime.now(UTC)
    # Appends a query to a base URL it is GIVEN; owns no route knowledge (luxarch --emit pagination).
    templates.env.globals["paginated_url"] = paginated_url
    # Date/time
    templates.env.filters["localtime"] = localtime
    templates.env.filters["format_datetime"] = format_datetime
    templates.env.filters["datetime"] = format_datetime
    templates.env.filters["date"] = format_date
    templates.env.filters["time"] = format_time
    templates.env.filters["timeago"] = timeago
    templates.env.filters["timeago_color"] = timeago_color
    templates.env.filters["tzname"] = tzname
    templates.env.filters["duration"] = duration
    templates.env.filters["day_of_week"] = day_of_week
    templates.env.filters["month_name"] = month_name

    # Number/string formatting
    templates.env.filters["num"] = num
    templates.env.filters["number_format"] = number_format
    templates.env.filters["friendly_name"] = friendly_name

    # JSON
    templates.env.filters["tojson_safe"] = tojson_safe

    # Globals
    templates.env.globals["current_year"] = datetime.now(UTC).year
