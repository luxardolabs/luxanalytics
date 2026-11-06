"""App CRUD — all app table queries."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_model import App
from app.models.event_model import Event


class AppCRUD:
    async def get_by_public_id(self, db: AsyncSession, public_id: str) -> App | None:
        result = await db.execute(
            select(App).where(
                App.public_id == public_id, App.is_active, App.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def get_by_project_id(self, db: AsyncSession, project_id: str) -> App | None:
        result = await db.execute(
            select(App).where(
                App.project_id == project_id, App.is_active, App.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def get_by_app_id(self, db: AsyncSession, app_id: str) -> App | None:
        """The live app with this app_id (a deleted one is not found)."""
        result = await db.execute(
            select(App).where(App.app_id == app_id, App.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_app_id_including_deleted(
        self, db: AsyncSession, app_id: str
    ) -> App | None:
        """The app holding this app_id, deleted or not: a deleted row keeps its app_id."""
        result = await db.execute(select(App).where(App.app_id == app_id))
        return result.scalar_one_or_none()

    async def get_all(
        self, db: AsyncSession, include_inactive: bool = False
    ) -> list[App]:
        query = select(App).where(App.deleted_at.is_(None))
        if not include_inactive:
            query = query.where(App.is_active.is_(True))
        query = query.order_by(App.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, app: App) -> App:
        """Insert the app. The insert runs in a savepoint, so a unique-index refusal leaves the
        request's transaction usable for the caller to answer with."""
        async with db.begin_nested():
            db.add(app)
            await db.flush()
        await db.refresh(app)
        return app

    async def update(self, db: AsyncSession, app: App) -> App:
        await db.flush()
        await db.refresh(app)
        return app

    async def soft_delete(self, db: AsyncSession, app: App) -> None:
        app.soft_delete()
        await db.flush()

    async def restore(self, db: AsyncSession, app: App) -> App:
        """Bring a deleted app back, active, with its app_id and events as they were."""
        app.deleted_at = None
        app.is_active = True
        return await self.update(db, app)

    async def search(self, db: AsyncSession, query_str: str) -> list[App]:
        pattern = f"%{query_str}%"
        result = await db.execute(
            select(App)
            .where(
                App.deleted_at.is_(None),
                (App.name.ilike(pattern))
                | (App.app_id.ilike(pattern))
                | (App.organization.ilike(pattern))
                | (App.public_id.ilike(pattern)),
            )
            .order_by(App.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_stats(self, db: AsyncSession, app_id: str) -> dict[str, int]:
        result = await db.execute(
            select(
                func.count(Event.id).label("total_events"),
                func.count(func.distinct(Event.user_id)).label("unique_users"),
                func.count(func.distinct(Event.session_id)).label("unique_sessions"),
            ).where(Event.app_id == app_id)
        )
        row = result.one()
        return {
            "total_events": row.total_events,
            "unique_users": row.unique_users,
            "unique_sessions": row.unique_sessions,
        }

    async def event_counts(
        self, db: AsyncSession, app_ids: list[str]
    ) -> dict[str, int]:
        """Events per app for every id in `app_ids`, in one GROUP BY (absent = no events)."""
        if not app_ids:
            return {}
        result = await db.execute(
            select(Event.app_id, func.count(Event.id))
            .where(Event.app_id.in_(app_ids))
            .group_by(Event.app_id)
        )
        return dict(result.all())


app_crud = AppCRUD()
