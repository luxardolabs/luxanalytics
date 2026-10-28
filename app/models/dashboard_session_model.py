"""The dashboard's session state: one row, the time of the last logout."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import Base

# The one row's key: the dashboard has one account, so one logout time revokes every session.
SESSION_STATE_ROW = 1


class DashboardSessionState(Base):
    """When the last logout was: a session that signed in before it is refused (app.core.auth).

    In the database, not Redis, so a Redis restart or a restore from an older snapshot cannot bring
    back an earlier time and with it the cookies that logout revoked."""

    __tablename__ = "dashboard_session_state"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    sessions_valid_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
