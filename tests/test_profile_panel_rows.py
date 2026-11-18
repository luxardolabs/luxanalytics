"""The user profile's rows and the links into panels: the all-apps Devices card counts what its
rows show, a long app id is cut, an id with nothing visible gets no link, an explorer key with a
newline opens its own key, and signing in from a panel URL lands on that panel."""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.event_crud import event_crud
from app.models.device_model import Device
from app.models.event_model import Event
from app.schemas.event_schema import EventCreate
from app.services.core.event_core_service import EventCoreService
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
    now = datetime.now(UTC)
    install = {"device_id": "d1", "device_model": "iPhone15"}
    ingest = EventCoreService(db)
    await ingest.create_events(
        "test_app",
        [
            _sdk_event(
                "up-1",
                now - timedelta(minutes=5),
                system_version="17",
                app_version="1.0",
                **install,
            )
        ],
    )
    await ingest.create_events(
        "test_app",
        [_sdk_event("up-2", now, system_version="18", app_version="1.1", **install)],
    )
    await db.flush()
    await _login(client, "198.51.100.160")

    flat = _flat((await client.get("/dashboard/user/adv_u?app_id=test_app")).text)

    assert "Devices 1" in flat
    assert flat.count("iPhone15") == 1
    assert "iOS 18 · v1.1 2 events" in flat


def _sdk_event(client_id: str, at: datetime, **metadata: str) -> EventCreate:
    """An event as the SDK sends it, for the ingest path that upserts the devices table."""
    return EventCreate.model_validate(
        {
            "client_event_id": client_id,
            "name": "e",
            "timestamp": at.isoformat(),
            "user_id": "adv_u",
            "metadata": metadata,
        }
    )


async def _profile_versions(
    db: AsyncSession,
) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """(devices table, profile rows): each device's app version, keyed by device id."""
    stored = {
        d.device_id: d.app_version
        for d in (
            await db.execute(select(Device).where(Device.app_id == "test_app"))
        ).scalars()
    }
    rows = await event_crud.get_user_devices(db, "adv_u", "test_app")
    return stored, {r.device_id: r.app_version for r in rows}


async def test_an_offline_flush_shows_the_latest_version_like_the_devices_table(
    db: AsyncSession,
) -> None:
    """An offline queue flushes old and new events in one batch, so every row of it has the same
    received_at: the profile row takes its values from the devices table, which orders by event
    time, never from whichever event of the batch a query happens to pick."""
    ingest = EventCoreService(db)
    now = datetime.now(UTC)
    for i in range(12):
        device = {"device_id": f"flush-{i}", "device_model": "iPhone14"}
        old = _sdk_event(
            f"{'a' if i % 2 else 'z'}-{i}-old",
            now - timedelta(hours=2),
            system_version="17.0",
            app_version="1.0",
            **device,
        )
        new = _sdk_event(
            f"{'z' if i % 2 else 'a'}-{i}-new",
            now,
            system_version="18.0",
            app_version="2.0",
            **device,
        )
        await ingest.create_events("test_app", [old, new])
    await db.flush()

    stored, profile = await _profile_versions(db)

    assert set(stored.values()) == {"2.0"}
    assert profile == stored


async def test_a_later_event_without_a_model_keeps_the_model_like_the_devices_table(
    db: AsyncSession,
) -> None:
    ingest = EventCoreService(db)
    now = datetime.now(UTC)
    await ingest.create_events(
        "test_app",
        [
            _sdk_event(
                "m1",
                now - timedelta(minutes=5),
                device_id="keep-m",
                device_model="iPhone15",
                system_version="18.0",
                app_version="1.0",
            )
        ],
    )
    await db.flush()
    await ingest.create_events("test_app", [_sdk_event("m2", now, device_id="keep-m")])
    await db.flush()

    device = (
        await db.execute(select(Device).where(Device.device_id == "keep-m"))
    ).scalar_one()
    rows = await event_crud.get_user_devices(db, "adv_u", "test_app")

    assert device.device_model == "iPhone15"
    assert (rows[0].device_model, rows[0].os_version) == ("iPhone15", device.os_version)
    assert rows[0].event_count == 2


async def test_a_device_in_two_apps_shows_each_installs_own_values(
    db: AsyncSession,
) -> None:
    """The devices table is keyed on (app, device): with no app selected each row joins its own
    app's install, and with one selected only that app's row is listed."""
    ingest = EventCoreService(db)
    now = datetime.now(UTC)
    shared = {"device_id": "two-apps"}
    await ingest.create_events(
        "test_app", [_sdk_event("t1", now, app_version="1.0", **shared)]
    )
    await ingest.create_events(
        "other_app", [_sdk_event("o1", now, app_version="9.0", **shared)]
    )
    await db.flush()

    everywhere = await event_crud.get_user_devices(db, "adv_u")
    scoped = await event_crud.get_user_devices(db, "adv_u", "other_app")

    assert sorted((r.app_id, r.app_version) for r in everywhere) == [
        ("other_app", "9.0"),
        ("test_app", "1.0"),
    ]
    assert [(r.app_id, r.app_version) for r in scoped] == [("other_app", "9.0")]


async def test_a_device_with_events_but_no_devices_row_is_still_listed(
    db: AsyncSession,
) -> None:
    await db.execute(
        insert(Event),
        [_row("test_app", "e", user_id="no-row", device_id="orphan", device_model="M")],
    )
    await db.flush()

    rows = await event_crud.get_user_devices(db, "no-row", "test_app")

    assert [(r.device_id, r.device_model, r.event_count) for r in rows] == [
        ("orphan", None, 1)
    ]


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
