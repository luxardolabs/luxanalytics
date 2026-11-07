"""A session id is app-chosen, so two apps can share one: an app's exit screens come from its own
screen views only."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.event_crud import event_crud
from app.models.event_model import Event

pytestmark = pytest.mark.db


def _row(
    app_id: str, name: str, at: datetime, screen: str | None = None
) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "app_id": app_id,
        "name": name,
        "timestamp": at,
        "received_at": at,
        "session_id": "shared",
        "properties": {"screen": screen} if screen else None,
    }


async def test_exit_screens_ignore_another_apps_screens_in_a_shared_session(
    db: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    await db.execute(
        insert(Event),
        [
            _row("test_app", "screen_viewed", now - timedelta(seconds=90), "A_home"),
            _row("other_app", "screen_viewed", now - timedelta(seconds=30), "B_only"),
            _row("test_app", "app_entered_background", now),
        ],
    )
    await db.flush()
    assert await event_crud.get_exit_screens(db, app_id="test_app") == [("A_home", 1)]
