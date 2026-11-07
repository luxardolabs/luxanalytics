"""A time pill reloads the page for the SAME app and event filter it shows: the values are
URL-encoded in the pill's link, so "&", "#", "+" and "%41" survive the round trip."""

import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient

PAGES = [
    "overview",
    "events",
    "devices",
    "errors",
    "performance",
    "features",
    "journey",
    "feedback",
    "explorer",
]
APP_IDS = ["a&b", "a+b", "a#1", "a%41", "a b", "café", "x=y"]


def _pill_queries(html: str) -> list[dict[str, list[str]]]:
    links = [
        unescape(u)
        for u in re.findall(r'data-hours="\d+".*?hx-get="([^"]*)"', html, re.DOTALL)
    ]
    return [parse_qs(urlsplit(u).query, keep_blank_values=True) for u in links]


@pytest.mark.db
@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("app_id", APP_IDS)
async def test_every_pill_asks_for_the_same_app(
    dashboard_client: AsyncClient, page: str, app_id: str
) -> None:
    response = await dashboard_client.get(
        f"/dashboard/{page}", params={"app_id": app_id}
    )
    assert response.status_code == 200
    queries = _pill_queries(response.text)
    assert len(queries) == 8
    for query in queries:
        assert query["app_id"] == [app_id]


@pytest.mark.db
@pytest.mark.parametrize("event_name", ["a&b", "a#1", "a+b", "100%"])
async def test_every_events_pill_asks_for_the_same_event_name(
    dashboard_client: AsyncClient, event_name: str
) -> None:
    response = await dashboard_client.get(
        "/dashboard/events", params={"app_id": "x", "event_name": event_name}
    )
    assert response.status_code == 200
    queries = _pill_queries(response.text)
    assert len(queries) == 8
    for query in queries:
        assert query["event_name"] == [event_name]
        assert query["app_id"] == ["x"]


@pytest.mark.db
async def test_every_explorer_pill_keeps_the_event_name(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.get(
        "/dashboard/explorer", params={"app_id": "x", "event_name": "a&b"}
    )
    assert response.status_code == 200
    queries = _pill_queries(response.text)
    assert len(queries) == 8
    for query in queries:
        assert query["event_name"] == ["a&b"]
        assert query["app_id"] == ["x"]


@pytest.mark.db
async def test_an_unscoped_page_asks_for_no_app(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get("/dashboard/overview")
    for query in _pill_queries(response.text):
        assert "app_id" not in query
        assert "hours" in query
