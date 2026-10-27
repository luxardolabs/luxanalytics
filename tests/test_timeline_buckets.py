"""Each timeline bucket starts where the dashboard's gap filler expects it to
(AnalyticsCoreService.get_events_timeline steps from the same boundaries)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.event_crud import event_crud
from app.models.event_model import Event

pytestmark = pytest.mark.db

AT = datetime(2026, 10, 14, 14, 37, 12, tzinfo=UTC)  # a Wednesday


@pytest.mark.parametrize(
    ("bucket_key", "start"),
    [
        ("5min", datetime(2026, 10, 14, 14, 35, tzinfo=UTC)),
        ("30min", datetime(2026, 10, 14, 14, 30, tzinfo=UTC)),
        ("1hour", datetime(2026, 10, 14, 14, 0, tzinfo=UTC)),
        # 12:00-18:00 of that day. The text expression this replaced added the slot to the
        # start of the HOUR, so 14:37 landed at 02:00 the next day, a key the gap filler never has.
        ("6hour", datetime(2026, 10, 14, 12, 0, tzinfo=UTC)),
        ("1day", datetime(2026, 10, 14, tzinfo=UTC)),
        ("1week", datetime(2026, 10, 12, tzinfo=UTC)),
        ("1month", datetime(2026, 10, 1, tzinfo=UTC)),
    ],
)
async def test_an_event_lands_in_the_bucket_that_holds_it(
    db: AsyncSession, bucket_key: str, start: datetime
) -> None:
    db.add(
        Event(
            id=str(uuid4()),
            app_id="bucket_app",
            name="app_opened",
            timestamp=AT,
            received_at=AT,
            properties={},
        )
    )
    await db.flush()

    rows = await event_crud.timeline_buckets(
        db, bucket_key, [Event.app_id == "bucket_app"]
    )

    assert [(bucket.replace(tzinfo=UTC), count) for bucket, count in rows] == [
        (start, 1)
    ]


async def test_an_unknown_bucket_key_is_refused(db: AsyncSession) -> None:
    with pytest.raises(ValueError, match="Invalid bucket key"):
        await event_crud.timeline_buckets(db, "2hour", [])
