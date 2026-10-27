"""The performance page survives any duration_ms an SDK sends.

Metadata values are strings, and the queries cast `duration_ms` straight to INTEGER, so one event
with "12.5" (a Swift Double) made the page a 500 for that app and for all apps, for as long as the
event is in range: events are never deleted (adversarial audit 2026-10-27, LUXANALYTI-98).
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.models.app_model import App
from tests.test_ingest_http import PROJECT, _dsn_auth

pytestmark = pytest.mark.db


async def _login(client: AsyncClient) -> None:
    client.base_url = "https://test"  # the session cookie is https_only
    login = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": "198.51.100.98"},
        follow_redirects=False,
    )
    assert login.status_code == 303


async def _measured(client: AsyncClient, duration_ms: str) -> None:
    body = {
        "name": "performance_measured",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {"operation": "load", "duration_ms": duration_ms},
    }
    r = await client.post(f"/api/v1/events/{PROJECT}", json=body, headers=_dsn_auth())
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("bad", ["12.5", "abc", "99999999999", "-3", ""])
async def test_one_odd_duration_does_not_break_the_page(
    client: AsyncClient, sample_app: App, bad: str
) -> None:
    await _measured(client, "150")
    await _measured(client, bad)
    await _login(client)

    scoped = await client.get(
        f"/dashboard/performance/content?app_id={sample_app.app_id}"
    )
    all_apps = await client.get("/dashboard/performance/content?hours=0")

    assert scoped.status_code == 200
    assert all_apps.status_code == 200
    # The valid measurement is still the one the page reports.
    assert "150" in scoped.text


async def test_a_fractional_duration_is_measured(
    client: AsyncClient, sample_app: App
) -> None:
    """A Swift Double is a real measurement, not a bad value: it counts (the page shows whole ms)."""
    await _measured(client, "12.5")
    await _login(client)

    page = await client.get(
        f"/dashboard/performance/content?app_id={sample_app.app_id}"
    )

    assert page.status_code == 200
    assert "12ms" in page.text.replace(" ", "")
