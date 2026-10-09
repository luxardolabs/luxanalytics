"""One device row per (app, device) (LUXANALYTI-84). The SDK's device id comes from
identifierForVendor, which every app of one vendor shares, so one phone running two apps reports
one id to both. Keyed on the id alone, the row went to whichever app came first: the other app's
device list missed the phone, and every app overwrote its version, build and TestFlight flag."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.device_crud import device_crud
from app.models.device_model import Device
from app.models.event_model import Event
from app.schemas.event_schema import EventCreate
from app.services.core.analytics_core_service import AnalyticsCoreService
from app.services.core.event_core_service import EventCoreService
from tests.test_apps_web import _login

pytestmark = pytest.mark.db

PHONE = "shared-phone"


async def _send(
    db: AsyncSession, app_id: str, version: str, testflight: str, n: int = 1
) -> None:
    now = datetime.now(UTC).isoformat()
    md = {"device_id": PHONE, "app_version": version, "is_testflight": testflight}
    events = [EventCreate(name=f"e{i}", timestamp=now, metadata=md) for i in range(n)]
    await EventCoreService(db).create_events(app_id, events)
    await db.flush()


async def test_each_app_keeps_its_own_row_for_a_shared_phone(db: AsyncSession) -> None:
    await _send(db, "app_card", "3037.0.2", "false")
    await _send(db, "app_wx", "1.0.23", "true")
    rows = {
        d.app_id: d
        for d in (
            await db.execute(select(Device).where(Device.device_id == PHONE))
        ).scalars()
    }
    assert set(rows) == {"app_card", "app_wx"}
    assert (rows["app_card"].app_version, rows["app_card"].is_testflight) == (
        "3037.0.2",
        False,
    )
    assert (rows["app_wx"].app_version, rows["app_wx"].is_testflight) == (
        "1.0.23",
        True,
    )


async def test_the_second_app_lists_the_phone_with_its_own_counts(
    db: AsyncSession,
) -> None:
    await _send(db, "app_card", "3037.0.2", "false", n=3)
    await _send(db, "app_wx", "1.0.23", "true", n=2)
    for app_id, expected in (("app_card", 3), ("app_wx", 2)):
        rows = await device_crud.get_details_with_event_counts(db, app_id=app_id)
        mine = [(d.app_id, total) for d, total, _ in rows if d.device_id == PHONE]
        assert mine == [(app_id, expected)]
    # All apps: one row per install, each with its own app's count, not the phone's total.
    everything = await device_crud.get_details_with_event_counts(db)
    assert sorted((d.app_id, t) for d, t, _ in everything if d.device_id == PHONE) == [
        ("app_card", 3),
        ("app_wx", 2),
    ]


async def test_all_apps_breakdowns_count_a_phone_once(db: AsyncSession) -> None:
    await _send(db, "app_card", "3037.0.2", "false")
    await _send(db, "app_wx", "1.0.23", "true")
    versions = dict(await device_crud.count_by_field(db, Device.platform))
    assert versions["ios"] == 1
    # TestFlight is a per-install fact: the phone is a TestFlight install of one app only.
    assert await device_crud.testflight_stats(db, app_id="app_wx") == {
        "testflight": 1,
        "appstore": 0,
    }
    assert await device_crud.testflight_stats(db, app_id="app_card") == {
        "testflight": 0,
        "appstore": 1,
    }


async def test_events_are_untouched(db: AsyncSession) -> None:
    await _send(db, "app_card", "3037.0.2", "false")
    await _send(db, "app_wx", "1.0.23", "true")
    apps = (
        await db.execute(select(Event.app_id).where(Event.device_id == PHONE))
    ).scalars()
    assert sorted(apps) == ["app_card", "app_wx"]


async def test_all_apps_device_rows_name_their_app(
    client: AsyncClient, db: AsyncSession
) -> None:
    """All apps: one row per install, so a shared phone is listed once per app, and each row
    must say which app it is (adversarial pass 5: two identical-looking ids, different badges)."""
    await _send(db, "app_card", "3037.0.2", "false")
    await _send(db, "app_wx", "1.0.23", "true")
    rows = (await AnalyticsCoreService(db).get_device_analytics(app_id=None, hours=0))[
        "device_details"
    ]
    assert sorted(r["app_id"] for r in rows if r["device_id"] == PHONE[:8] + "...") == [
        "app_card",
        "app_wx",
    ]
    await _login(client, "198.51.100.84")
    page = (await client.get("/dashboard/devices/content", params={"hours": 0})).text
    assert page.count(PHONE[:8] + "...") == 2
    assert "app_card" in page and "app_wx" in page
