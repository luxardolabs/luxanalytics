"""Alembic migration environment.

Two modes of operation:
- From app startup (init_db): receives an existing connection via config.attributes
- From CLI (alembic upgrade head): creates its own async engine
"""

import asyncio
import sys
from logging.config import fileConfig
from os.path import abspath, dirname

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Ensure app modules are importable
sys.path.insert(0, dirname(dirname(abspath(__file__))))

# Import all models so they register with Base.metadata
from app.models import App, Device, Event  # noqa: F401
from app.models.base import Base

# Alembic Config object
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _settings_url() -> str:
    """The app's own database URL — used only when no caller supplied one."""
    from app.core.config import settings

    return settings.DATABASE_URL


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without connecting."""
    # The caller's URL wins (the test harness and db-verify pass a throwaway DB); alembic.ini
    # carries none, so a bare `alembic upgrade head` falls back to the app's DATABASE_URL.
    url = config.get_main_option("sqlalchemy.url") or _settings_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Run migrations using the given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations via new async engine (CLI only)."""
    configuration = config.get_section(config.config_ini_section, {})
    # The caller's URL wins (repo.alembic_env_honors_caller_url): env.py runs AFTER the test
    # harness / db-verify set it, so the Config is consulted first and settings only as fallback.
    url = config.get_main_option("sqlalchemy.url") or _settings_url()
    configuration["sqlalchemy.url"] = url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations.

    If a connection was passed from init_db(), use it directly.
    Otherwise (CLI invocation), create a new async engine.
    """
    connectable = config.attributes.get("connection", None)

    if connectable is not None:
        do_run_migrations(connectable)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
