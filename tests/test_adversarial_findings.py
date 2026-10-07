"""Defects the adversarial pass (/adversarial) reproduced against a5ee013..HEAD, kept as regressions.

The *_control tests are the same request on a path that always worked, so a failure here is the
defect, not the harness."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.event_model import Event
from app.services.core.analytics_core_service import AnalyticsCoreService


async def _login(client: AsyncClient, ip: str) -> None:
    client.base_url = "https://test"
    r = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": ip},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _ev(name: str, **kw: Any) -> dict[str, Any]:
    now = kw.pop("at", datetime.now(UTC))
    row = {
        "id": str(uuid4()),
        "app_id": "adv_app",
        "name": name,
        "timestamp": now,
        "received_at": now,
        "user_id": "adv_user",
        "session_id": "adv_session",
        "properties": None,
        "event_metadata": None,
    }
    row.update(kw)
    return row


async def _seed(db: AsyncSession, rows: list[dict[str, Any]]) -> None:
    await db.execute(insert(Event), rows)
    await db.flush()


# ── hours=0 ("All") now renders, but several queries treat 0 as "the last 0 hours" ──────────


@pytest.mark.db
async def test_events_list_all_time_shows_events_control(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed(db, [_ev("adv_marker_evt", at=datetime.now(UTC) - timedelta(hours=1))])
    await _login(client, "198.51.100.201")
    r = await client.get("/dashboard/events/content?hours=24&app_id=adv_app")
    assert r.status_code == 200
    assert "Adv Marker Evt" in r.text


@pytest.mark.db
async def test_events_list_all_time_shows_events(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The 'All' pill (hours=0) on the events page: get_filtered uses since=now-0h=now."""
    await _seed(db, [_ev("adv_marker_evt", at=datetime.now(UTC) - timedelta(hours=1))])
    await _login(client, "198.51.100.202")
    r = await client.get("/dashboard/events/content?hours=0&app_id=adv_app")
    assert r.status_code == 200
    assert "Adv Marker Evt" in r.text, "All-time events list is empty"


def _journey_rows() -> list[dict[str, Any]]:
    t0 = datetime.now(UTC) - timedelta(hours=1)
    return [
        _ev("screen_viewed", at=t0, properties={"screen": "home"}),
        _ev(
            "screen_viewed",
            at=t0 + timedelta(seconds=5),
            properties={"screen": "settings"},
        ),
    ]


@pytest.mark.db
async def test_journey_transitions_24h_control(db: AsyncSession) -> None:
    await _seed(db, _journey_rows())
    data = await AnalyticsCoreService(db).get_user_journey_analytics(
        "adv_app", hours=24
    )
    assert data["transitions"], data["transitions"]
    assert data["entry_points"]


@pytest.mark.db
async def test_journey_transitions_all_time(db: AsyncSession) -> None:
    """hours=0 used to 500 (Interactions/Hour); now it renders, but the raw-SQL journey queries
    use make_interval(hours => 0), i.e. 'since NOW()', so Sankey/heatmap/entry/exit/dwell/session
    metrics are empty while popular_screens (time_conditions) are populated."""
    await _seed(db, _journey_rows())
    data = await AnalyticsCoreService(db).get_user_journey_analytics("adv_app", hours=0)
    assert data["popular_screens"]  # time_conditions treats 0 as all time
    assert data["entry_points"], "All-time journey: entry points empty"
    assert data["transitions"], "All-time journey: transitions empty"


# ── user profile: screens visited from SQL now include JSON-null / empty screens ────────────


@pytest.mark.db
async def test_profile_screens_visited_skips_null_and_empty(db: AsyncSession) -> None:
    await _seed(
        db,
        [
            _ev("screen_viewed", properties={"screen": "home"}),
            _ev("screen_viewed", properties={"screen": None}),  # legacy row
            _ev("screen_viewed", properties={"screen": ""}),
        ],
    )
    profile = await AnalyticsCoreService(db).get_user_profile("adv_user")
    # a5ee013 skipped falsy screens (`if screen and ...`)
    assert profile["screens_visited"] == ["home"], profile["screens_visited"]


# ── key deep dive: SQL aggregates vs Python samples over a non-string JSONB value ──────────


@pytest.mark.db
async def test_key_deep_dive_bool_value_consistent(db: AsyncSession) -> None:
    await _seed(db, [_ev("toggle", properties={"enabled": True})])
    data = await AnalyticsCoreService(db).get_key_deep_dive(
        "enabled", "adv_app", hours=24
    )
    top_value = data["top_values"][0][0]
    sample_value = data["samples"][0]["key_value"]
    assert top_value == sample_value, (top_value, sample_value)


@pytest.mark.db
async def test_key_deep_dive_null_value_counts(db: AsyncSession) -> None:
    await _seed(
        db,
        [
            _ev("x", properties={"k": "a"}),
            _ev("x", properties={"k": None}),
        ],
    )
    data = await AnalyticsCoreService(db).get_key_deep_dive("k", "adv_app", hours=24)
    distinct_in_top = len(data["top_values"])
    assert data["unique_values"] == distinct_in_top, (
        data["unique_values"],
        data["top_values"],
        data["value_by_event_type"],
    )


# ── attacker-controlled ids in path params (pre-existing, re-verified) ───────────────────────


@pytest.mark.db
async def test_events_page_survives_slash_in_user_id(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed(db, [_ev("adv_slash", user_id="evil/user")])
    await _login(client, "198.51.100.203")
    r = await client.get("/dashboard/events/content?hours=24&app_id=adv_app")
    assert r.status_code == 200


@pytest.mark.db
async def test_explorer_survives_slash_in_property_key(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed(db, [_ev("adv_slash", properties={"a/b": "v"})])
    await _login(client, "198.51.100.204")
    r = await client.get("/dashboard/explorer/content?hours=24&app_id=adv_app")
    assert r.status_code == 200


@pytest.mark.db
async def test_explorer_key_with_query_char_links_to_itself(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed(db, [_ev("adv_q", properties={"a?b": "v"})])
    await _login(client, "198.51.100.205")
    r = await client.get("/dashboard/explorer/content?hours=24&app_id=adv_app")
    assert r.status_code == 200
    assert "/dashboard/explorer/key/a%3Fb" in r.text, [
        line.strip() for line in r.text.splitlines() if "explorer/key/" in line
    ][:2]


# ── older 500s the sweep found on every-route rendering ─────────────────────────────────────


@pytest.mark.db
async def test_performance_page_survives_operations_without_success_flag(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed(
        db,
        [
            _ev(
                "performance_measured",
                properties={"operation": "sync", "duration_ms": "12"},
            )
        ],
    )
    await _login(client, "198.51.100.206")
    r = await client.get("/dashboard/performance/content?hours=24&app_id=adv_app")
    assert r.status_code == 200


@pytest.mark.db
async def test_event_detail_with_a_malformed_id_is_a_404(client: AsyncClient) -> None:
    await _login(client, "198.51.100.207")
    r = await client.get("/dashboard/events/detail/not-a-uuid")
    assert r.status_code == 404


@pytest.mark.db
async def test_a_slash_user_id_link_opens_that_users_profile(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed(db, [_ev("adv_slash", user_id="evil/user")])
    await _login(client, "198.51.100.208")
    page = await client.get("/dashboard/events/content?hours=24&app_id=adv_app")
    assert 'hx-get="/dashboard/user/evil%2Fuser"' in page.text
    panel = await client.get("/dashboard/user/evil%2Fuser")
    assert panel.status_code == 200
    assert "evil/user" in panel.text
