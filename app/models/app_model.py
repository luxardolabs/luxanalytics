from typing import Any

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import UUIDBaseModel


class App(UUIDBaseModel):
    """Application registration for multi-tenant event collection."""

    __tablename__ = "apps"

    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(16), unique=True, nullable=False, index=True
    )
    app_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    organization: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )

    # Free-form per-app settings: arbitrary JSON keys, so explicitly dict[str, Any].
    app_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    @property
    def dsn(self):
        """Generate the DSN for this app (Sentry-style)."""
        from app.core.config import settings

        base_url = (
            getattr(settings, "EXTERNAL_URL", None)
            or "https://analytics.luxardolabs.com"
        )
        host = base_url.replace("https://", "").replace("http://", "")
        protocol = "https" if "https" in base_url else "http"
        return f"{protocol}://{self.public_id}@{host}/api/v1/events/{self.project_id}"
