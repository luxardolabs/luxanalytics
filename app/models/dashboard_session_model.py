"""The dashboard's session state: one row, the current session generation."""

from sqlalchemy import BigInteger, CheckConstraint, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base_model import Base

# The one row's key: the dashboard has one account, so one logout revokes every session.
SESSION_STATE_ROW = 1


class DashboardSessionState(Base):
    """The session generation every dashboard cookie must carry (app.core.auth).

    Login copies the current generation into the cookie; logout increments it, so every cookie
    signed in before is refused. A count, not a time, so neither a clock step nor two logouts
    committing out of order can bring a revoked cookie back. In the database, not Redis, so a
    Redis restart or restore cannot either."""

    __tablename__ = "dashboard_session_state"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
