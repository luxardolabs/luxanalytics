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
