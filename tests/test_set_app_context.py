"""The nav app selector stores any app_id in its cookie, whatever characters it has: the cookie
carries the id percent-encoded and the dashboard reads it back."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_model import Event

HX = {"HX-Current-URL": "https://test/dashboard/events"}
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _event(app_id: str, name: str) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "app_id": app_id,
        "name": name,
        "timestamp": NOW,
        "received_at": datetime.now(UTC),
        "properties": None,
    }


@pytest.mark.db
@pytest.mark.parametrize("app_id", ["café☃", "a\r\nb", "a\tb", "a;b=c", 'a"b', "a b"])
async def test_an_app_id_with_any_character_can_be_selected(
    dashboard_client: AsyncClient, db: AsyncSession, app_id: str
) -> None:
    await db.execute(
        insert(Event),
        [_event(app_id, "cookie_probe"), _event("other_app", "other_probe")],
    )
    await db.flush()

    response = await dashboard_client.post(
        "/set-app-context", data={"app_id": app_id}, headers=HX
    )
    assert response.status_code == 204
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.isascii()
    assert "\n" not in set_cookie

    # The cookie names the app on the next request: only its events are listed.
    listing = await dashboard_client.get("/dashboard/events/content")
    assert listing.status_code == 200
    assert "Cookie Probe" in listing.text
    assert "Other Probe" not in listing.text


@pytest.mark.db
async def test_the_dropdown_offers_the_selected_app(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    await db.execute(insert(Event), [_event("café☃", "cookie_probe")])
    await db.flush()
    await dashboard_client.post(
        "/set-app-context", data={"app_id": "café☃"}, headers=HX
    )
    dropdown = await dashboard_client.get("/dashboard/apps-dropdown")
    assert dropdown.status_code == 200


@pytest.mark.db
async def test_a_nul_byte_in_the_selection_is_a_422(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.post(
        "/set-app-context", data={"app_id": "a\x00b"}, headers=HX
    )
    assert response.status_code == 422


@pytest.mark.db
@pytest.mark.parametrize(
    "current_url",
    ["http://[bad/", "http://[::1", "https://[x]:abc/dashboard/events", ""],
)
async def test_an_unparseable_current_url_goes_back_to_the_overview(
    dashboard_client: AsyncClient, current_url: str
) -> None:
    response = await dashboard_client.post(
        "/set-app-context",
        data={"app_id": "x"},
        headers={"HX-Current-URL": current_url},
    )
    assert response.status_code == 204
    assert response.headers["hx-redirect"] == "/dashboard/overview"


@pytest.mark.db
async def test_an_empty_selection_clears_the_cookie(
    dashboard_client: AsyncClient,
) -> None:
    await dashboard_client.post("/set-app-context", data={"app_id": "x"}, headers=HX)
    response = await dashboard_client.post(
        "/set-app-context", data={"app_id": ""}, headers=HX
    )
    assert response.status_code == 204
    assert "analytics_app_id=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.db
async def test_a_cookie_holding_an_encoded_nul_byte_selects_no_app(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    await db.execute(insert(Event), [_event("some_app", "cookie_probe")])
    await db.flush()
    # The Cookie header is written out: the jar would not send a cookie set for another domain.
    session = "; ".join(f"{c.name}={c.value}" for c in dashboard_client.cookies.jar)
    headers = {"Cookie": f"{session}; analytics_app_id=a%00b"}
    for path in ("/dashboard/overview", "/dashboard/events", "/dashboard/devices"):
        assert (await dashboard_client.get(path, headers=headers)).status_code == 200

    # No app is selected, so the list is every app's.
    listing = await dashboard_client.get("/dashboard/events/content", headers=headers)
    assert "Cookie Probe" in listing.text
