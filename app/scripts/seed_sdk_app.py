"""Ensure the dev server has the app the Swift SDK's integration tests send to (LUXANALYTI-73).

Idempotent and dev-only: run `make seed-sdk-app`. It creates `sdk-integration` when it is missing,
reactivates it when it was deactivated, and prints its DSN on stdout, for the owner to store as a
LuxPM credential. Never logged, never committed.
"""

import asyncio
import sys
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import get_db_context
from app.schemas.app_schema import AppCreate, AppRow, AppUpdate
from app.services.core.app_core_service import AppCoreService

SDK_APP_ID = "sdk-integration"


class NotADevEnvironment(RuntimeError):
    """The seed was asked to run outside dev/test."""


async def ensure_sdk_app(db: AsyncSession) -> AppRow:
    """The `sdk-integration` app, created or reactivated as needed; ids stable across runs."""
    # A test app must never be minted on prod.
    if settings.is_production:
        raise NotADevEnvironment(
            f"refusing to seed {SDK_APP_ID!r} in ENVIRONMENT={settings.ENVIRONMENT!r}"
        )
    core = AppCoreService(db)
    app = await core.get_app_by_app_id(SDK_APP_ID)
    if app is None:
        return await core.create_app(
            AppCreate(
                app_id=SDK_APP_ID,
                name="SDK integration tests",
                organization="Luxardo Labs",
                description="Swift SDK integration tests against dev; disposable data.",
            )
        )
    if not app.is_active:
        updated = await core.update_app(SDK_APP_ID, AppUpdate(is_active=True))
        if updated is not None:
            return updated
    return app


def dsn_for(app: AppRow, external_url: str) -> str:
    """https://PUBLIC_ID@host[:port]/api/v1/events/PROJECT_ID on the public base URL."""
    base = urlsplit(external_url)
    return (
        f"{base.scheme}://{app.public_id}@{base.netloc}/api/v1/events/{app.project_id}"
    )


async def main() -> int:
    if not settings.EXTERNAL_URL:
        sys.stderr.write("EXTERNAL_URL is not set; the DSN would have no host\n")
        return 2
    try:
        async with get_db_context() as db:
            app = await ensure_sdk_app(db)
    except NotADevEnvironment as e:
        sys.stderr.write(f"{e}\n")
        return 2
    sys.stdout.write(dsn_for(app, settings.EXTERNAL_URL) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
