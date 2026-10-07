"""Every dashboard page and HTMX partial renders for a logged-in session (route smoke).

Guards the view-seam relocation: each route asks DashboardViewService for its context, and a
context key a template needs that the view stopped providing shows up here as a 500.
"""

import pytest
from httpx import AsyncClient

from app.core.config import settings

PAGES = [
    "/dashboard/apps-dropdown",
    "/dashboard/overview",
    "/dashboard/overview/content",
    "/dashboard/overview/timeline",
    "/dashboard/overview/event-types",
    "/dashboard/overview/top-screens",
    "/dashboard/events",
    "/dashboard/events/content",
    "/dashboard/events/content?search_q=screen",
    "/dashboard/devices",
    "/dashboard/devices/content",
    "/dashboard/errors",
    "/dashboard/errors/content",
    "/dashboard/performance",
    "/dashboard/performance/content",
    "/dashboard/features",
    "/dashboard/features/content",
    "/dashboard/journey",
    "/dashboard/journey/content",
    "/dashboard/journey?app_id=test_app",
    "/dashboard/journey/content?app_id=test_app",
    "/dashboard/feedback",
    "/dashboard/feedback/content",
    "/dashboard/explorer",
    "/dashboard/explorer/content",
    "/dashboard/explorer/key/screen",
    "/dashboard/user/user_1",
    "/dashboard/session/session_1",
]


@pytest.mark.db
async def test_every_dashboard_page_renders(
    client: AsyncClient, sample_app: object, sample_events: list[dict[str, object]]
) -> None:
    client.base_url = "https://test"  # the session cookie is https_only
    login = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": "198.51.100.90"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    failures = {}
    for path in PAGES:
        response = await client.get(path)
        if response.status_code != 200:
            failures[path] = response.status_code
    assert not failures, failures


@pytest.mark.db
async def test_a_missing_event_is_a_404(client: AsyncClient) -> None:
    client.base_url = "https://test"
    await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": "198.51.100.91"},
        follow_redirects=False,
    )
    response = await client.get(
        "/dashboard/events/detail/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404


@pytest.mark.db
async def test_every_dashboard_page_renders_with_no_data(client: AsyncClient) -> None:
    # No app, no events: every value the templates read must still be PROVIDED by the view
    # (StrictUndefined: a missing one raises), so an empty install shows its empty states.
    client.base_url = "https://test"
    login = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": "198.51.100.92"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    failures = {}
    for path in [
        *PAGES,
        "/dashboard/user/nobody",
        "/dashboard/session/no-such-session",
    ]:
        response = await client.get(path)
        if response.status_code != 200:
            failures[path] = response.status_code
    assert not failures, failures
