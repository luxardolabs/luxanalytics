"""Every dashboard page and HTMX partial renders for a logged-in session (route smoke).

Guards the view-seam relocation: each route asks DashboardViewService for its context, and a
context key a template needs that the view stopped providing shows up here as a 500.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

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

    # Every content partial also under the "All" time pill (hours=0): the overview divided by
    # it, and "All" with no app built an empty and_() that SQLAlchemy is removing.
    all_time = [
        f"{p}{'&' if '?' in p else '?'}hours=0" for p in PAGES if "/content" in p
    ]
    failures = {}
    for path in [*PAGES, *all_time]:
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


@pytest.mark.db
async def test_top_event_links_encode_the_event_name(
    client: AsyncClient, db: AsyncSession, sample_app: object
) -> None:
    # The link used to interpolate the raw name into the query: `a&b c` broke it at the `&`.
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.models.event_model import Event

    now = datetime.now(UTC)
    db.add(
        Event(
            id=uuid4(), app_id="test_app", name="a&b c", timestamp=now, received_at=now
        )
    )
    await db.flush()

    client.base_url = "https://test"
    await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": "198.51.100.93"},
        follow_redirects=False,
    )
    response = await client.get("/dashboard/overview/content?app_id=test_app")
    assert response.status_code == 200
    assert "event_name=a%26b+c" in response.text


@pytest.mark.db
async def test_the_events_list_pages_through_the_full_page_with_its_filters(
    client: AsyncClient, db: AsyncSession, sample_app: object
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.models.event_model import Event

    now = datetime.now(UTC)
    for _ in range(60):  # more than one page (50)
        db.add(
            Event(
                id=uuid4(),
                app_id="test_app",
                name="tick",
                timestamp=now,
                received_at=now,
            )
        )
    await db.flush()

    client.base_url = "https://test"
    await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": "198.51.100.94"},
        follow_redirects=False,
    )
    response = await client.get(
        "/dashboard/events/content?app_id=test_app&event_name=tick"
    )
    assert response.status_code == 200
    # Page links go to the full events PAGE (not the fragment), with the filters carried along.
    assert (
        'href="/dashboard/events?hours=24&amp;app_id=test_app&amp;event_name=tick&amp;page=2"'
        in response.text
    )
    second = await client.get(
        "/dashboard/events?hours=24&app_id=test_app&event_name=tick&page=2"
    )
    assert second.status_code == 200
