"""
Base model classes and mixins.

Provides:
- UUID primary keys
- Timestamps (created_at, updated_at)
- Soft delete support (deleted_at)
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, MetaData
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# luxarch:naming-convention asset v3 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit naming-convention`.
# Canonical fleet SQLAlchemy MetaData naming convention — emitted by `luxarch --emit naming-convention`.
#
# Constraint and index names end up IN THE DATABASE, so every repo must use the SAME convention or
# autogenerate churns and names can't be cleanly ALTER'd across repos. Required by
# `repo.metadata_naming_convention`.
#
# THE KEY DETAIL — use `column_0_N_name` (ALL columns), NOT `column_0_name` / `column_0_label` (the FIRST
# column only). Two multi-column constraints that share a leading column would otherwise generate the SAME
# name and collide the moment DDL runs (asyncpg DuplicateTableError) — silent until create_all/migrate.
# The SQLAlchemy-docs example uses the first-column-only forms; do NOT copy it (in one repo it collided
# and failed 392 tests). `repo.metadata_naming_convention` flags the first-column-only forms for this
# reason.
#
# Note: Postgres truncates identifiers at 63 characters, so `column_0_N_name` on a wide constraint
# truncates — that is expected, not a bug.
#
# Usage:
#
#     from sqlalchemy import MetaData
#     from sqlalchemy.orm import DeclarativeBase
#
#     metadata = MetaData(naming_convention=NAMING_CONVENTION)
#
#     class Base(DeclarativeBase):
#         metadata = metadata
#
# Adopting on a MATURE schema: the next `alembic revision --autogenerate` will propose renaming existing
# constraints to match the convention. Review that migration and apply it deliberately (name-only ALTERs) —
# see `luxarch --playbook alembic`.
#
# Pasted as a block (comments and the dict) below your models module's own docstring.

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        UUID: PG_UUID(as_uuid=True),
    }


class UUIDMixin:
    """Mixin that adds a UUID primary key."""

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )


class TimestampMixin:
    """Mixin that adds created_at and updated_at timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class SoftDeleteMixin:
    """Mixin that adds soft delete support."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    def soft_delete(self) -> None:
        self.deleted_at = datetime.now(UTC)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class UUIDTimestampModel(Base, UUIDMixin, TimestampMixin):
    """Base model with UUID primary key and timestamps, no soft delete.

    Use for high-volume data like events where soft delete adds overhead.
    """

    __abstract__ = True

    def to_dict(self) -> dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class UUIDBaseModel(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    """Base model with UUID primary key, timestamps, and soft delete.

    Use as the base for most entity models (apps, devices).
    """

    __abstract__ = True

    def to_dict(self) -> dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


def utc_now() -> datetime:
    return datetime.now(UTC)
