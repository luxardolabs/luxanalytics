"""An app that already exists keeps working whatever its app_id holds: the list renders, and the
links it hands out reach that app when a browser follows them."""

import re
from html import unescape
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_model import App

APP_IDS = [
    "MyApp",
    "com.luxardo.Foo",
    "a b",
    "café",
    "a&b",
    "a+b",
    "a#b",
    "a?b",
    "a%b",
    "a%41",
    "a/b",
    "a/b/c",
    "x/edit",
    "x/detail",
]


def _app(app_id: str) -> App:
    return App(
        id=uuid4(),
        public_id=uuid4().hex,
        project_id=str(uuid4().int)[:16],
        app_id=app_id,
        name=f"name of {app_id}",
        is_active=True,
        app_metadata={},
    )


@pytest.mark.db
@pytest.mark.parametrize("app_id", APP_IDS)
async def test_the_links_of_an_existing_app_reach_that_app(
    dashboard_client: AsyncClient, db: AsyncSession, app_id: str
) -> None:
    db.add(_app(app_id))
    await db.flush()

    listing = await dashboard_client.get("/apps/list")
    assert listing.status_code == 200
    links = [unescape(u) for u in re.findall(r'hx-get="(/apps/[^"]*)"', listing.text)]
    assert links

    for link in links:
        panel = await dashboard_client.get(link)
        assert panel.status_code == 200, link
        assert f"name of {app_id}" in unescape(panel.text) or app_id in unescape(
            panel.text
        )

    edit_link = next(link for link in links if link.endswith("/edit"))
    edit = unescape((await dashboard_client.get(edit_link)).text)
    update_url = re.search(r'hx-put="([^"]*)"', edit)
    assert update_url is not None
    updated = await dashboard_client.put(
        update_url.group(1), data={"name": "renamed", "is_active": "on"}
    )
    assert updated.status_code == 200
    assert "renamed" in updated.text

    deleted = await dashboard_client.delete(update_url.group(1))
    assert deleted.status_code == 200
    assert "renamed" not in deleted.text


def _as_a_browser_sends_it(link: str) -> str:
    """RFC 3986 dot-segment removal: what a browser does to a URL before it sends it."""
    parts = urlsplit(urljoin("https://test/", link))
    return parts.path + (f"?{parts.query}" if parts.query else "")


@pytest.mark.db
async def test_a_dotted_app_id_cannot_send_an_action_to_another_app(
    dashboard_client: AsyncClient, db: AsyncSession
) -> None:
    victim, odd = _app("victim"), _app("adv/../victim")
    db.add_all([victim, odd])
    await db.flush()

    listing = await dashboard_client.get("/apps/list")
    links = [unescape(u) for u in re.findall(r'hx-get="(/apps/[^"]*)"', listing.text)]
    odd_links = [u for u in links if "adv" in u]
    assert odd_links
    for link in odd_links:
        assert _as_a_browser_sends_it(link) == link

    edit = next(u for u in odd_links if u.endswith("/edit"))
    form = unescape((await dashboard_client.get(edit)).text)
    update_url = re.search(r'hx-put="([^"]*)"', form)
    assert update_url is not None
    sent = _as_a_browser_sends_it(update_url.group(1))
    response = await dashboard_client.put(sent, data={"name": "overwritten"})
    assert response.status_code == 200
    await db.refresh(victim)
    await db.refresh(odd)
    assert victim.name == "name of victim"
    assert odd.name == "overwritten"


@pytest.mark.db
@pytest.mark.parametrize("app_id", [".", ".."])
async def test_an_app_id_of_dots_fails_safe(
    dashboard_client: AsyncClient, db: AsyncSession, app_id: str
) -> None:
    # A browser cannot send a dot segment as part of a path (even as %2e), so these ids have no
    # working link: what its links reach must never be another app.
    victim, odd = _app("victim"), _app(app_id)
    db.add_all([victim, odd])
    await db.flush()

    listing = await dashboard_client.get("/apps/list")
    assert listing.status_code == 200
    links = [unescape(u) for u in re.findall(r'hx-get="(/apps/[^"]*)"', listing.text)]
    for link in [u for u in links if "victim" not in u]:
        sent = _as_a_browser_sends_it(link)
        for method in ("GET", "PUT", "DELETE"):
            response = await dashboard_client.request(
                method, sent, data={"name": "overwritten"}
            )
            assert response.status_code != 200 or "name of victim" not in response.text
    await db.refresh(victim)
    assert victim.name == "name of victim"
    assert victim.is_active


@pytest.mark.db
async def test_an_app_id_with_a_slash_does_not_break_the_list_or_the_export(
    dashboard_client: AsyncClient, db: AsyncSession, sample_app: App
) -> None:
    db.add(_app("a/b"))
    await db.flush()

    listing = await dashboard_client.get("/apps/list")
    assert listing.status_code == 200
    assert sample_app.app_id in listing.text
    export = await dashboard_client.get("/apps/export/json")
    assert export.status_code == 200
    assert "a/b" in export.text
