from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import Base, UUIDMixin


class Event(Base, UUIDMixin):
    __tablename__ = "events"

    # Event identity
    app_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Timestamps
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Actor
    user_id: Mapped[str | None] = mapped_column(String, index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    device_id: Mapped[str | None] = mapped_column(String, index=True)

    # Promoted device context (queried frequently via GROUP BY)
    device_model: Mapped[str | None] = mapped_column(String)
    os_version: Mapped[str | None] = mapped_column(String)
    app_version: Mapped[str | None] = mapped_column(String)
    platform: Mapped[str | None] = mapped_column(String, default="ios")

    # Flexible event-specific data (JSONB with GIN index for key queries)
    properties: Mapped[dict | None] = mapped_column(JSONB)

    # Legacy column — kept during dual-write transition, will be dropped in Phase 6
    event_metadata: Mapped[dict | None] = mapped_column("event_metadata", JSONB)

    __table_args__ = (
        # Primary query patterns
        Index("ix_events_app_name_received", "app_id", "name", "received_at"),
        Index("ix_events_app_device_received", "app_id", "device_id", "received_at"),
        Index("ix_events_app_user_received", "app_id", "user_id", "received_at"),
        Index("ix_events_app_session_received", "app_id", "session_id", "received_at"),
        # GIN index on properties for JSONB key/value queries
        Index("ix_events_properties_gin", "properties", postgresql_using="gin"),
        # Analytics query indexes (partial — only relevant rows indexed)
        Index(
            "ix_events_perf_operation",
            text("(properties->>'operation')"),
            postgresql_where=text(
                "name = 'performance_measured' AND properties IS NOT NULL"
            ),
        ),
        Index(
            "ix_events_screen_session",
            "session_id",
            "received_at",
            postgresql_where=text(
                "name = 'screen_viewed' AND properties->>'screen' IS NOT NULL"
            ),
        ),
        Index(
            "ix_events_user_received",
            "user_id",
            text("received_at DESC"),
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "ix_events_session_received",
            "session_id",
            "received_at",
            postgresql_where=text("session_id IS NOT NULL"),
        ),
    )

    def __repr__(self):
        return f"<Event(id={self.id}, app_id={self.app_id}, name={self.name})>"
