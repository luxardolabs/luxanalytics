"""User ids and session ids are app-chosen, so two apps can share one: the user and session panels
show the selected app's rows only, and every link to a panel carries the app of the row it is on."""

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
