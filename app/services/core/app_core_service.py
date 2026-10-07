"""App service — business logic for app management. Calls app_crud for DB access."""

import secrets
import string

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tracing import create_service_span
from app.crud.app_crud import app_crud
from app.models.app_model import App
from app.schemas.app_schema import AppCreate, AppRow, AppStats, AppUpdate


class AppCoreService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def generate_public_id(length: int = 32) -> str:
        alphabet = string.ascii_lowercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def generate_project_id(length: int = 16) -> str:
        return "".join(secrets.choice(string.digits) for _ in range(length))

    async def create_app(self, app_data: AppCreate) -> AppRow:
        with create_service_span("AppCoreService", "create_app"):
            app = App(
                public_id=self.generate_public_id(),
                project_id=self.generate_project_id(),
                app_id=app_data.app_id,
                name=app_data.name,
                organization=app_data.organization,
                description=app_data.description,
                app_metadata=app_data.app_metadata,
            )
            return AppRow.model_validate(await app_crud.create(self.db, app))

    async def get_app_by_public_id(self, public_id: str) -> App | None:
        with create_service_span("AppCoreService", "get_app_by_public_id"):
            return await app_crud.get_by_public_id(self.db, public_id)

    async def get_app_by_project_id(self, project_id: str) -> App | None:
        with create_service_span("AppCoreService", "get_app_by_project_id"):
            return await app_crud.get_by_project_id(self.db, project_id)

    async def get_app_by_app_id(self, app_id: str) -> AppRow | None:
        with create_service_span("AppCoreService", "get_app_by_app_id", app_id=app_id):
            app = await app_crud.get_by_app_id(self.db, app_id)
            return AppRow.model_validate(app) if app is not None else None

    async def get_all_apps(self, include_inactive: bool = False) -> list[AppRow]:
        with create_service_span("AppCoreService", "get_all_apps"):
            apps = await app_crud.get_all(self.db, include_inactive)
            return [AppRow.model_validate(a) for a in apps]

    async def update_app(self, app_id: str, app_update: AppUpdate) -> AppRow | None:
        with create_service_span("AppCoreService", "update_app", app_id=app_id):
            app = await app_crud.get_by_app_id(self.db, app_id)
            if not app:
                return None
            for field, value in app_update.model_dump(exclude_unset=True).items():
                setattr(app, field, value)
            return AppRow.model_validate(await app_crud.update(self.db, app))

    async def regenerate_public_id(self, app_id: str) -> App | None:
        with create_service_span(
            "AppCoreService", "regenerate_public_id", app_id=app_id
        ):
            app = await app_crud.get_by_app_id(self.db, app_id)
            if not app:
                return None
            app.public_id = self.generate_public_id()
            return await app_crud.update(self.db, app)

    async def delete_app(self, app_id: str) -> bool:
        with create_service_span("AppCoreService", "delete_app", app_id=app_id):
            app = await app_crud.get_by_app_id(self.db, app_id)
            if not app:
                return False
            await app_crud.delete(self.db, app)
            return True

    async def search_apps(self, query: str) -> list[AppRow]:
        with create_service_span("AppCoreService", "search_apps"):
            if not query:
                apps = await app_crud.get_all(self.db, include_inactive=True)
            else:
                apps = await app_crud.search(self.db, query)
            return [AppRow.model_validate(a) for a in apps]

    async def get_event_counts(self, app_ids: list[str]) -> dict[str, int]:
        with create_service_span("AppCoreService", "get_event_counts"):
            return await app_crud.event_counts(self.db, app_ids)

    async def get_app_stats(self, app_id: str) -> AppStats:
        with create_service_span("AppCoreService", "get_app_stats", app_id=app_id):
            return AppStats.model_validate(await app_crud.get_stats(self.db, app_id))
