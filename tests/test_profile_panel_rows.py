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


Install = tuple[str | None, str | None, str | None]


async def _installs(db: AsyncSession) -> tuple[dict[str, Install], dict[str, Install]]:
    """(devices table, profile rows): each device's (model, OS, app version)."""
    stored = {
        d.device_id: (d.device_model, d.os_version, d.app_version)
        for d in (
            await db.execute(select(Device).where(Device.app_id == "test_app"))
        ).scalars()
    }
    rows = await event_crud.get_user_devices(db, "adv_u", "test_app")
    return stored, {
        r.device_id: (r.device_model, r.os_version, r.app_version) for r in rows
    }


async def test_a_late_batch_of_older_events_agrees_with_the_devices_table(
    db: AsyncSession,
) -> None:
    """The devices table holds an install's current state, the last batch to arrive winning: a
    retried batch of older events that arrives last is what both show."""
    now = datetime.now(UTC)
    ingest = EventCoreService(db)
    await ingest.create_events(
        "test_app", [_sdk_event("n", now, device_id="d1", app_version="1.1")]
    )
    await ingest.create_events(
        "test_app",
        [_sdk_event("o", now - timedelta(hours=1), device_id="d1", app_version="1.0")],
    )
    await db.flush()

    stored, rows = await _installs(db)

    assert stored["d1"] == rows["d1"] == (None, None, "1.0")


async def test_batches_in_order_agree_with_the_devices_table(db: AsyncSession) -> None:
    now = datetime.now(UTC)
    ingest = EventCoreService(db)
    await ingest.create_events(
        "test_app",
        [_sdk_event("o", now - timedelta(hours=1), device_id="d1", app_version="1.0")],
    )
    await ingest.create_events(
        "test_app", [_sdk_event("n", now, device_id="d1", app_version="1.1")]
    )
    await db.flush()

    stored, rows = await _installs(db)

    assert stored["d1"] == rows["d1"] == (None, None, "1.1")


async def test_a_slow_device_clock_after_a_correct_one_agrees_with_the_devices_table(
    db: AsyncSession,
) -> None:
    """A phone whose clock was reset a month back, now on iOS 18: its later batch is its current
    state, whatever its event time says."""
    now = datetime.now(UTC)
    ingest = EventCoreService(db)
    await ingest.create_events(
        "test_app", [_sdk_event("a", now, device_id="d1", system_version="17")]
    )
    await ingest.create_events(
        "test_app",
        [
            _sdk_event(
                "b", now - timedelta(days=30), device_id="d1", system_version="18"
            )
        ],
    )
    await db.flush()

    stored, rows = await _installs(db)

    assert stored["d1"] == rows["d1"] == (None, "18", None)


async def test_equal_event_times_in_one_batch_agree_with_the_devices_table(
    db: AsyncSession,
) -> None:
    """Within one batch both sides break a tie of event time the same way, by event id."""
    at = datetime.now(UTC)
    batch = []
    for i in range(24):
        batch.append(_sdk_event(f"x{i}-1", at, device_id=f"d{i:02}", app_version="A"))
        batch.append(_sdk_event(f"x{i}-2", at, device_id=f"d{i:02}", app_version="B"))
    await EventCoreService(db).create_events("test_app", batch)
    await db.flush()

    stored, rows = await _installs(db)

    assert {k: (stored[k], rows[k]) for k in stored if stored[k] != rows[k]} == {}


async def test_a_whitespace_device_id_is_a_device_in_ingest_rows_and_card(
    db: AsyncSession,
) -> None:
    await EventCoreService(db).create_events(
        "test_app",
        [_sdk_event("w", datetime.now(UTC), device_id=" ", device_model="M")],
    )
    await db.flush()

    stored, rows = await _installs(db)
    summary = await event_crud.get_user_summary(db, "adv_u", "test_app")

    assert set(stored) == set(rows) == {" "}
    assert summary["total_devices"] == len(rows) == 1


async def test_a_hostile_app_or_user_id_is_bound_not_spliced(db: AsyncSession) -> None:
    await db.execute(
        insert(Event),
        [_row("test_app", "e", user_id="u'x", device_id="d", device_model="M")],
    )
    await db.flush()

    assert await event_crud.get_user_devices(db, "u'x", "x' OR '1'='1") == []
    rows = await event_crud.get_user_devices(db, "u'x", None)
    assert [(r.device_id, r.device_model) for r in rows] == [("d", "M")]


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


async def test_a_device_with_no_devices_row_shows_its_events_values(
    db: AsyncSession,
) -> None:
    await db.execute(
        insert(Event),
        [_row("test_app", "e", user_id="no-row", device_id="orphan", device_model="M")],
    )
    await db.flush()

    rows = await event_crud.get_user_devices(db, "no-row", "test_app")

    assert [(r.device_id, r.device_model, r.event_count) for r in rows] == [
        ("orphan", "M", 1)
    ]


def _event_of(
    client_id: str, user_id: str, at: datetime, **metadata: str
) -> EventCreate:
    return EventCreate.model_validate(
        {
            "client_event_id": client_id,
            "name": "e",
            "timestamp": at.isoformat(),
            "user_id": user_id,
            "metadata": metadata,
        }
    )


async def test_a_shared_install_shows_each_user_the_values_of_their_own_events(
    db: AsyncSession,
) -> None:
    """Two users of one app on one device id share its devices row, which the later user's events
    rewrite: each user's row describes what that user's events sent."""
    ingest = EventCoreService(db)
    now = datetime.now(UTC)
    install = {"device_id": "shared-dev", "device_model": "iPhone12"}
    await ingest.create_events(
        "test_app",
        [
            _event_of(
                "x1",
                "user_x",
                now - timedelta(days=3),
                system_version="17.0",
                app_version="1.0",
                **install,
            )
        ],
    )
    await db.flush()
    await ingest.create_events(
        "test_app",
        [
            _event_of(
                "y1", "user_y", now, system_version="18.0", app_version="2.0", **install
            )
        ],
    )
    await db.flush()

    x_rows = await event_crud.get_user_devices(db, "user_x", "test_app")
    y_rows = await event_crud.get_user_devices(db, "user_y", "test_app")

    assert [(r.os_version, r.app_version, r.event_count) for r in x_rows] == [
        ("17.0", "1.0", 1)
    ]
    assert [(r.os_version, r.app_version, r.event_count) for r in y_rows] == [
        ("18.0", "2.0", 1)
    ]


async def test_another_apps_install_never_feeds_a_row(db: AsyncSession) -> None:
    await db.execute(
        insert(Event), [_row("test_app", "e", user_id="ten", device_id="tdev")]
    )
    await db.execute(
        insert(Device),
        [
            {
                "app_id": "other_app",
                "device_id": "tdev",
                "device_model": "LEAK",
                "os_version": "LEAK",
                "app_version": "LEAK",
                "platform": "LEAK",
            }
        ],
    )
    await db.flush()

    for app in ("test_app", None):
        rows = await event_crud.get_user_devices(db, "ten", app)
        assert len(rows) == 1
        assert "LEAK" not in {
            rows[0].device_model,
            rows[0].os_version,
            rows[0].app_version,
            rows[0].platform,
        }
    assert await event_crud.get_user_devices(db, "ten", "other_app") == []


async def test_an_empty_device_id_is_no_device_in_the_rows_or_the_card(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Ingest treats an empty device id as none (it upserts no devices row): the profile lists no
    device for it and its Devices card does not count it."""
    ingest = EventCoreService(db)
    await ingest.create_events(
        "test_app",
        [
            _event_of(
                "emp1",
                "user_empty",
                datetime.now(UTC),
                device_id="",
                device_model="iPhone15",
            )
        ],
    )
    await db.flush()
    await _login(client, "198.51.100.162")

    rows = await event_crud.get_user_devices(db, "user_empty", "test_app")
    flat = _flat((await client.get("/dashboard/user/user_empty?app_id=test_app")).text)

    assert rows == []
    assert "Devices 0" in flat


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
