"""Event CRUD — all event table queries."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Float,
    Integer,
    Row,
    and_,
    case,
    cast,
    desc,
    func,
    literal,
    null,
    select,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.event_model import Event

# A WHERE clause as built by time_conditions(): SQLAlchemy boolean predicates, AND-ed by callers.
Conditions = list[ColumnElement[bool]]


def _has_value(expr: ColumnElement[str]) -> ColumnElement[bool]:
    """A JSONB property that carries a value: present, not JSON null, not an empty string.
    One rule for every property aggregate, so a panel's totals and its rows agree."""
    return and_(expr.isnot(None), expr != "")


class EventWriteCRUD:
    """Write operations for events."""

    async def bulk_insert(
        self, db: AsyncSession, rows: list[dict[str, Any]]
    ) -> set[str]:
        """Insert the rows; one whose (app_id, client_event_id) is already stored is skipped.
        Returns the ids of the rows actually inserted."""
        if not rows:
            return set()
        stmt = (
            pg_insert(Event)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["app_id", "client_event_id"],
                index_where=Event.client_event_id.isnot(None),
            )
            .returning(Event.id)
        )
        result = await db.execute(stmt)
        return {str(row_id) for row_id in result.scalars().all()}


class EventCRUD:
    """All database queries against the events table."""

    # ── Counts ────────────────────────────────────────────────────────────

    async def count(self, db: AsyncSession, conditions: Conditions) -> int:
        result = await db.execute(
            select(func.count(Event.id)).where(and_(true(), *conditions))
        )
        return result.scalar() or 0

    async def count_unique_users(self, db: AsyncSession, conditions: Conditions) -> int:
        result = await db.execute(
            select(func.count(func.distinct(Event.user_id))).where(
                and_(true(), *conditions)
            )
        )
        return result.scalar() or 0

    async def count_unique_devices(
        self, db: AsyncSession, conditions: Conditions
    ) -> int:
        result = await db.execute(
            select(func.count(func.distinct(Event.device_id))).where(
                and_(true(), *conditions), Event.device_id.isnot(None)
            )
        )
        return result.scalar() or 0

    # ── Reads ─────────────────────────────────────────────────────────────

    async def get_by_id(self, db: AsyncSession, event_id: str) -> Event | None:
        result = await db.execute(select(Event).where(Event.id == event_id))
        return result.scalar_one_or_none()

    async def get_filtered(
        self,
        db: AsyncSession,
        *,
        skip: int,
        limit: int,
        app_id: str | None = None,
        event_name: str | None = None,
        user_id: str | None = None,
        hours: int = 24,
    ) -> tuple[list[Event], int]:
        """Returns (events, total_count)."""
        # time_conditions: hours=0 is "All" (it read "the last 0 hours", an always-empty list).
        conditions = self.time_conditions(hours, app_id)
        if event_name:
            conditions.append(Event.name.icontains(event_name, autoescape=True))
        if user_id:
            conditions.append(Event.user_id == user_id)
        where = and_(true(), *conditions)
        total = (
            await db.execute(select(func.count(Event.id)).where(where))
        ).scalar() or 0
        query = (
            select(Event)
            .where(where)
            .order_by(desc(Event.received_at))
            .limit(limit)
            .offset(skip)
        )
        result = await db.execute(query)
        return list(result.scalars().all()), total

    async def search(
        self,
        db: AsyncSession,
        query_str: str,
        search_type: str = "event_name",
        limit: int = 50,
    ) -> list[Event]:
        if search_type == "user_id":
            q = select(Event).where(Event.user_id.icontains(query_str, autoescape=True))
        elif search_type == "metadata":
            q = select(Event).where(
                Event.properties.astext.icontains(query_str, autoescape=True)
            )
        else:
            q = select(Event).where(Event.name.icontains(query_str, autoescape=True))
        q = q.order_by(desc(Event.received_at)).limit(limit)
        result = await db.execute(q)
        return list(result.scalars().all())

    # ── Aggregations ──────────────────────────────────────────────────────

    async def top_event_names(
        self, db: AsyncSession, conditions: Conditions, limit: int = 10
    ) -> list[dict[str, Any]]:
        query = (
            select(Event.name, func.count(Event.id).label("count"))
            .where(and_(true(), *conditions))
            .group_by(Event.name)
            .order_by(desc("count"))
            .limit(limit)
        )
        result = await db.execute(query)
        return [{"name": row.name, "count": row.count} for row in result.all()]

    async def count_by_column[T](
        self,
        db: AsyncSession,
        column: InstrumentedAttribute[T | None],
        conditions: Conditions,
        limit: int = 20,
    ) -> list[tuple[T, int]]:
        """GROUP BY a promoted column. Returns [(value, count)], NULLs excluded."""
        query = (
            select(column, func.count().label("cnt"))
            .where(and_(true(), *conditions), column.isnot(None))
            .group_by(column)
            .order_by(desc("cnt"))
            .limit(limit)
        )
        result = await db.execute(query)
        return [(value, count) for value, count in result.all() if value is not None]

    async def count_by_property(
        self, db: AsyncSession, key: str, conditions: Conditions, limit: int = 20
    ) -> list[tuple[Any, int]]:
        """GROUP BY a JSONB properties key. Returns [(value, count)]; a JSON null or "" is no value."""
        prop_expr = Event.properties[key].astext
        query = (
            select(prop_expr.label("val"), func.count().label("cnt"))
            .where(and_(true(), *conditions), _has_value(prop_expr))
            .group_by(prop_expr)
            .order_by(desc("cnt"))
            .limit(limit)
        )
        result = await db.execute(query)
        return [(row[0], row[1]) for row in result.all()]

    async def get_by_names(
        self,
        db: AsyncSession,
        names: list[str],
        conditions: Conditions,
        limit: int = 2000,
    ) -> list[Event]:
        """The newest events with one of `names`, newest first."""
        query = (
            select(Event)
            .where(and_(true(), *conditions), Event.name.in_(names))
            .order_by(desc(Event.received_at))
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    # ── Timeline ──────────────────────────────────────────────────────────

    # Fixed-width buckets are binned from midnight UTC, so a 6-hour bucket is 00/06/12/18.
    _BIN_ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)
    _BIN_STRIDES = {
        "5min": timedelta(minutes=5),
        "30min": timedelta(minutes=30),
        "6hour": timedelta(hours=6),
    }
    _TRUNC_UNITS = {"1hour": "hour", "1day": "day", "1week": "week", "1month": "month"}

    @classmethod
    def _bucket_expression(cls, bucket_key: str) -> ColumnElement[datetime]:
        """The start of the timeline bucket each event falls in, built from SQLAlchemy constructs
        with every unit and stride a bound value, so no SQL is text (fw.no_dynamic_column_sort)."""
        if bucket_key in cls._BIN_STRIDES:
            return func.date_bin(
                literal(cls._BIN_STRIDES[bucket_key]),
                Event.received_at,
                literal(cls._BIN_ORIGIN),
            )
        if bucket_key in cls._TRUNC_UNITS:
            return func.date_trunc(cls._TRUNC_UNITS[bucket_key], Event.received_at)
        raise ValueError(f"Invalid bucket key: {bucket_key}")

    async def timeline_buckets(
        self, db: AsyncSession, bucket_key: str, conditions: Conditions
    ) -> list[Row[datetime, int]]:
        """(bucket start, event count) per bucket, oldest first."""
        bucket = self._bucket_expression(bucket_key).label("bucket")
        query = (
            select(bucket, func.count(Event.id).label("count"))
            .where(and_(true(), *conditions))
            .group_by(bucket)
            .order_by(bucket)
        )
        result = await db.execute(query)
        return list(result.all())

    async def hourly_counts(
        self, db: AsyncSession, conditions: Conditions
    ) -> list[Row[Any]]:
        bucket = func.date_trunc("hour", Event.received_at).label("hour")
        query = (
            select(bucket, func.count().label("cnt"))
            .where(and_(true(), *conditions))
            .group_by(bucket)
            .order_by(bucket)
        )
        result = await db.execute(query)
        return list(result.all())

    # ── Performance ───────────────────────────────────────────────────────

    @staticmethod
    def _duration_ms() -> ColumnElement[float]:
        """`properties.duration_ms` as a number when it is one (`150`, `12.5`), else NULL, which the
        aggregates skip. A metadata value is whatever the SDK sent, so a plain CAST would fail the whole
        page on one "12.5" or "abc"."""
        raw = Event.properties["duration_ms"].astext
        return case(
            (raw.regexp_match(r"^[0-9]{1,12}(\.[0-9]+)?$"), cast(raw, Float)),
            else_=null(),
        )

    async def performance_by_operation(
        self, db: AsyncSession, conditions: Conditions
    ) -> list[Row[Any]]:
        """Aggregate performance stats per operation using percentile_cont."""
        duration_col = self._duration_ms()
        operation_col = Event.properties["operation"].astext
        success_col = Event.properties["success"].astext

        query = (
            select(
                operation_col.label("operation"),
                func.count().label("total_count"),
                # No row with a success flag sums to NULL: count it as 0 successes.
                func.coalesce(
                    func.sum(func.cast(success_col == "true", Integer)), 0
                ).label("success_count"),
                func.min(duration_col).label("min_ms"),
                func.max(duration_col).label("max_ms"),
                func.avg(duration_col).label("mean_ms"),
                func.percentile_cont(0.5).within_group(duration_col).label("p50"),
                func.percentile_cont(0.75).within_group(duration_col).label("p75"),
                func.percentile_cont(0.9).within_group(duration_col).label("p90"),
                func.percentile_cont(0.95).within_group(duration_col).label("p95"),
                func.percentile_cont(0.99).within_group(duration_col).label("p99"),
            )
            .where(and_(true(), *conditions), Event.properties.has_key("duration_ms"))
            .group_by(operation_col)
            .order_by(desc("total_count"))
        )
        return list((await db.execute(query)).all())

    async def performance_global(
        self, db: AsyncSession, conditions: Conditions
    ) -> Row[Any]:
        """Global performance percentiles across all operations."""
        duration_col = self._duration_ms()
        query = select(
            func.count().label("total"),
            func.min(duration_col).label("min"),
            func.max(duration_col).label("max"),
            func.avg(duration_col).label("mean"),
            func.percentile_cont(0.5).within_group(duration_col).label("p50"),
            func.percentile_cont(0.75).within_group(duration_col).label("p75"),
            func.percentile_cont(0.9).within_group(duration_col).label("p90"),
            func.percentile_cont(0.95).within_group(duration_col).label("p95"),
            func.percentile_cont(0.99).within_group(duration_col).label("p99"),
        ).where(and_(true(), *conditions), Event.properties.has_key("duration_ms"))
        return (await db.execute(query)).one()

    # ── Conditions builder ────────────────────────────────────────────────

    async def get_apps_with_stats(
        self, db: AsyncSession
    ) -> list[Row[str, int, int, datetime]]:
        """Get app_ids with event counts, unique users, last event."""
        query = (
            select(
                Event.app_id,
                func.count(Event.id).label("total_events"),
                func.count(func.distinct(Event.user_id)).label("unique_users"),
                func.max(Event.received_at).label("last_event"),
            )
            .group_by(Event.app_id)
            .order_by(desc(func.count(Event.id)))
        )
        # unbounded-reads: one row per app_id; ingest stores only an HMAC_KEYS key or a registered App's id
        result = await db.execute(query)
        return list(result.all())

    async def get_apps_for_dropdown(self, db: AsyncSession) -> list[Row[str, int]]:
        """Get app_ids with event counts for dropdown."""
        query = (
            select(Event.app_id, func.count(Event.id).label("total_events"))
            .group_by(Event.app_id)
            .order_by(Event.app_id)
        )
        # unbounded-reads: one row per app_id; ingest stores only an HMAC_KEYS key or a registered App's id
        result = await db.execute(query)
        return list(result.all())

    async def get_button_flows(
        self, db: AsyncSession, conditions: Conditions, limit: int = 15
    ) -> list[tuple[str | None, str, int]]:
        """(screen, button, count) for the commonest screen→button taps."""
        screen_prop = Event.properties["screen"].astext
        button_prop = Event.properties["button"].astext
        query = (
            select(
                screen_prop.label("screen"),
                button_prop.label("button"),
                func.count().label("cnt"),
            )
            .where(and_(true(), *conditions), Event.properties.has_key("button"))
            .group_by(screen_prop, button_prop)
            .order_by(desc("cnt"))
            .limit(limit)
        )
        result = await db.execute(query)
        return [(r.screen, r.button, r.cnt) for r in result.all()]

    async def get_recent(
        self, db: AsyncSession, conditions: Conditions, limit: int = 10
    ) -> list[Event]:
        """Get most recent events matching conditions."""
        query = (
            select(Event)
            .where(and_(true(), *conditions))
            .order_by(desc(Event.received_at))
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    # ── User / Session queries ─────────────────────────────────────────

    async def get_events_by_session(
        self, db: AsyncSession, session_id: str, limit: int = 200
    ) -> list[Event]:
        query = (
            select(Event)
            .where(Event.session_id == session_id)
            .order_by(Event.received_at)
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_session_span(
        self, db: AsyncSession, session_id: str
    ) -> Row[int, datetime | None, datetime | None]:
        """(event count, first received_at, last received_at) over the WHOLE session, which the
        capped event list cannot give."""
        query = select(
            func.count(Event.id),
            func.min(Event.received_at),
            func.max(Event.received_at),
        ).where(Event.session_id == session_id)
        return (await db.execute(query)).one()

    async def get_user_sessions(
        self, db: AsyncSession, user_id: str, limit: int
    ) -> list[Row[str | None, int, datetime, datetime, Sequence[Any]]]:
        """The user's `limit` most recent sessions, with first/last event and event count."""
        query = (
            select(
                Event.session_id,
                func.count(Event.id).label("event_count"),
                func.min(Event.received_at).label("first_event"),
                func.max(Event.received_at).label("last_event"),
                func.array_agg(func.distinct(Event.name)).label("event_types"),
            )
            .where(Event.user_id == user_id, Event.session_id.isnot(None))
            .group_by(Event.session_id)
            .order_by(desc(func.max(Event.received_at)))
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.all())

    async def get_user_devices(
        self, db: AsyncSession, user_id: str
    ) -> list[
        Row[str | None, str | None, str | None, str | None, str | None, int, datetime]
    ]:
        """Get distinct devices used by a user."""
        query = (
            select(
                Event.device_id,
                Event.device_model,
                Event.os_version,
                Event.app_version,
                Event.platform,
                func.count(Event.id).label("event_count"),
                func.max(Event.received_at).label("last_seen"),
            )
            .where(Event.user_id == user_id, Event.device_id.isnot(None))
            .group_by(
                Event.device_id,
                Event.device_model,
                Event.os_version,
                Event.app_version,
                Event.platform,
            )
            .order_by(desc(func.max(Event.received_at)))
        )
        result = await db.execute(query)
        return list(result.all())

    async def get_user_summary(self, db: AsyncSession, user_id: str) -> dict[str, Any]:
        """Aggregate stats for a single user."""
        query = select(
            func.count(Event.id).label("total_events"),
            func.count(func.distinct(Event.session_id)).label("total_sessions"),
            func.count(func.distinct(Event.device_id)).label("total_devices"),
            func.count(func.distinct(Event.name)).label("event_types"),
            func.min(Event.received_at).label("first_seen"),
            func.max(Event.received_at).label("last_seen"),
        ).where(Event.user_id == user_id)
        result = (await db.execute(query)).one()
        return {
            "total_events": result.total_events,
            "total_sessions": result.total_sessions,
            "total_devices": result.total_devices,
            "event_types": result.event_types,
            "first_seen": result.first_seen,
            "last_seen": result.last_seen,
        }

    async def get_screen_transitions(
        self,
        db: AsyncSession,
        hours: int = 720,
        app_id: str | None = None,
        limit: int = 50,
    ) -> list[tuple[str, str, int]]:
        """Consecutive screen-to-screen transitions within sessions using LAG window function."""
        result = await db.execute(
            text("""
            WITH screen_events AS (
                SELECT
                    session_id,
                    properties->>'screen' AS screen,
                    received_at,
                    LAG(properties->>'screen') OVER (
                        PARTITION BY session_id ORDER BY received_at
                    ) AS prev_screen
                FROM events
                WHERE name = 'screen_viewed'
                  AND properties->>'screen' IS NOT NULL
                  AND session_id IS NOT NULL
                  AND (:hours <= 0 OR received_at >= NOW() - make_interval(hours => :hours))
                  AND (CAST(:app_id AS VARCHAR) IS NULL OR app_id = :app_id)
            )
            SELECT prev_screen, screen AS next_screen, COUNT(*) AS cnt
            FROM screen_events
            WHERE prev_screen IS NOT NULL
              AND prev_screen != screen
            GROUP BY prev_screen, screen
            ORDER BY cnt DESC
            LIMIT :limit
        """),
            {"hours": hours, "limit": limit, "app_id": app_id},
        )
        return [(r.prev_screen, r.next_screen, r.cnt) for r in result.all()]

    async def get_screen_entry_points(
        self, db: AsyncSession, hours: int = 720, app_id: str | None = None
    ) -> list[tuple[str, int]]:
        """First screen viewed per session (entry points)."""
        result = await db.execute(
            text("""
            WITH first_screens AS (
                SELECT DISTINCT ON (session_id)
                    session_id,
                    properties->>'screen' AS screen
                FROM events
                WHERE name = 'screen_viewed'
                  AND properties->>'screen' IS NOT NULL
                  AND session_id IS NOT NULL
                  AND (:hours <= 0 OR received_at >= NOW() - make_interval(hours => :hours))
                  AND (CAST(:app_id AS VARCHAR) IS NULL OR app_id = :app_id)
                ORDER BY session_id, received_at
            )
            SELECT screen, COUNT(*) AS cnt
            FROM first_screens
            GROUP BY screen
            ORDER BY cnt DESC
            LIMIT 20
        """),
            {"hours": hours, "app_id": app_id},
        )
        return [(r.screen, r.cnt) for r in result.all()]

    async def get_screen_dwell_times(
        self, db: AsyncSession, hours: int = 720, app_id: str | None = None
    ) -> list[Row[Any]]:
        """Average time spent on each screen (seconds between arriving and leaving)."""
        result = await db.execute(
            text("""
            WITH screen_events AS (
                SELECT
                    session_id,
                    properties->>'screen' AS screen,
                    received_at,
                    LEAD(received_at) OVER (
                        PARTITION BY session_id ORDER BY received_at
                    ) AS next_event_at
                FROM events
                WHERE name = 'screen_viewed'
                  AND properties->>'screen' IS NOT NULL
                  AND session_id IS NOT NULL
                  AND (:hours <= 0 OR received_at >= NOW() - make_interval(hours => :hours))
                  AND (CAST(:app_id AS VARCHAR) IS NULL OR app_id = :app_id)
            )
            SELECT
                screen,
                COUNT(*) AS visit_count,
                ROUND(AVG(EXTRACT(EPOCH FROM (next_event_at - received_at)))::numeric, 1) AS avg_seconds,
                ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (next_event_at - received_at))
                )::numeric, 1) AS median_seconds,
                MAX(EXTRACT(EPOCH FROM (next_event_at - received_at)))::int AS max_seconds
            FROM screen_events
            WHERE next_event_at IS NOT NULL
              AND EXTRACT(EPOCH FROM (next_event_at - received_at)) < 600
              AND EXTRACT(EPOCH FROM (next_event_at - received_at)) > 0
            GROUP BY screen
            ORDER BY avg_seconds DESC
        """),
            {"hours": hours, "app_id": app_id},
        )
        return list(result.all())

    async def get_exit_screens(
        self, db: AsyncSession, hours: int = 720, app_id: str | None = None
    ) -> list[tuple[str, int]]:
        """Screens users were on before backgrounding the app."""
        result = await db.execute(
            text("""
            WITH bg_events AS (
                SELECT session_id, received_at
                FROM events
                WHERE name = 'app_entered_background'
                  AND session_id IS NOT NULL
                  AND (:hours <= 0 OR received_at >= NOW() - make_interval(hours => :hours))
                  AND (CAST(:app_id AS VARCHAR) IS NULL OR app_id = :app_id)
            ),
            last_screen AS (
                SELECT DISTINCT ON (bg.session_id, bg.received_at)
                    bg.session_id,
                    e.properties->>'screen' AS screen
                FROM bg_events bg
                JOIN events e ON e.session_id = bg.session_id
                    AND e.name = 'screen_viewed'
                    AND e.properties->>'screen' IS NOT NULL
                    AND e.received_at < bg.received_at
                    AND e.received_at > bg.received_at - INTERVAL '5 minutes'
                ORDER BY bg.session_id, bg.received_at, e.received_at DESC
            )
            SELECT screen, COUNT(*) AS exit_count
            FROM last_screen
            GROUP BY screen
            ORDER BY exit_count DESC
            LIMIT 20
        """),
            {"hours": hours, "app_id": app_id},
        )
        return [(r.screen, r.exit_count) for r in result.all()]

    async def get_session_metrics(
        self, db: AsyncSession, hours: int = 720, app_id: str | None = None
    ) -> dict[str, Any]:
        """Aggregate session-level metrics: avg screens, avg duration, error rate."""
        result = await db.execute(
            text("""
            WITH session_stats AS (
                SELECT
                    session_id,
                    COUNT(*) FILTER (WHERE name = 'screen_viewed') AS screen_count,
                    COUNT(*) AS event_count,
                    COUNT(*) FILTER (WHERE name = 'error_occurred') AS error_count,
                    MAX(received_at) - MIN(received_at) AS duration
                FROM events
                WHERE session_id IS NOT NULL
                  AND (:hours <= 0 OR received_at >= NOW() - make_interval(hours => :hours))
                  AND (CAST(:app_id AS VARCHAR) IS NULL OR app_id = :app_id)
                GROUP BY session_id
            )
            SELECT
                COUNT(*) AS total_sessions,
                ROUND(AVG(screen_count)::numeric, 1) AS avg_screens,
                ROUND(AVG(event_count)::numeric, 1) AS avg_events,
                ROUND(AVG(EXTRACT(EPOCH FROM duration))::numeric, 0) AS avg_duration_seconds,
                ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM duration))::numeric, 0) AS median_duration_seconds,
                SUM(error_count) AS total_errors,
                COUNT(*) FILTER (WHERE error_count > 0) AS sessions_with_errors
            FROM session_stats
            WHERE event_count > 1
        """),
            {"hours": hours, "app_id": app_id},
        )
        row = result.one()
        return {
            "total_sessions": row.total_sessions or 0,
            "avg_screens": float(row.avg_screens or 0),
            "avg_events": float(row.avg_events or 0),
            "avg_duration_seconds": int(row.avg_duration_seconds or 0),
            "median_duration_seconds": int(row.median_duration_seconds or 0),
            "total_errors": row.total_errors or 0,
            "sessions_with_errors": row.sessions_with_errors or 0,
        }

    async def get_events_with_properties(
        self, db: AsyncSession, conditions: Conditions, limit: int = 500
    ) -> list[Event]:
        """Get events that have non-null properties for metadata analysis."""
        query = (
            select(Event)
            .where(and_(true(), *conditions), Event.properties.isnot(None))
            .order_by(desc(Event.received_at))
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def property_key_summary(
        self, db: AsyncSession, key: str, conditions: Conditions
    ) -> Row[int, int, int]:
        """Over every event with a value for `key`: how many, distinct values, distinct names."""
        value = Event.properties[key].astext
        query = select(
            func.count(Event.id),
            func.count(func.distinct(value)),
            func.count(func.distinct(Event.name)),
        ).where(and_(true(), *conditions), _has_value(value))
        return (await db.execute(query)).one()

    async def property_values_by_name(
        self, db: AsyncSession, key: str, conditions: Conditions, limit: int
    ) -> list[Row[str, str, int]]:
        """(event name, value of `key`, count), commonest first; null and "" are no value."""
        value = Event.properties[key].astext
        query = (
            select(Event.name, value, func.count().label("cnt"))
            .where(and_(true(), *conditions), _has_value(value))
            .group_by(Event.name, value)
            .order_by(desc("cnt"))
            .limit(limit)
        )
        return list((await db.execute(query)).all())

    async def get_events_with_property_key(
        self, db: AsyncSession, key: str, conditions: Conditions, limit: int = 200
    ) -> list[Event]:
        """Get events that have a specific key in properties."""
        query = (
            select(Event)
            .where(and_(true(), *conditions), _has_value(Event.properties[key].astext))
            .order_by(desc(Event.received_at))
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    def time_conditions(
        hours: int, app_id: str | None = None, event_name: str | None = None
    ) -> Conditions:
        """Build standard time + app_id filter conditions. hours=0 means all time."""
        conditions: Conditions = []
        if hours > 0:
            since = datetime.now(UTC) - timedelta(hours=hours)
            conditions.append(Event.received_at >= since)
        if app_id:
            conditions.append(Event.app_id == app_id)
        if event_name:
            conditions.append(Event.name == event_name)
        return conditions


event_crud = EventCRUD()
