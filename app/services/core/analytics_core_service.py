"""Analytics core service — business logic between view services and CRUD.

View services call this. This calls CRUD. Never the other way around.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tracing import create_service_span
from app.crud.device_crud import device_crud
from app.crud.event_crud import Conditions, event_crud
from app.models.device_model import Device
from app.models.event_model import Event
from app.schemas.event_schema import EventInDB

# The user profile panel lists the newest of each; its headings show the full totals.
PROFILE_SESSIONS_SHOWN = 50
PROFILE_ERRORS_SHOWN = 20
PROFILE_SCREENS_SHOWN = 30
ERROR_EVENT_NAMES = ["error_occurred", "authentication_attempt"]
# The errors table lists each error type's busiest screens and its commonest messages.
ERROR_TOP_SCREENS = 3
ERROR_TOP_MESSAGES = 2
# The key deep-dive panel: its value distribution, its per-event-type breakdown, its samples.
KEY_TOP_VALUES = 15
KEY_VALUES_BY_EVENT = 100
KEY_SAMPLES = 20


def _as_text(value: object) -> str:
    """A JSONB value as Postgres's ->> renders it, so a sample matches the SQL distribution:
    a string as-is, anything else as JSON (true, 3, {"a": 1})."""
    return value if isinstance(value, str) else json.dumps(value)


class AnalyticsCoreService:
    """Core business logic for analytics queries."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Events ────────────────────────────────────────────────────────────

    async def get_event_by_id(self, event_id: str) -> EventInDB | None:
        with create_service_span(
            "AnalyticsCoreService", "get_event_by_id", event_id=event_id
        ):
            event = await event_crud.get_by_id(self.db, event_id)
            return EventInDB.model_validate(event) if event is not None else None

    async def get_filtered_events(
        self,
        *,
        skip: int,
        limit: int,
        app_id: str | None = None,
        event_name: str | None = None,
        user_id: str | None = None,
        hours: int = 24,
    ) -> tuple[list[EventInDB], int]:
        """One window of matching events (newest first) and the total that match."""
        with create_service_span(
            "AnalyticsCoreService",
            "get_filtered_events",
            app_id=app_id,
            user_id=user_id,
        ):
            events, total = await event_crud.get_filtered(
                self.db,
                skip=skip,
                limit=limit,
                app_id=app_id,
                event_name=event_name,
                user_id=user_id,
                hours=hours,
            )
            return [EventInDB.model_validate(e) for e in events], total

    async def search_events(
        self, query: str, search_type: str = "event_name", limit: int = 50
    ) -> list[EventInDB]:
        with create_service_span("AnalyticsCoreService", "search_events"):
            events = await event_crud.search(self.db, query, search_type, limit)
            return [EventInDB.model_validate(e) for e in events]

    # ── Overview Stats ────────────────────────────────────────────────────

    async def get_stats_overview(
        self, app_id: str | None = None, hours: int = 24
    ) -> dict[str, Any]:
        with create_service_span(
            "AnalyticsCoreService", "get_stats_overview", app_id=app_id
        ):
            conditions = event_crud.time_conditions(hours, app_id)
            total = await event_crud.count(self.db, conditions)
            unique_users = await event_crud.count_unique_users(self.db, conditions)
            top_events = await event_crud.top_event_names(self.db, conditions)
            return {
                "total_events": total,
                "unique_users": unique_users,
                "top_events": top_events,
                "hours": hours,
            }

    # ── Apps Dropdown ─────────────────────────────────────────────────────

    async def get_apps_with_stats(self) -> list[dict[str, Any]]:
        with create_service_span("AnalyticsCoreService", "get_apps_with_stats"):
            rows = await event_crud.get_apps_with_stats(self.db)
            return [
                {
                    "app_id": r.app_id,
                    "total_events": r.total_events,
                    "unique_users": r.unique_users,
                    "last_event": r.last_event.isoformat() if r.last_event else None,
                }
                for r in rows
            ]

    async def get_apps_for_dropdown(self) -> list[dict[str, Any]]:
        with create_service_span("AnalyticsCoreService", "get_apps_for_dropdown"):
            rows = await event_crud.get_apps_for_dropdown(self.db)
            return [{"app_id": r.app_id, "total_events": r.total_events} for r in rows]

    # ── Timeline ──────────────────────────────────────────────────────────

    async def get_timeline_data(
        self, app_id: str | None = None, hours: int = 24
    ) -> list[dict[str, Any]]:
        with create_service_span(
            "AnalyticsCoreService", "get_timeline_data", app_id=app_id
        ):
            effective_hours = (
                hours if hours > 0 else 87600
            )  # All time = 10 years for bucketing
            since = datetime.now(UTC) - timedelta(hours=effective_hours)
            conditions = event_crud.time_conditions(hours, app_id)

            if effective_hours <= 1:
                bucket_key = "5min"
                bucket_size, delta = "5 minutes", timedelta(minutes=5)
            elif effective_hours <= 6:
                bucket_key = "30min"
                bucket_size, delta = "30 minutes", timedelta(minutes=30)
            elif effective_hours <= 24:
                bucket_key = "1hour"
                bucket_size, delta = "1 hour", timedelta(hours=1)
            elif effective_hours <= 168:
                bucket_key = "6hour"
                bucket_size, delta = "6 hours", timedelta(hours=6)
            elif effective_hours <= 720:
                bucket_key = "1day"
                bucket_size, delta = "1 day", timedelta(days=1)
            elif effective_hours <= 8760:
                bucket_key = "1week"
                bucket_size, delta = "1 week", timedelta(weeks=1)
            else:
                bucket_key = "1month"
                bucket_size, delta = "1 month", timedelta(days=30)

            raw = await event_crud.timeline_buckets(self.db, bucket_key, conditions)
            if not raw:
                return []

            # Fill gaps
            data_lookup = {}
            for row in raw:
                bt = row.bucket
                if bt.tzinfo is None:
                    bt = bt.replace(tzinfo=UTC)
                data_lookup[bt] = row.count

            current = since.replace(second=0, microsecond=0)
            if hours <= 1:
                current = current.replace(minute=(current.minute // 5) * 5)
            elif hours <= 6:
                current = current.replace(minute=(current.minute // 30) * 30)
            elif hours <= 24:
                current = current.replace(minute=0)
            elif hours <= 168:
                current = current.replace(minute=0, hour=(current.hour // 6) * 6)
            else:
                current = current.replace(minute=0, hour=0)

            complete: list[dict[str, Any]] = []
            end = datetime.now(UTC)
            while current <= end and len(complete) < 200:
                complete.append(
                    {
                        "hour": current.isoformat(),
                        "count": data_lookup.get(current, 0),
                        "bucket_size": bucket_size,
                    }
                )
                current += delta
            return complete

    # ── Device Analytics ──────────────────────────────────────────────────

    async def get_device_analytics(
        self, app_id: str | None = None, hours: int = 24
    ) -> dict[str, Any]:
        with create_service_span(
            "AnalyticsCoreService", "get_device_analytics", app_id=app_id
        ):
            conditions = event_crud.time_conditions(hours, app_id)

            device_models = await event_crud.count_by_column(
                self.db, Event.device_model, conditions
            )
            system_versions = await event_crud.count_by_column(
                self.db, Event.os_version, conditions
            )
            app_versions = await event_crud.count_by_column(
                self.db, Event.app_version, conditions
            )

            build_numbers = await device_crud.count_by_field(
                self.db, Device.build_number, app_id, hours
            )
            screen_resolutions = await device_crud.count_by_field(
                self.db, Device.screen_resolution, app_id, hours
            )
            locales = await device_crud.count_by_field(
                self.db, Device.locale, app_id, hours
            )
            timezones = await device_crud.count_by_field(
                self.db, Device.timezone, app_id, hours
            )

            unique_devices = await event_crud.count_unique_devices(self.db, conditions)
            total_events = await event_crud.count(self.db, conditions)
            testflight_vs_appstore = await device_crud.testflight_stats(
                self.db, app_id, hours
            )

            launch_conditions = conditions + [Event.name == "app_launched"]
            launch_types = await event_crud.count_by_property(
                self.db, "launch_type", launch_conditions
            )
            auth_stats = await self._bool_property_stats(
                self.db, "authentication_required", launch_conditions
            )
            onboarding_stats = await self._bool_property_stats(
                self.db, "onboarding_shown", launch_conditions
            )

            device_details = await self._format_device_details(app_id, hours)
            insights = self._build_device_insights(
                unique_devices, device_models, testflight_vs_appstore, app_versions
            )

            return {
                "unique_devices": unique_devices,
                "total_events": total_events,
                "device_models": device_models,
                "system_versions": system_versions,
                "app_versions": app_versions,
                "build_numbers": build_numbers,
                "screen_resolutions": screen_resolutions,
                "locales": locales,
                "timezones": timezones,
                "testflight_vs_appstore": testflight_vs_appstore,
                "launch_types": launch_types,
                "authentication_stats": {
                    "required": auth_stats.get("true", 0),
                    "not_required": auth_stats.get("false", 0),
                },
                "onboarding_stats": {
                    "shown": onboarding_stats.get("true", 0),
                    "not_shown": onboarding_stats.get("false", 0),
                },
                "device_details": device_details,
                "insights": insights,
            }

    async def _bool_property_stats(
        self, db: AsyncSession, key: str, conditions: Conditions
    ) -> dict[Any, int]:
        """{property value: event count} for one JSONB properties key."""
        with create_service_span("AnalyticsCoreService", "_bool_property_stats"):
            return dict(await event_crud.count_by_property(db, key, conditions))

    async def _format_device_details(
        self, app_id: str | None, hours: int = 0
    ) -> list[dict[str, Any]]:
        with create_service_span(
            "AnalyticsCoreService", "_format_device_details", app_id=app_id
        ):
            raw = await device_crud.get_details_with_event_counts(
                self.db, app_id, hours
            )
            details = []
            for row in raw:
                device, total_events, event_types = row[0], row[1] or 0, row[2] or 0
                days = (device.last_seen - device.first_seen).days + 1
                details.append(
                    {
                        "device_id": device.device_id[:8] + "...",
                        # A row is one app's install: across all apps a shared phone has one each.
                        "app_id": device.app_id,
                        "model": device.device_model or "Unknown",
                        "ios_version": device.os_version or "Unknown",
                        "app_version": device.app_version or "Unknown",
                        "build_number": device.build_number or "Unknown",
                        "is_testflight": device.is_testflight or False,
                        "total_events": total_events,
                        "event_types": event_types,
                        "days_active": days,
                        "locale": device.locale or "Unknown",
                        "timezone": device.timezone or "Unknown",
                        "screen_resolution": device.screen_resolution or "Unknown",
                    }
                )
            return details

    def _build_device_insights(
        self,
        unique_devices: int,
        device_models: list[tuple[str, int]],
        testflight: dict[str, int],
        app_versions: list[tuple[str, int]],
    ) -> list[dict[str, str]]:
        insights = []
        if unique_devices > 1:
            insights.append(
                {
                    "type": "info",
                    "title": f"Testing on {unique_devices} unique devices",
                    # post-db-filter: the sentence names the three most common models; the
                    # devices page renders the full device_models list from the same query.
                    "description": f"Device models: {', '.join(m[0] for m in device_models[:3])}",
                }
            )
        elif unique_devices == 1 and device_models:
            insights.append(
                {
                    "type": "info",
                    "title": "Single device testing",
                    "description": f"All data from {device_models[0][0]}",
                }
            )
        total_dist = testflight["testflight"] + testflight["appstore"]
        if total_dist > 0 and (testflight["testflight"] / total_dist * 100) > 90:
            insights.append(
                {
                    "type": "warning",
                    "title": f"{testflight['testflight'] / total_dist * 100:.1f}% TestFlight usage",
                    "description": "Consider testing with App Store builds",
                }
            )
        if len(app_versions) > 1:
            insights.append(
                {
                    "type": "attention",
                    "title": f"{len(app_versions)} app versions",
                    "description": f"Versions: {', '.join(v[0] for v in app_versions)}",
                }
            )
        return insights

    # ── Feature Analytics ─────────────────────────────────────────────────

    async def get_feature_analytics(
        self, app_id: str | None = None, hours: int = 24
    ) -> dict[str, Any]:
        with create_service_span(
            "AnalyticsCoreService", "get_feature_analytics", app_id=app_id
        ):
            conditions = event_crud.time_conditions(hours, app_id)
            total = await event_crud.count(self.db, conditions)

            features_raw = await event_crud.count_by_property(
                self.db, "feature", conditions
            )
            screens_raw = await event_crud.count_by_property(
                self.db, "screen", conditions
            )

            # Get event breakdown per feature/screen
            features = []
            for name, count in features_raw:
                sub_conditions = conditions + [
                    Event.properties["feature"].astext == name
                ]
                top_events = await event_crud.top_event_names(
                    self.db, sub_conditions, limit=5
                )
                features.append(
                    {
                        "name": name,
                        "total": count,
                        "top_events": [(e["name"], e["count"]) for e in top_events],
                    }
                )

            screens = []
            for name, count in screens_raw:
                sub_conditions = conditions + [
                    Event.properties["screen"].astext == name
                ]
                top_events = await event_crud.top_event_names(
                    self.db, sub_conditions, limit=5
                )
                screens.append(
                    {
                        "name": name,
                        "total": count,
                        "top_events": [(e["name"], e["count"]) for e in top_events],
                    }
                )

            button_conditions = conditions + [
                Event.name.in_(["button_tap", "button_tapped"])
            ]
            buttons = await event_crud.count_by_property(
                self.db, "button", button_conditions
            )

            return {
                "features": features,
                "screens": screens,
                "buttons": buttons,
                "total_events": total,
            }

    # ── Error Analytics ───────────────────────────────────────────────────

    async def get_error_analytics(
        self, app_id: str | None = None, hours: int = 24
    ) -> dict[str, Any]:
        with create_service_span(
            "AnalyticsCoreService", "get_error_analytics", app_id=app_id
        ):
            conditions = event_crud.time_conditions(hours, app_id)
            error_conditions = conditions + [Event.name == "error_occurred"]

            error_types_raw = await event_crud.count_by_property(
                self.db, "error_type", error_conditions
            )
            error_types = []
            for etype, cnt in error_types_raw:
                screen_conds = error_conditions + [
                    Event.properties["error_type"].astext == etype
                ]
                top_screens = await event_crud.count_by_property(
                    self.db, "screen", screen_conds, limit=ERROR_TOP_SCREENS
                )
                top_msgs = await event_crud.count_by_property(
                    self.db, "error_message", screen_conds, limit=ERROR_TOP_MESSAGES
                )
                error_types.append(
                    {
                        "type": etype,
                        "count": cnt,
                        "top_screens": top_screens,
                        "top_messages": top_msgs,
                    }
                )

            auth_conditions = conditions + [Event.name == "authentication_attempt"]
            auth_failures_raw = await event_crud.count_by_property(
                self.db, "auth_method", auth_conditions
            )
            auth_failures = [
                (f"{m}:{r}", c)
                for m, c in auth_failures_raw
                for r, _ in [("failed", c)]
            ]

            error_timeline_raw = await event_crud.hourly_counts(
                self.db, error_conditions
            )
            error_timeline = [
                {"hour": row.hour.isoformat(), "count": row.cnt}
                for row in error_timeline_raw
            ]

            total_errors = sum(e["count"] for e in error_types) + len(auth_failures)
            return {
                "error_types": error_types,
                "auth_failures": auth_failures,
                "error_timeline": error_timeline,
                "total_errors": total_errors,
            }

    # ── Performance Analytics ─────────────────────────────────────────────

    async def get_performance_analytics(
        self, app_id: str | None = None, hours: int = 24
    ) -> dict[str, Any]:
        with create_service_span(
            "AnalyticsCoreService", "get_performance_analytics", app_id=app_id
        ):
            conditions = event_crud.time_conditions(hours, app_id) + [
                Event.name == "performance_measured",
                Event.properties.isnot(None),
            ]

            rows = await event_crud.performance_by_operation(self.db, conditions)
            if not rows:
                return {
                    "operations": [],
                    "performance_summary": {},
                    "total_measurements": 0,
                    "insights": [],
                }

            global_result = await event_crud.performance_global(self.db, conditions)
            gs = {
                k: int(getattr(global_result, k) or 0)
                for k in ["min", "max", "mean", "p50", "p75", "p90", "p95", "p99"]
            }
            gs["median"] = gs["p50"]

            operations = []
            fast_ops, slow_ops, attention_ops = [], [], []
            for row in rows:
                sr = round((row.success_count / row.total_count) * 100, 1)
                median, p95 = int(row.p50 or 0), int(row.p95 or 0)
                is_fast = median <= gs["p50"]
                is_slow = p95 > gs["p95"]
                needs_attention = sr < 95 or is_slow

                dist = {}
                if median <= gs["p50"]:
                    dist = {
                        "excellent": {"count": 1, "percentage": 70.0},
                        "good": {"count": 1, "percentage": 20.0},
                        "average": {"count": 1, "percentage": 10.0},
                        "slow": {"count": 0, "percentage": 0},
                        "very_slow": {"count": 0, "percentage": 0},
                    }
                elif median <= gs["p75"]:
                    dist = {
                        "excellent": {"count": 1, "percentage": 20.0},
                        "good": {"count": 1, "percentage": 50.0},
                        "average": {"count": 1, "percentage": 30.0},
                        "slow": {"count": 0, "percentage": 0},
                        "very_slow": {"count": 0, "percentage": 0},
                    }
                elif median <= gs["p90"]:
                    dist = {
                        "excellent": {"count": 0, "percentage": 0},
                        "good": {"count": 1, "percentage": 20.0},
                        "average": {"count": 1, "percentage": 50.0},
                        "slow": {"count": 1, "percentage": 30.0},
                        "very_slow": {"count": 0, "percentage": 0},
                    }
                else:
                    dist = {
                        "excellent": {"count": 0, "percentage": 0},
                        "good": {"count": 0, "percentage": 0},
                        "average": {"count": 1, "percentage": 20.0},
                        "slow": {"count": 1, "percentage": 40.0},
                        "very_slow": {"count": 1, "percentage": 40.0},
                    }

                op = {
                    "operation": row.operation,
                    "total_count": row.total_count,
                    "success_rate": sr,
                    "min_ms": int(row.min_ms or 0),
                    "max_ms": int(row.max_ms or 0),
                    "mean_ms": int(row.mean_ms or 0),
                    "median_ms": median,
                    "p95_ms": p95,
                    "performance_distribution": dist,
                    "is_fast_operation": is_fast,
                    "is_slow_operation": is_slow,
                    "needs_attention": needs_attention,
                }
                operations.append(op)
                if is_fast:
                    fast_ops.append(op)
                if is_slow:
                    slow_ops.append(op)
                if needs_attention:
                    attention_ops.append(op)

            insights = []
            if fast_ops:
                insights.append(
                    {
                        "type": "positive",
                        "title": f"{len(fast_ops)} operations performing excellently",
                        "description": f"'{fast_ops[0]['operation']}' median within fastest 50%",
                    }
                )
            if slow_ops:
                insights.append(
                    {
                        "type": "warning",
                        "title": f"{len(slow_ops)} operations consistently slow",
                        "description": f"'{slow_ops[0]['operation']}' p95 exceeds global threshold",
                    }
                )
            if attention_ops:
                insights.append(
                    {
                        "type": "attention",
                        "title": f"{len(attention_ops)} operations need attention",
                        "description": "Low success rates or poor performance",
                    }
                )

            thresholds = {
                "excellent": f"≤ {gs['p50']}ms",
                "good": f"{gs['p50'] + 1}-{gs['p75']}ms",
                "average": f"{gs['p75'] + 1}-{gs['p90']}ms",
                "slow": f"{gs['p90'] + 1}-{gs['p95']}ms",
                "very_slow": f"> {gs['p95']}ms",
            }

            return {
                "operations": operations,
                "total_measurements": int(global_result.total),
                "performance_summary": {
                    "global_stats": gs,
                    "performance_thresholds": thresholds,
                    "total_measurements": int(global_result.total),
                },
                "insights": insights,
            }

    # ── User Journey ──────────────────────────────────────────────────────

    async def get_user_journey_analytics(
        self, app_id: str | None = None, hours: int = 24
    ) -> dict[str, Any]:
        with create_service_span(
            "AnalyticsCoreService", "get_user_journey_analytics", app_id=app_id
        ):
            conditions = event_crud.time_conditions(hours, app_id)

            screen_conds = conditions + [Event.name == "screen_viewed"]
            popular_screens = await event_crud.count_by_property(
                self.db, "screen", screen_conds, limit=10
            )

            button_conds = conditions + [
                Event.name.in_(["button_tap", "button_tapped"])
            ]
            # Get button flows as screen→button
            button_flows = await event_crud.get_button_flows(self.db, button_conds)

            launch_conds = conditions + [Event.name == "app_launched"]
            launch_patterns = await event_crud.count_by_property(
                self.db, "launch_type", launch_conds
            )

            # Screen details with features
            screen_details = {}
            for screen_name, visits in popular_screens:
                feat_conds = screen_conds + [
                    Event.properties["screen"].astext == screen_name
                ]
                feats = await event_crud.count_by_property(
                    self.db, "feature", feat_conds
                )
                screen_details[screen_name] = {
                    "visits": visits,
                    "features": dict(feats),
                }

            journey_conds = conditions + [
                Event.name.in_(
                    ["screen_viewed", "button_tap", "button_tapped", "app_launched"]
                )
            ]
            total = await event_crud.count(self.db, journey_conds)

            # Screen transition flow for Sankey diagram
            transitions = await event_crud.get_screen_transitions(
                self.db, hours=hours, app_id=app_id
            )
            entry_points = await event_crud.get_screen_entry_points(
                self.db, hours=hours, app_id=app_id
            )

            # Build Sankey — assign each screen a fixed column, forward links for Sankey,
            # back-navigations tracked separately so the user can see them
            screen_col = {}
            for screen, _ in entry_points:
                screen_col[screen] = 1

            for _ in range(10):
                for from_s, to_s, _ in transitions:
                    if from_s in screen_col and to_s not in screen_col:
                        screen_col[to_s] = screen_col[from_s] + 1

            sankey_nodes = {"App Start"}
            sankey_links = []
            back_navigations = []

            for screen, count in entry_points:
                sankey_nodes.add(screen)
                sankey_links.append(
                    {"source": "App Start", "target": screen, "value": count}
                )

            for from_s, to_s, count in transitions:
                if from_s == to_s:
                    continue
                from_col = screen_col.get(from_s, 1)
                to_col = screen_col.get(to_s, 2)
                if to_col <= from_col:
                    back_navigations.append(
                        {"from": from_s, "to": to_s, "count": count}
                    )
                else:
                    sankey_nodes.add(from_s)
                    sankey_nodes.add(to_s)
                    sankey_links.append(
                        {"source": from_s, "target": to_s, "value": count}
                    )

            sankey_data = {
                "nodes": [{"name": n} for n in sorted(sankey_nodes)],
                "links": sankey_links,
            }

            # Transition heatmap — all screen→screen pairs (forward + backward)
            all_screens_set = set()
            for from_s, to_s, _ in transitions:
                all_screens_set.add(from_s)
                all_screens_set.add(to_s)
            heatmap_screens = sorted(all_screens_set)
            heatmap_data = []
            for from_s, to_s, count in transitions:
                if from_s == to_s:
                    continue
                x = heatmap_screens.index(to_s)
                y = heatmap_screens.index(from_s)
                heatmap_data.append([x, y, count])

            transition_heatmap = {
                "screens": heatmap_screens,
                "data": heatmap_data,
            }

            # Dwell times, exit screens, session metrics
            dwell_times = await event_crud.get_screen_dwell_times(
                self.db, hours=hours, app_id=app_id
            )
            exit_screens = await event_crud.get_exit_screens(
                self.db, hours=hours, app_id=app_id
            )
            session_metrics = await event_crud.get_session_metrics(
                self.db, hours=hours, app_id=app_id
            )

            dwell_list = [
                {
                    "screen": r.screen,
                    "visits": r.visit_count,
                    "avg_seconds": float(r.avg_seconds or 0),
                    "median_seconds": float(r.median_seconds or 0),
                    "max_seconds": int(r.max_seconds or 0),
                }
                for r in dwell_times
            ]

            return {
                "popular_screens": popular_screens,
                "button_flows": button_flows,
                "launch_patterns": launch_patterns,
                "screen_details": screen_details,
                "total_interactions": total,
                "sankey_data": sankey_data,
                "transition_heatmap": transition_heatmap,
                "transitions": transitions,
                "entry_points": entry_points,
                "dwell_times": dwell_list,
                "exit_screens": exit_screens,
                "session_metrics": session_metrics,
            }

    # ── Feedback ──────────────────────────────────────────────────────────

    async def get_feedback_analytics(
        self, app_id: str | None = None, hours: int = 24
    ) -> dict[str, Any]:
        with create_service_span(
            "AnalyticsCoreService", "get_feedback_analytics", app_id=app_id
        ):
            conditions = event_crud.time_conditions(hours, app_id) + [
                Event.name == "feedback_submitted",
                Event.properties.isnot(None),
            ]
            total = await event_crud.count(self.db, conditions)
            if total == 0:
                return {
                    "total_feedback": 0,
                    "unique_users": 0,
                    "with_email": 0,
                    "email_percentage": 0,
                    "avg_length": 0,
                    "premium_count": 0,
                    "premium_percentage": 0,
                    "categories": [],
                    "top_features": [],
                    "top_screens": [],
                    "recent_feedback": [],
                }

            unique_users = await event_crud.count_unique_users(self.db, conditions)
            categories = await event_crud.count_by_property(
                self.db, "feedback_category", conditions
            )
            top_features = await event_crud.count_by_property(
                self.db, "feature", conditions, limit=10
            )
            top_screens = await event_crud.count_by_property(
                self.db, "screen", conditions, limit=10
            )

            recent_feedback = await event_crud.get_recent(self.db, conditions, limit=10)

            cat_list = [
                {"name": c, "count": n, "percentage": round(n / total * 100, 1)}
                for c, n in categories
            ]
            feat_list = [{"name": f, "count": n} for f, n in top_features]
            screen_list = [{"name": s, "count": n} for s, n in top_screens]

            return {
                "total_feedback": total,
                "unique_users": unique_users,
                "with_email": 0,
                "email_percentage": 0,
                "avg_length": 0,
                "premium_count": 0,
                "premium_percentage": 0,
                "categories": cat_list,
                "top_features": feat_list,
                "top_screens": screen_list,
                "recent_feedback": [
                    EventInDB.model_validate(e) for e in recent_feedback
                ],
            }

    # ── User Profile ──────────────────────────────────────────────────────

    async def get_user_profile(self, user_id: str) -> dict[str, Any]:
        with create_service_span(
            "AnalyticsCoreService", "get_user_profile", user_id=user_id
        ):
            summary = await event_crud.get_user_summary(self.db, user_id)
            sessions = await event_crud.get_user_sessions(
                self.db, user_id, limit=PROFILE_SESSIONS_SHOWN
            )
            devices = await event_crud.get_user_devices(self.db, user_id)
            by_user = [Event.user_id == user_id]
            errors = await event_crud.get_by_names(
                self.db, ERROR_EVENT_NAMES, by_user, limit=PROFILE_ERRORS_SHOWN
            )
            total_errors = await event_crud.count(
                self.db, by_user + [Event.name.in_(ERROR_EVENT_NAMES)]
            )
            screens = await event_crud.count_by_property(
                self.db,
                "screen",
                by_user + [Event.name == "screen_viewed"],
                limit=PROFILE_SCREENS_SHOWN,
            )

            # Format sessions
            session_list = []
            for s in sessions:
                duration = (
                    (s.last_event - s.first_event).total_seconds()
                    if s.first_event and s.last_event
                    else 0
                )
                session_list.append(
                    {
                        "session_id": s.session_id,
                        "event_count": s.event_count,
                        "first_event": s.first_event,
                        "last_event": s.last_event,
                        "duration_seconds": int(duration),
                        "event_types": s.event_types or [],
                    }
                )

            # Format devices
            device_list = []
            for d in devices:
                device_list.append(
                    {
                        "device_id": d.device_id[:12] + "..."
                        if d.device_id
                        else "Unknown",
                        "device_model": d.device_model or "Unknown",
                        "os_version": d.os_version or "Unknown",
                        "app_version": d.app_version or "Unknown",
                        "platform": d.platform or "ios",
                        "event_count": d.event_count,
                        "last_seen": d.last_seen,
                    }
                )

            return {
                "user_id": user_id,
                **summary,
                "sessions": session_list,
                "devices": device_list,
                "errors": [EventInDB.model_validate(e) for e in errors],
                "total_errors": total_errors,
                "screens_visited": [screen for screen, _ in screens],
            }

    # ── Session Detail ────────────────────────────────────────────────────

    async def get_session_detail(self, session_id: str) -> dict[str, Any]:
        with create_service_span(
            "AnalyticsCoreService", "get_session_detail", session_id=session_id
        ):
            events = await event_crud.get_events_by_session(self.db, session_id)
            if not events:
                return {"session_id": session_id, "events": [], "duration_seconds": 0}

            first = events[0]
            last = events[-1]
            duration = (last.received_at - first.received_at).total_seconds()

            # Extract flow
            screens = []
            buttons = []
            errors = []
            perf = []
            for e in events:
                if e.name == "screen_viewed" and e.properties:
                    screens.append(e.properties.get("screen", "unknown"))
                elif e.name in ("button_tap", "button_tapped") and e.properties:
                    buttons.append(
                        {
                            "button": e.properties.get("button", "unknown"),
                            "screen": e.properties.get("screen", "unknown"),
                        }
                    )
                elif e.name == "error_occurred":
                    errors.append(e)
                elif e.name == "performance_measured" and e.properties:
                    perf.append(
                        {
                            "operation": e.properties.get("operation", "unknown"),
                            "duration_ms": e.properties.get("duration_ms", "0"),
                        }
                    )

            return {
                "session_id": session_id,
                "user_id": first.user_id,
                "device_model": first.device_model,
                "os_version": first.os_version,
                "app_version": first.app_version,
                "platform": first.platform,
                "first_event": first.received_at,
                "last_event": last.received_at,
                "duration_seconds": int(duration),
                "total_events": len(events),
                "events": events,
                "screen_flow": screens,
                "button_taps": buttons,
                "errors": errors,
                "performance": perf,
            }

    # ── Metadata Explorer ─────────────────────────────────────────────────

    async def get_metadata_analysis(
        self, app_id: str | None = None, hours: int = 24, event_name: str | None = None
    ) -> dict[str, Any]:
        """Comprehensive metadata/properties analysis — key discovery + event patterns."""
        with create_service_span(
            "AnalyticsCoreService", "get_metadata_analysis", app_id=app_id
        ):
            conditions = event_crud.time_conditions(hours, app_id, event_name)
            events = await event_crud.get_events_with_properties(
                self.db, conditions, limit=500
            )

            all_keys = set()
            key_counts: dict[str, int] = {}
            key_types: dict[str, list[str]] = {}
            key_examples: dict[str, list[Any]] = {}
            event_patterns: dict[str, dict[str, Any]] = {}

            for e in events:
                props = e.properties
                if not props:
                    continue

                ename = e.name
                if ename not in event_patterns:
                    event_patterns[ename] = {
                        "count": 0,
                        "json_keys": [],
                        "examples": [],
                    }
                event_patterns[ename]["count"] += 1

                for key in props:
                    if key not in event_patterns[ename]["json_keys"]:
                        event_patterns[ename]["json_keys"].append(key)
                if len(event_patterns[ename]["examples"]) < 3:
                    event_patterns[ename]["examples"].append(props)

                for key, value in props.items():
                    all_keys.add(key)
                    key_counts[key] = key_counts.get(key, 0) + 1
                    vtype = type(value).__name__
                    if key not in key_types:
                        key_types[key] = []
                    if vtype not in key_types[key]:
                        key_types[key].append(vtype)
                    if key not in key_examples:
                        key_examples[key] = []
                    if len(key_examples[key]) < 10:
                        key_examples[key].append(value)

            for ename in event_patterns:
                event_patterns[ename]["json_keys"].sort()

            return {
                "total_events": len(events),
                "all_keys": sorted(all_keys),
                "key_counts": key_counts,
                "key_types": key_types,
                "key_examples": key_examples,
                "event_patterns": event_patterns,
                "most_common_keys": sorted(
                    key_counts.items(), key=lambda x: x[1], reverse=True
                )[:20],
            }

    async def get_key_deep_dive(
        self, key_name: str, app_id: str | None = None, hours: int = 24
    ) -> dict[str, Any]:
        """Deep dive into a specific properties key — values, distribution, timeline."""
        with create_service_span(
            "AnalyticsCoreService", "get_key_deep_dive", app_id=app_id
        ):
            # Counts and distributions are aggregates over EVERY matching event; only the samples
            # list is a bounded read (it was all computed from the newest 200 events, so a key
            # seen 10,000 times read "200 occurrences").
            conditions = event_crud.time_conditions(hours, app_id)
            total, unique_values, event_types = await event_crud.property_key_summary(
                self.db, key_name, conditions
            )
            top_values = await event_crud.count_by_property(
                self.db, key_name, conditions, limit=KEY_TOP_VALUES
            )
            value_by_event: dict[str, dict[str, int]] = {}
            for name, value, count in await event_crud.property_values_by_name(
                self.db, key_name, conditions, limit=KEY_VALUES_BY_EVENT
            ):
                value_by_event.setdefault(name, {})[value] = count
            recent = await event_crud.get_events_with_property_key(
                self.db, key_name, conditions, limit=KEY_SAMPLES
            )
            samples = [
                {
                    "event_name": e.name,
                    "key_value": _as_text((e.properties or {})[key_name]),
                    "received_at": e.received_at,
                }
                for e in recent
            ]
            return {
                "key_name": key_name,
                "total_occurrences": total,
                "unique_values": unique_values,
                "event_type_count": event_types,
                "top_values": top_values,
                "value_by_event_type": value_by_event,
                "samples": samples,
            }
