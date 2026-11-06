"""Drop five server defaults the chain set and the models do not declare.

Every write goes through the models, whose defaults run in Python: `App` by the ORM, `Event` and
`Device` by `pg_insert` on the model. So no write relies on these, and the chain now builds what the
models describe. Catalog-only: no table is rewritten.

Each ALTER needs an exclusive lock on its table, and reads of that table queue behind it while it
waits. A long reader such as a `pg_dump` would hold them all up, so the wait is bounded: past
LOCK_TIMEOUT the migration fails, changes nothing, and is safe to run again.

Downgrade puts the five defaults back.

Revision ID: 012_drop_chain_only_defaults
Revises: 011_dashboard_session_state
Create Date: 2026-11-06 17:30:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "012_drop_chain_only_defaults"
down_revision = "011_dashboard_session_state"
branch_labels = None
depends_on = None

LOCK_TIMEOUT = "5s"
DEFAULTS = (
    ("apps", "app_metadata", "'{}'::jsonb"),
    ("apps", "created_at", "now()"),
    ("apps", "updated_at", "now()"),
    ("devices", "platform", "'ios'"),
    ("events", "platform", "'ios'"),
)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    for table, column, _default in DEFAULTS:
        op.alter_column(table, column, server_default=None)


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    for table, column, default in DEFAULTS:
        op.alter_column(table, column, server_default=sa.text(default))
