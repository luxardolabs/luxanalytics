"""The user profile's rows and the links into panels: the all-apps Devices card counts what its
rows show, a long app id is cut, an id with nothing visible gets no link, an explorer key with a
newline opens its own key, and signing in from a panel URL lands on that panel."""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.event_model import Event
from tests.test_apps_web import _login
from tests.test_panels_per_app import _row

pytestmark = pytest.mark.db


def _flat(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


async def test_a_no_app_profile_counts_a_device_once_per_app(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The device rows are per (app, device): the Devices card counts the same pairs."""
    await db.execute(
        insert(Event),
        [
            _row("test_app", "e", user_id="ud", device_id="dev-shared"),
            _row("other_app", "e", user_id="ud", device_id="dev-shared"),
            _row("other_app", "e", user_id="ud", device_id=None),
        ],
    )
    await db.flush()
    await _login(client, "198.51.100.154")

    everywhere = _flat((await client.get("/dashboard/user/ud")).text)
    scoped = _flat((await client.get("/dashboard/user/ud?app_id=other_app")).text)

    assert "Devices 2" in everywhere
    assert "Devices 1" in scoped


async def test_an_install_that_upgraded_is_one_device_row_with_its_latest_values(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The rows are one per (app, device), as the Devices card counts them: an install that moved
    to a new iOS or app version is one row, showing the values it sent last."""
    install = {"user_id": "up", "device_id": "d1", "device_model": "iPhone15"}
    await db.execute(
        insert(Event),
        [
            _row("test_app", "e", os_version="17", app_version="1.0", **install),
            {
                **_row("test_app", "e", os_version="18", app_version="1.1", **install),
                "received_at": datetime.now(UTC) + timedelta(seconds=5),
            },
        ],
    )
    await db.flush()
    await _login(client, "198.51.100.160")

    flat = _flat((await client.get("/dashboard/user/up?app_id=test_app")).text)

    assert "Devices 1" in flat
    assert flat.count("iPhone15") == 1
    assert "iOS 18 · v1.1 2 events" in flat


async def test_a_long_app_id_is_cut_in_the_profile_rows(
    client: AsyncClient, db: AsyncSession
) -> None:
    long_app = "l" * 200
    await db.execute(
        insert(Event),
        [
            _row(long_app, "e", user_id="ul", session_id="s", device_id="d"),
            _row(long_app, "error_occurred", user_id="ul", session_id="s"),
        ],
    )
    await db.flush()
    await _login(client, "198.51.100.156")

    flat = _flat((await client.get("/dashboard/user/ul")).text)

    assert long_app not in flat
    assert flat.count("l" * 20) == 3


@pytest.mark.parametrize("blank", ["   ", "​", "\t"])
async def test_a_blank_user_or_session_id_gets_no_link(
    client: AsyncClient, db: AsyncSession, blank: str
) -> None:
    """A link labelled with nothing visible is nothing to click."""
    await db.execute(
        insert(Event), [_row("test_app", "blank_evt", user_id=blank, session_id=blank)]
    )
    await db.flush()
    await _login(client, "198.51.100.157")

    page = await client.get("/dashboard/events/content?app_id=test_app")

    assert "Blank Evt" in page.text
    assert 'hx-get="/dashboard/user/' not in page.text
    assert 'hx-get="/dashboard/session/' not in page.text


async def test_an_explorer_key_with_a_newline_opens_its_own_key(
    client: AsyncClient, db: AsyncSession
) -> None:
    await db.execute(
        insert(Event),
        [_row("test_app", "e", properties={"victim": "only_victims_value"})],
    )
    await db.flush()
    await _login(client, "198.51.100.158")

    own = await client.get("/dashboard/explorer/key/victim?app_id=test_app")
    other = await client.get("/dashboard/explorer/key/victim%0A?app_id=test_app")

    assert "only_victims_value" in own.text
    assert other.status_code == 200
    assert "only_victims_value" not in other.text


@pytest.mark.parametrize(
    "raw_path",
    [
        "/dashboard/user/a%3Fb",
        "/dashboard/user/a%2Fb",
        "/dashboard/user/a%23b",
        "/dashboard/user/victim%0A",
    ],
)
async def test_a_signed_out_panel_url_is_kept_whole_as_the_login_next(
    client: AsyncClient, raw_path: str
) -> None:
    """`next` is the path as requested: decoded, a `?` would split the id and land on another
    row, and a newline would refuse the whole login redirect."""
    client.base_url = "https://test"
    refused = await client.get(raw_path, follow_redirects=False)

    assert refused.status_code == 303
    login_url = refused.headers["location"]
    assert login_url.startswith("/login?next=")
    assert parse_qs(urlsplit(login_url).query)["next"][0] == raw_path


@pytest.mark.parametrize(
    "next_url",
    [
        "//evil.example",
        "/%2F%2Fevil.example",
        "/%5Cevil.example",
        "/%09/evil.example",
        "/.//evil.example",
        "/%2e//evil.example",
        "https://evil.example",
    ],
)
async def test_signing_in_never_leaves_the_site(
    client: AsyncClient, next_url: str
) -> None:
    client.base_url = "https://test"
    signed_in = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
            "next": next_url,
        },
        headers={"X-Forwarded-For": "198.51.100.161"},
        follow_redirects=False,
    )

    location = signed_in.headers["location"]
    assert not location.startswith(("//", "/\\")) and "://" not in location


async def test_signing_in_from_a_panel_url_lands_on_that_panel(
    client: AsyncClient,
) -> None:
    client.base_url = "https://test"
    refused = await client.get("/dashboard/user/a%3Fb", follow_redirects=False)
    next_url = parse_qs(urlsplit(refused.headers["location"]).query)["next"][0]

    signed_in = await client.post(
        "/login",
        data={
            "username": settings.DASHBOARD_USERNAME,
            "password": settings.DASHBOARD_PASSWORD,
            "next": next_url,
        },
        headers={"X-Forwarded-For": "198.51.100.159"},
        follow_redirects=False,
    )

    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/dashboard/user/a%3Fb"
