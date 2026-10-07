from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Device(Base):
    """Tracks unique devices across events. Upserted on each event ingest."""

    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    app_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Device hardware
    device_model: Mapped[str | None] = mapped_column(String)
    device_type: Mapped[str | None] = mapped_column(String)
    screen_resolution: Mapped[str | None] = mapped_column(String)

    # Software
    os_version: Mapped[str | None] = mapped_column(String)
    app_version: Mapped[str | None] = mapped_column(String)
    build_number: Mapped[str | None] = mapped_column(String)

    # Locale
    locale: Mapped[str | None] = mapped_column(String)
    timezone: Mapped[str | None] = mapped_column(String)

    # Distribution
    is_testflight: Mapped[bool | None] = mapped_column(Boolean)
    platform: Mapped[str | None] = mapped_column(String, default="ios")

    # Activity tracking
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_devices_app_last_seen", "app_id", "last_seen"),
    )

    def __repr__(self):
        return f"<Device(device_id={self.device_id[:12]}..., app_id={self.app_id})>"
