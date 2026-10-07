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

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

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
