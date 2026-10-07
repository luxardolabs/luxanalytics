"""Tests for dashboard query modules against seeded data."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.core.analytics_core_service import AnalyticsCoreService


@pytest.mark.asyncio
async def test_stats_overview(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    svc = AnalyticsCoreService(db)
    stats = await svc.get_stats_overview(app_id="test_app", hours=24)

    assert stats["total_events"] == 4
    assert stats["unique_users"] == 2
    assert len(stats["top_events"]) > 0
    assert stats["top_events"][0]["name"] in {
        "screen_view",
        "button_tapped",
        "error_occurred",
        "performance_measured",
    }


@pytest.mark.asyncio
async def test_stats_overview_all_apps(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    svc = AnalyticsCoreService(db)
    stats = await svc.get_stats_overview(hours=24)

    assert stats["total_events"] >= 4


@pytest.mark.asyncio
async def test_device_analytics(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    svc = AnalyticsCoreService(db)
    analytics = await svc.get_device_analytics(app_id="test_app", hours=24)

    assert analytics["unique_devices"] == 2
    assert analytics["total_events"] == 4

    # Device models should come from promoted columns
    model_names = [m[0] for m in analytics["device_models"]]
    assert "iPhone17,1" in model_names
    assert "iPhone16,1" in model_names

    # OS versions
    os_names = [v[0] for v in analytics["system_versions"]]
    assert "18.3" in os_names
    assert "17.5" in os_names


@pytest.mark.asyncio
async def test_error_analytics(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    svc = AnalyticsCoreService(db)
    analytics = await svc.get_error_analytics(app_id="test_app", hours=24)

    assert analytics["total_errors"] >= 1
    assert len(analytics["error_types"]) >= 1

    error_type_names = [e["type"] for e in analytics["error_types"]]
    assert "network" in error_type_names


@pytest.mark.asyncio
async def test_performance_analytics(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    svc = AnalyticsCoreService(db)
    analytics = await svc.get_performance_analytics(app_id="test_app", hours=24)

    assert analytics["total_measurements"] >= 1
    assert len(analytics["operations"]) >= 1

    op_names = [o["operation"] for o in analytics["operations"]]
    assert "api_call" in op_names

    api_op = next(o for o in analytics["operations"] if o["operation"] == "api_call")
    assert api_op["median_ms"] == 150
    assert api_op["success_rate"] == 100.0


@pytest.mark.asyncio
async def test_filtered_events(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    svc = AnalyticsCoreService(db)

    # Filter by event name: one window of matches and the total. The seed holds exactly one
    # screen_view for test_app, so assert exactly that.
    events, total = await svc.get_filtered_events(
        skip=0, limit=50, app_id="test_app", event_name="screen_view", hours=24
    )
    assert [e.name for e in events] == ["screen_view"]
    assert total == 1


@pytest.mark.asyncio
async def test_timeline_data(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    svc = AnalyticsCoreService(db)
    timeline = await svc.get_timeline_data(app_id="test_app", hours=24)

    assert len(timeline) > 0
    # Should have at least one non-zero bucket
    assert any(t["count"] > 0 for t in timeline)


@pytest.mark.asyncio
async def test_search_events(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    svc = AnalyticsCoreService(db)
    results = await svc.search_events(query="screen", search_type="event_name")

    assert len(results) >= 1
    assert all("screen" in e.name.lower() for e in results)


@pytest.mark.asyncio
async def test_feature_analytics(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    svc = AnalyticsCoreService(db)
    analytics = await svc.get_feature_analytics(app_id="test_app", hours=24)

    assert analytics["total_events"] >= 1
    # Our sample data has screen: "home" and screen: "settings"
    screen_names = [s["name"] for s in analytics["screens"]]
    assert len(screen_names) >= 1


@pytest.mark.asyncio
async def test_user_profile_errors_and_screens_span_the_whole_history(
    db: AsyncSession,
) -> None:
    """An old error and an old screen still show behind many newer events (they were read
    from the newest 50 events only, so a busy user's profile showed "Errors (0)")."""
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from sqlalchemy import insert

    from app.models.event_model import Event

    now = datetime.now(UTC)

    def row(name: str, age: timedelta, properties: dict[str, str]) -> dict[str, object]:
        return {
            "id": uuid4(),
            "app_id": "test_app",
            "name": name,
            "timestamp": now - age,
            "received_at": now - age,
            "user_id": "busy_user",
            "session_id": "s1",
            "properties": properties,
        }

    old = timedelta(days=3)
    rows = [row("error_occurred", old, {"error_type": "network"})]
    rows.append(row("screen_viewed", old, {"screen": "settings"}))
    rows += [row("button_tapped", timedelta(minutes=i), {}) for i in range(60)]
    await db.execute(insert(Event), rows)

    profile = await AnalyticsCoreService(db).get_user_profile("busy_user")

    assert [e.name for e in profile["errors"]] == ["error_occurred"]
    assert profile["total_errors"] == 1
    assert profile["screens_visited"] == ["settings"]


@pytest.mark.asyncio
async def test_key_deep_dive_counts_every_event_not_a_sample(db: AsyncSession) -> None:
    """Occurrences, unique values and the distribution are aggregates over every matching
    event (they were computed from the newest 200, so this key read "200 occurrences")."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import insert

    from app.models.event_model import Event
    from app.services.core.analytics_core_service import KEY_SAMPLES

    now = datetime.now(UTC)
    rows = [
        {
            "id": uuid4(),
            "app_id": "test_app",
            "name": "purchase" if i % 3 else "trial_started",
            "timestamp": now,
            "received_at": now,
            "properties": {"plan": "pro" if i % 2 else "basic"},
        }
        for i in range(210)
    ]
    await db.execute(insert(Event), rows)

    dive = await AnalyticsCoreService(db).get_key_deep_dive("plan", app_id="test_app")

    assert dive["total_occurrences"] == 210
    assert dive["unique_values"] == 2
    assert dive["event_type_count"] == 2
    assert dict(dive["top_values"]) == {"pro": 105, "basic": 105}
    assert sum(sum(v.values()) for v in dive["value_by_event_type"].values()) == 210
    assert len(dive["samples"]) == KEY_SAMPLES
