"""App CRUD — all app table queries."""

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_model import App
from app.models.event_model import Event


class AppCRUD:

    async def get_by_public_id(self, db: AsyncSession, public_id: str) -> Optional[App]:
        result = await db.execute(
            select(App).where(App.public_id == public_id, App.is_active)
        )
        return result.scalar_one_or_none()

    async def get_by_project_id(self, db: AsyncSession, project_id: str) -> Optional[App]:
        result = await db.execute(
            select(App).where(App.project_id == project_id, App.is_active)
        )
        return result.scalar_one_or_none()

    async def get_by_app_id(self, db: AsyncSession, app_id: str) -> Optional[App]:
        result = await db.execute(select(App).where(App.app_id == app_id))
        return result.scalar_one_or_none()

    async def get_all(self, db: AsyncSession, include_inactive: bool = False) -> list:
        query = select(App)
        if not include_inactive:
            query = query.where(App.is_active)
        query = query.order_by(App.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, app: App) -> App:
        db.add(app)
        await db.flush()
        await db.refresh(app)
        return app

    async def update(self, db: AsyncSession, app: App) -> App:
        await db.flush()
        await db.refresh(app)
        return app

    async def delete(self, db: AsyncSession, app: App) -> None:
        await db.delete(app)
        await db.flush()

    async def search(self, db: AsyncSession, query_str: str) -> list:
        pattern = f"%{query_str}%"
        result = await db.execute(
            select(App)
            .where(
                (App.name.ilike(pattern))
                | (App.app_id.ilike(pattern))
                | (App.organization.ilike(pattern))
                | (App.public_id.ilike(pattern))
            )
            .order_by(App.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_stats(self, db: AsyncSession, app_id: str) -> dict:
        result = await db.execute(
            select(
                func.count(Event.id).label("total_events"),
                func.count(func.distinct(Event.user_id)).label("unique_users"),
                func.count(func.distinct(Event.session_id)).label("unique_sessions"),
            ).where(Event.app_id == app_id)
        )
        row = result.first()
        return {"total_events": row.total_events, "unique_users": row.unique_users, "unique_sessions": row.unique_sessions}


app_crud = AppCRUD()
