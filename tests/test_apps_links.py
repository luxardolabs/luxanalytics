"""An app that already exists keeps working whatever its app_id holds: the list renders, and the
links it hands out reach that app when a browser follows them."""

import re
from html import unescape
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
