"""User ids and session ids are app-chosen, so two apps can share one: the user and session panels
show the selected app's rows only, and every link to a panel carries the app of the row it is on."""

import html
import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_model import Event
from tests.test_apps_web import _login

pytestmark = pytest.mark.db


def _row(app_id: str, name: str, **columns: object) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": str(uuid4()),
        "app_id": app_id,
        "name": name,
        "timestamp": now,
        "received_at": now,
        **columns,
    }


async def _seed_two_apps(db: AsyncSession) -> None:
    await db.execute(
        insert(Event),
        [
            _row(
                "test_app",
                "a_evt",
                user_id="u1",
                session_id="shared",
                device_id="dev-a",
                device_model="ModelInTestApp",
            ),
            _row(
                "other_app",
                "b_evt",
                user_id="u1",
                session_id="shared",
                device_id="dev-b",
                device_model="ModelInOtherApp",
            ),
            _row("other_app", "b_only_evt", user_id="u1", session_id="only_other"),
        ],
    )
    await db.flush()


async def test_user_panel_shows_only_the_selected_apps_rows(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_two_apps(db)
    await _login(client, "198.51.100.141")
    response = await client.get("/dashboard/user/u1?app_id=test_app")
    assert response.status_code == 200
    assert "ModelInTestApp" in response.text
    assert "ModelInOtherApp" not in response.text
    assert "only_other" not in response.text


async def test_session_panel_shows_only_the_selected_apps_events(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_two_apps(db)
    await _login(client, "198.51.100.142")
    response = await client.get("/dashboard/session/shared?app_id=test_app")
    assert response.status_code == 200
    assert "A Evt" in response.text
    assert "B Evt" not in response.text


async def test_a_session_panel_with_no_app_asks_for_one(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_two_apps(db)
    await _login(client, "198.51.100.144")
    response = await client.get("/dashboard/session/shared")
    assert response.status_code == 200
    assert "Select an app" in response.text
    assert "A Evt" not in response.text
    assert "B Evt" not in response.text


async def test_a_user_panel_with_no_app_counts_each_apps_sessions(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_two_apps(db)
    await _login(client, "198.51.100.145")
    response = await client.get("/dashboard/user/u1")
    assert response.status_code == 200
    assert "Sessions (3 of 3)" in response.text
    for link in (
        "/dashboard/session/shared?app_id=test_app",
        "/dashboard/session/shared?app_id=other_app",
        "/dashboard/session/only_other?app_id=other_app",
    ):
        assert f'hx-get="{link}"' in response.text


async def _event_page(client: AsyncClient) -> str:
    page = await client.get("/dashboard/events/content?hours=24")
    assert page.status_code == 200
    return page.text


async def test_a_row_with_an_empty_app_id_has_no_panel_links(
    client: AsyncClient, db: AsyncSession
) -> None:
    await db.execute(
        insert(Event),
        [
            _row("", "blank_evt", user_id="blank_user", session_id="blank_session"),
            _row("test_app", "a_evt", user_id="u1", session_id="s1"),
        ],
    )
    await db.flush()
    await _login(client, "198.51.100.146")
    text = await _event_page(client)
    assert "/dashboard/user/u1?app_id=test_app" in text
    assert "/dashboard/user/blank_user" not in text
    assert "/dashboard/session/blank_session" not in text


async def test_a_dot_segment_id_has_no_panel_link(
    client: AsyncClient, db: AsyncSession
) -> None:
    await db.execute(
        insert(Event),
        [
            _row("test_app", "dots_evt", user_id="..", session_id="."),
            _row("test_app", "a_evt", user_id="u1", session_id="s1"),
        ],
    )
    await db.flush()
    await _login(client, "198.51.100.147")
    text = await _event_page(client)
    assert "/dashboard/user/u1?app_id=test_app" in text
    assert "/dashboard/user/.." not in text
    assert "/dashboard/session/." not in text


async def test_an_app_id_too_long_for_a_url_is_left_off_the_link(
    client: AsyncClient, db: AsyncSession
) -> None:
    long_app = "a" * 3600
    await db.execute(
        insert(Event),
        [_row(long_app, "long_evt", user_id="u7", session_id="s7")],
    )
    await db.flush()
    await _login(client, "198.51.100.148")
    text = await _event_page(client)
    assert 'hx-get="/dashboard/user/u7"' in text
    assert 'hx-get="/dashboard/session/s7"' in text


async def test_panel_links_carry_the_row_apps_id(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_two_apps(db)
    await _login(client, "198.51.100.143")
    page = await client.get("/dashboard/events/content?hours=24")
    assert page.status_code == 200
    assert 'hx-get="/dashboard/user/u1?app_id=other_app"' in page.text
    assert 'hx-get="/dashboard/session/shared?app_id=test_app"' in page.text
    profile = await client.get("/dashboard/user/u1?app_id=other_app")
    assert 'hx-get="/dashboard/session/only_other?app_id=other_app"' in profile.text


AWKWARD_IDS = [
    "a b",
    "a+b",
    "a&app_id=other_app",
    "a%20b",
    "a/b",
    "/lead",
    "trail/",
    "a?b=c#d",
    "é✓",
    "'single'",
    "{{7*7}}",
    'x"><img src=x onerror=alert(1)>',
]


@pytest.mark.parametrize("awkward", AWKWARD_IDS)
async def test_panel_links_round_trip_an_awkward_id_to_the_same_app_only(
    client: AsyncClient, db: AsyncSession, awkward: str
) -> None:
    await db.execute(
        insert(Event),
        [
            _row(awkward, "mine_evt", user_id=awkward, session_id=awkward),
            _row("other_app", "theirs_evt", user_id=awkward, session_id=awkward),
        ],
    )
    await db.flush()
    await _login(client, "198.51.100.149")
    page = await client.get("/dashboard/events/content", params={"app_id": awkward})
    assert page.status_code == 200
    assert "<img src=x" not in page.text
    panels = [
        html.unescape(link)
        for link in re.findall(r'hx-get="([^"]+)"', page.text)
        if link.startswith(("/dashboard/user/", "/dashboard/session/"))
    ]
    assert len(panels) == 2
    for link in panels:
        panel = await client.get(link)
        assert panel.status_code == 200, link
        assert "Theirs Evt" not in panel.text, link
        assert "<img src=x" not in panel.text
