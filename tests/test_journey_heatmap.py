"""The journey page's transition heatmap: SDK-supplied screen names never reach its tooltip as markup.

charts.js builds the tooltip string from `data-heatmap-screens`, and ECharts inserts a formatter's
string as HTML. A screen name comes from any client holding the app's DSN, which is public (it
ships in every iOS app), so the tooltip list holds each name HTML-escaped; the axes keep the raw
names, which ECharts draws as canvas text.
"""

import html
import json
import re
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.models.app_model import App
from tests.test_ingest_http import PROJECT, _dsn_auth

pytestmark = pytest.mark.db

MARKUP = '<img src=x onerror="window.__xss=1">'


async def _login(client: AsyncClient) -> None:
    client.base_url = "https://test"  # the session cookie is https_only
    login = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
        },
        headers={"X-Forwarded-For": "198.51.100.96"},
        follow_redirects=False,
    )
    assert login.status_code == 303


async def _screen_viewed(client: AsyncClient, screen: str) -> None:
    body = {
        "name": "screen_viewed",
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": "s-heatmap",
        "user_id": "u-heatmap",
        "metadata": {"screen": screen},
    }
    r = await client.post(f"/api/v1/events/{PROJECT}", json=body, headers=_dsn_auth())
    assert r.status_code == 200, r.text


def _attribute(page: str, name: str) -> object:
    """A data attribute's JSON value, as the browser's getAttribute + JSON.parse read it."""
    m = re.search(rf"{name}='([^']*)'", page)
    assert m, f"{name} rendered"
    return json.loads(html.unescape(m.group(1)))


async def test_a_screen_name_reaches_the_heatmap_tooltip_as_text(
    client: AsyncClient, sample_app: App
) -> None:
    await _screen_viewed(client, "home")
    await _screen_viewed(client, MARKUP)
    await _login(client)

    page = await client.get(f"/dashboard/journey/content?app_id={sample_app.app_id}")

    assert page.status_code == 200
    heatmap = re.search(r'<div id="transition-heatmap".*?</div>', page.text, re.S)
    assert heatmap, "the heatmap rendered"
    tooltip_screens = _attribute(heatmap.group(0), "data-heatmap-screens")
    assert isinstance(tooltip_screens, list)
    assert "home" in tooltip_screens
    # The formatter concatenates these into HTML: each is the escaped text of a name.
    assert html.escape(MARKUP) in tooltip_screens
    assert not any("<" in s for s in tooltip_screens), tooltip_screens
    # The axes are drawn as canvas text, so they keep the name as sent.
    config = _attribute(heatmap.group(0), "data-echart-config")
    assert isinstance(config, dict)
    assert MARKUP in config["xAxis"]["data"]
