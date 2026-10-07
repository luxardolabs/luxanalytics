"""Device CRUD — all device table queries."""

from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import and_, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_model import Device
from app.models.event_model import Event


class DeviceCRUD:
    """All database queries against the devices table."""

    def _time_filters(self, hours: int = 0, app_id: Optional[str] = None) -> list:
        """Build WHERE conditions for devices table (last_seen based)."""
        filters = []
        if hours and hours > 0:
            cutoff = datetime.now(UTC) - timedelta(hours=hours)
            filters.append(Device.last_seen >= cutoff)
        if app_id:
            filters.append(Device.app_id == app_id)
        return filters

    async def count_by_field(
        self, db: AsyncSession, column, app_id: Optional[str] = None,
        hours: int = 0, limit: int = 20,
    ) -> list:
        """GROUP BY a devices table column. Returns [(value, count)]."""
        filters = self._time_filters(hours, app_id) + [column.isnot(None)]
        query = (
            select(column, func.count().label("cnt"))
            .where(and_(*filters))
            .group_by(column)
            .order_by(desc("cnt"))
            .limit(limit)
        )
        result = await db.execute(query)
        return [(row[0], row[1]) for row in result.all()]

    async def testflight_stats(
        self, db: AsyncSession, app_id: Optional[str] = None, hours: int = 0,
    ) -> dict:
        filters = self._time_filters(hours, app_id)
        query = select(
            func.count().filter(Device.is_testflight.is_(True)).label("testflight"),
            func.count().filter(
                Device.is_testflight.is_(False) | Device.is_testflight.is_(None)
            ).label("appstore"),
        )
        if filters:
            query = query.where(and_(*filters))
        result = (await db.execute(query)).one()
        return {"testflight": result.testflight, "appstore": result.appstore}

    async def get_details_with_event_counts(
        self, db: AsyncSession, app_id: Optional[str] = None,
        hours: int = 0, limit: int = 50,
    ) -> list:
        """Device details joined with event counts."""
        # Event counts subquery — also filtered by time
        event_filters = [Event.device_id.isnot(None)]
        if hours and hours > 0:
            cutoff = datetime.now(UTC) - timedelta(hours=hours)
            event_filters.append(Event.received_at >= cutoff)
        if app_id:
            event_filters.append(Event.app_id == app_id)

        event_count_subq = (
            select(
                Event.device_id,
                func.count(Event.id).label("total_events"),
                func.count(func.distinct(Event.name)).label("event_types"),
            )
            .where(and_(*event_filters))
            .group_by(Event.device_id)
            .subquery()
        )

        device_filters = self._time_filters(hours, app_id)
        query = (
            select(Device, event_count_subq.c.total_events, event_count_subq.c.event_types)
            .outerjoin(event_count_subq, Device.device_id == event_count_subq.c.device_id)
            .order_by(desc(event_count_subq.c.total_events))
            .limit(limit)
        )
        if device_filters:
            query = query.where(and_(*device_filters))
        result = await db.execute(query)
        return list(result.all())


    async def upsert(self, db: AsyncSession, device_data: dict) -> None:
        stmt = pg_insert(Device).values(**device_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["device_id"],
            set_={
                "last_seen": device_data["last_seen"],
                "os_version": device_data["os_version"],
                "app_version": device_data["app_version"],
                "build_number": device_data["build_number"],
                "device_model": device_data["device_model"],
                "is_testflight": device_data["is_testflight"],
                "locale": device_data["locale"],
                "timezone": device_data["timezone"],
                "screen_resolution": device_data["screen_resolution"],
            },
        )
        await db.execute(stmt)


device_crud = DeviceCRUD()
