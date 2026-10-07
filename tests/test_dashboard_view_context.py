"""The dashboard view computes what its templates render (luxarch --playbook template-logic).

The page smoke test proves each partial renders; these prove the values the view hands it.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.main import app
from app.services.views.dashboard_view_service import DashboardViewService


def _view(db: AsyncSession) -> DashboardViewService:
    request = Request({"type": "http", "app": app, "headers": [], "query_string": b""})
    return DashboardViewService(db, request)


@pytest.mark.db
async def test_event_types_pie_is_labelled_and_counted(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    ctx = await _view(db).event_types_context("test_app", 24)
    pie = {slice_["name"]: slice_["value"] for slice_ in ctx["pie_data"]}
    assert pie == {
        "Screen View": 1,
        "Button Tapped": 1,
        "Error Occurred": 1,
        "Performance Measured": 1,
    }


@pytest.mark.db
async def test_timeline_series_line_up(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    ctx = await _view(db).timeline_context("test_app", 24)
    assert len(ctx["timeline_labels"]) == len(ctx["timeline_counts"]) > 0
    assert sum(ctx["timeline_counts"]) == len(sample_events)


@pytest.mark.db
async def test_top_screens_bar_runs_least_busy_first(
    db: AsyncSession, sample_events: list[dict[str, object]]
) -> None:
    ctx = await _view(db).top_screens_context("test_app", 24)
    assert len(ctx["bar_labels"]) == len(ctx["bar_data"]) > 0
    assert ctx["bar_data"] == sorted(ctx["bar_data"])
    assert "Home" in ctx["bar_labels"]  # friendly_name applied in the view
